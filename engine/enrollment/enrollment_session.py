"""
Peek Face Engine - Guided Enrollment Session
Layer D: State-driven 9-direction guided enrollment with multi-frame candidate selection.

SECURITY CONSTRAINTS:
1. Multi-frame candidate selection runs in memory; camera frames are discarded immediately.
2. Only 512-D float vectors and minimal quality metrics are persisted.
3. Templates are encrypted via Windows DPAPI before touching disk.
"""

import time
import numpy as np
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

from engine.detection.scrfd_detector import SCRFDDetector, FaceDetection
from engine.alignment.aligner import FaceAligner
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.pose.pose_estimator import HeadPose, HeadPoseEstimator, ORDERED_ENROLLMENT_POSES
from engine.quality.quality_checker import FaceQualityChecker, QualityCheckResult
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate

logger = logging.getLogger("Peek.EnrollmentSession")

class EnrollmentState(str, Enum):
    IDLE = "IDLE"
    GUIDING_TO_POSE = "GUIDING_TO_POSE"
    ACCUMULATING_CANDIDATES = "ACCUMULATING_CANDIDATES"
    POSE_COMPLETED = "POSE_COMPLETED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

@dataclass
class PoseCandidate:
    embedding: np.ndarray  # (512,)
    quality_score: float
    pose_score: float
    composite_score: float


@dataclass
class EnrollmentProgress:
    current_pose: HeadPose
    pose_index: int              # 0 to 8
    total_poses: int             # 9
    state: EnrollmentState
    candidates_collected: int
    target_candidates: int
    guidance_message: str
    is_complete: bool
    quality_passed: bool
    pose_matched: bool
    progress_percentage: float   # 0.0 to 100.0


class EnrollmentSession:
    """
    Orchestrates the 9-direction facial enrollment flow.
    """

    def __init__(
        self,
        detector: SCRFDDetector,
        aligner: FaceAligner,
        embedder: ArcFaceEmbedder,
        profile_store: Optional[SecureProfileStore] = None,
        candidates_per_pose: int = 2,
        keep_best_per_pose: int = 1
    ):
        self.detector = detector
        self.aligner = aligner
        self.embedder = embedder
        self.profile_store = profile_store or SecureProfileStore()

        self.pose_estimator = HeadPoseEstimator()
        self.quality_checker = FaceQualityChecker()

        self.candidates_per_pose = candidates_per_pose
        self.keep_best_per_pose = keep_best_per_pose
        self.poses = list(ORDERED_ENROLLMENT_POSES)

        self.current_pose_idx = 0
        self.state = EnrollmentState.IDLE
        self.candidate_buffer: List[PoseCandidate] = []
        self.final_templates: List[PeekTemplate] = []
        self.pose_transition_cooldown = 0.0

    def start(self):
        """Starts or restarts the 9-direction enrollment process."""
        self.current_pose_idx = 0
        self.state = EnrollmentState.GUIDING_TO_POSE
        self.candidate_buffer.clear()
        self.final_templates.clear()
        self.pose_transition_cooldown = time.time() + 0.5
        logger.info("Enrollment session started. Target pose: %s", self.current_pose.value)

    @property
    def current_pose(self) -> HeadPose:
        if self.current_pose_idx < len(self.poses):
            return self.poses[self.current_pose_idx]
        return HeadPose.CENTER

    @property
    def is_complete(self) -> bool:
        return self.state == EnrollmentState.COMPLETED

    def process_frame(self, frame: np.ndarray, mirrored: bool = False) -> EnrollmentProgress:
        """
        Processes a single camera frame during enrollment.
        Evaluates face presence, quality, pose alignment, and accumulates candidates.
        """
        if self.state in (EnrollmentState.IDLE, EnrollmentState.COMPLETED):
            return self._build_progress(
                guidance="Enrollment complete." if self.is_complete else "Session idle.",
                quality_passed=False,
                pose_matched=False
            )

        # Handle brief inter-pose transition cooldown
        if time.time() < self.pose_transition_cooldown:
            return self._build_progress(
                guidance=f"Get ready for next pose: {self.current_pose.value}",
                quality_passed=True,
                pose_matched=False
            )

        # 1. Detect faces
        detections = self.detector.detect(frame)

        # 2. Quality gating
        q_result = self.quality_checker.evaluate(frame, detections)
        if not q_result.passed:
            return self._build_progress(
                guidance=q_result.rejection_reason or "Adjust position",
                quality_passed=False,
                pose_matched=False
            )

        # 3. Pose alignment check
        det = detections[0]
        pose_match, pose_score, pose_guidance = self.pose_estimator.evaluate_against_target(
            det.landmarks,
            self.current_pose,
            mirrored=mirrored
        )

        if not pose_match:
            return self._build_progress(
                guidance=pose_guidance,
                quality_passed=True,
                pose_matched=False
            )

        # 4. Face passes quality & pose target -> Extract embedding candidate
        aligned_face = self.aligner.align(frame, det.landmarks)
        embedding = self.embedder.extract_embedding(aligned_face)
        del aligned_face  # SECURITY: Immediate discard of pixel buffer

        composite_score = 0.5 * q_result.composite_score + 0.5 * pose_score
        self.candidate_buffer.append(PoseCandidate(
            embedding=embedding,
            quality_score=q_result.composite_score,
            pose_score=pose_score,
            composite_score=composite_score
        ))

        self.state = EnrollmentState.ACCUMULATING_CANDIDATES

        # 5. Check if candidate buffer for current pose is complete
        if len(self.candidate_buffer) >= self.candidates_per_pose:
            self._finalize_current_pose()

        guidance = "Hold pose steady..." if self.state != EnrollmentState.COMPLETED else "Enrollment finished!"
        return self._build_progress(
            guidance=guidance,
            quality_passed=True,
            pose_matched=True
        )

    def _finalize_current_pose(self):
        """Sorts candidates, preserves top M embeddings, and transitions to next pose."""
        self.candidate_buffer.sort(key=lambda c: c.composite_score, reverse=True)
        selected = self.candidate_buffer[:self.keep_best_per_pose]

        for i, cand in enumerate(selected):
            tmpl = PeekTemplate(
                template_id=f"tmpl_{self.current_pose.value.lower()}_{i+1}_{int(time.time()*1000)}",
                embedding=cand.embedding.tolist(),
                pose_label=self.current_pose.value,
                quality_score=cand.composite_score,
                captured_at=time.time()
            )
            self.final_templates.append(tmpl)

        logger.info(
            "Pose %s finalized with %d templates (best quality: %.3f)",
            self.current_pose.value,
            len(selected),
            selected[0].composite_score if selected else 0.0
        )

        self.candidate_buffer.clear()
        self.current_pose_idx += 1

        if self.current_pose_idx >= len(self.poses):
            self.state = EnrollmentState.COMPLETED
            logger.info("All 9 enrollment poses successfully captured! Total templates: %d", len(self.final_templates))
        else:
            self.state = EnrollmentState.GUIDING_TO_POSE
            self.pose_transition_cooldown = time.time() + 0.3  # 300ms to orient to next direction

    def _build_progress(
        self,
        guidance: str,
        quality_passed: bool,
        pose_matched: bool
    ) -> EnrollmentProgress:
        total = len(self.poses)
        idx = min(self.current_pose_idx, total - 1)
        
        # Overall percentage calculation
        pose_weight = 100.0 / total
        pose_fraction = (len(self.candidate_buffer) / float(self.candidates_per_pose)) * pose_weight
        pct = min(100.0, self.current_pose_idx * pose_weight + pose_fraction)
        if self.is_complete:
            pct = 100.0

        return EnrollmentProgress(
            current_pose=self.current_pose,
            pose_index=idx,
            total_poses=total,
            state=self.state,
            candidates_collected=len(self.candidate_buffer),
            target_candidates=self.candidates_per_pose,
            guidance_message=guidance,
            is_complete=self.is_complete,
            quality_passed=quality_passed,
            pose_matched=pose_matched,
            progress_percentage=pct
        )

    def finalize_and_save_profile(
        self,
        display_name: str = "Primary User",
        profile_id: Optional[str] = None
    ) -> PeekProfile:
        """
        Creates and encrypts the completed PeekProfile with Windows DPAPI.
        """
        if not self.final_templates:
            raise ValueError("No templates enrolled to create a profile.")

        pid = profile_id or f"profile_{int(time.time())}"
        profile = PeekProfile(
            profile_id=pid,
            display_name=display_name,
            matching_threshold=0.50,
            templates=self.final_templates,
            enabled=True
        )

        self.profile_store.save_profile(profile)
        logger.info("Encrypted and saved multi-pose profile '%s' with %d templates.", display_name, len(profile.templates))
        return profile
