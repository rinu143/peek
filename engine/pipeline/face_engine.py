"""
Peek Face Engine - Core Pipeline
Coordinates Camera, Detector, Tracker, Pose Estimator, Aligner, Embedder, Matcher,
Temporal Verifier, and Secure Profile Storage.

NON-NEGOTIABLE SECURITY CONSTRAINTS:
1. Face match alone can never unlock. Even when temporal verification passes 100%,
   is_authorized_to_unlock remains strictly False. Liveness is an independent gate.
2. Raw camera frames are processed in-memory and discarded immediately; only embeddings are stored.
3. Fail open, never fail closed.
"""

import os
import cv2
import time
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from engine.detection.scrfd_detector import SCRFDDetector, FaceDetection
from engine.tracking.face_tracker import FaceTracker, TrackedFace
from engine.pose.pose_estimator import HeadPoseEstimator
from engine.alignment.aligner import FaceAligner
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.recognition.matcher import FaceMatcher, MatchResult
from engine.temporal.temporal_verifier import TemporalVerifier, TemporalVerificationResult
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate

logger = logging.getLogger("Peek.FaceEngine")

@dataclass
class EngineFrameResult:
    has_face: bool
    detection: Optional[FaceDetection] = None
    track_id: Optional[int] = None
    all_tracks: List[TrackedFace] = field(default_factory=list)
    embedding: Optional[np.ndarray] = None
    match_result: Optional[MatchResult] = None
    temporal_result: Optional[TemporalVerificationResult] = None
    is_temporally_confirmed: bool = False
    estimated_pose: Optional[str] = None
    state_label: str = "SEARCHING"
    # SECURITY FLAG: Enforces that even 100% biometric match cannot unlock without liveness
    is_authorized_to_unlock: bool = False
    details: str = ""


class FaceEngine:
    """
    Unified Face Recognition Engine for Peek with persistent tracking & temporal verification.
    """

    def __init__(
        self,
        detector_model_path: Optional[str] = None,
        embedder_model_path: Optional[str] = None,
        profile_store: Optional[SecureProfileStore] = None,
        match_threshold: float = 0.48,
        temporal_window_size: int = 7,
        temporal_min_matches: int = 5
    ):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, "models")
        
        det_path = detector_model_path or os.path.join(models_dir, "scrfd_500m_bnkps.onnx")
        emb_path = embedder_model_path or os.path.join(models_dir, "w600k_mbf.onnx")

        if not os.path.exists(det_path) or not os.path.exists(emb_path):
            from engine.models.download_models import ensure_models_exist
            logger.info("ONNX models missing; triggering automated download...")
            det_path, emb_path = ensure_models_exist()

        logger.info(f"Loading SCRFD Detector from: {det_path}")
        self.detector = SCRFDDetector(det_path, conf_threshold=0.5, nms_threshold=0.4)

        logger.info(f"Loading ArcFace Embedder from: {emb_path}")
        self.embedder = ArcFaceEmbedder(emb_path)

        self.tracker = FaceTracker(min_hits_to_confirm=2, max_missing_frames=5)
        self.pose_estimator = HeadPoseEstimator()
        self.aligner = FaceAligner(target_size=(112, 112))
        self.matcher = FaceMatcher(default_threshold=match_threshold)
        self.temporal_verifier = TemporalVerifier(
            window_size=temporal_window_size,
            min_matches_required=temporal_min_matches
        )
        self.profile_store = profile_store or SecureProfileStore()
        
        # In-memory enrolled templates cache for fast real-time matching
        self.active_profile: Optional[PeekProfile] = None
        self.cached_template_embeddings: List[np.ndarray] = []
        self.cached_template_poses: List[str] = []
        self.reload_active_profile()

    def reload_active_profile(self):
        """Loads enabled profiles from DPAPI storage and caches template embeddings."""
        profiles = self.profile_store.list_profiles()
        enabled_profiles = [p for p in profiles if p.enabled]
        
        if enabled_profiles:
            self.active_profile = enabled_profiles[0]
            self.cached_template_embeddings = [
                np.array(t.embedding, dtype=np.float32) for t in self.active_profile.templates
            ]
            self.cached_template_poses = [t.pose_label for t in self.active_profile.templates]
            logger.info(f"Active profile loaded: '{self.active_profile.display_name}' with {len(self.cached_template_embeddings)} templates.")
        else:
            self.active_profile = None
            self.cached_template_embeddings = []
            self.cached_template_poses = []
            logger.info("No active profile enrolled.")
        
        self.temporal_verifier.reset()

    def set_active_profile(self, profile: PeekProfile):
        """Sets active profile in memory."""
        self.active_profile = profile
        self.cached_template_embeddings = [
            np.array(t.embedding, dtype=np.float32) for t in profile.templates
        ]
        self.cached_template_poses = [t.pose_label for t in profile.templates]
        self.temporal_verifier.reset()

    def process_frame(self, frame: np.ndarray) -> EngineFrameResult:
        """
        Executes the detection -> tracking -> pose -> alignment -> embedding -> matching -> temporal pipeline.
        
        SECURITY GUARANTEE:
        Aligned crops are processed in memory and discarded. No raw camera frames persist.
        Match alone NEVER sets is_authorized_to_unlock to True.
        """
        # Step 1: Detect all faces in frame
        detections = self.detector.detect(frame)

        # Step 2: Track faces and resolve dominant target face
        dominant_target, all_tracks = self.tracker.update(detections)
        
        if dominant_target is None:
            temporal_res = self.temporal_verifier.update(None, False, 0.0)
            return EngineFrameResult(
                has_face=False,
                all_tracks=all_tracks,
                temporal_result=temporal_res,
                state_label="SEARCHING",
                is_authorized_to_unlock=False,
                details="Looking for you..."
            )

        # Re-pack dominant target as FaceDetection for downstream consumers
        dominant_detection = FaceDetection(
            bbox=dominant_target.bbox,
            confidence=dominant_target.confidence,
            landmarks=dominant_target.landmarks
        )

        # Face size check
        if dominant_target.width < 45 or dominant_target.height < 45:
            temporal_res = self.temporal_verifier.update(dominant_target.track_id, False, 0.0)
            return EngineFrameResult(
                has_face=True,
                detection=dominant_detection,
                track_id=dominant_target.track_id,
                all_tracks=all_tracks,
                temporal_result=temporal_res,
                state_label="FACE_TOO_SMALL",
                is_authorized_to_unlock=False,
                details="Move closer to camera"
            )

        # Step 3: Estimate head pose
        pose_est = self.pose_estimator.estimate(dominant_target.landmarks)
        pose_label = pose_est.pose.value

        # Step 4: 5-point alignment to 112x112 canonical ArcFace geometry
        aligned_face = self.aligner.align(frame, dominant_target.landmarks)

        # Step 5: Extract 512-D L2-normalized ArcFace embedding
        embedding = self.embedder.extract_embedding(aligned_face)

        # SECURITY CONSTRAINT #2: Aligned frame buffer is immediately deleted from memory
        del aligned_face

        # Step 6: Biometric template matching (if active profile exists)
        if not self.cached_template_embeddings:
            temporal_res = self.temporal_verifier.update(dominant_target.track_id, False, 0.0)
            return EngineFrameResult(
                has_face=True,
                detection=dominant_detection,
                track_id=dominant_target.track_id,
                all_tracks=all_tracks,
                embedding=embedding,
                temporal_result=temporal_res,
                estimated_pose=pose_label,
                state_label="FACE_FOUND",
                is_authorized_to_unlock=False,
                details="Face detected (No enrolled profile)"
            )

        match_result = self.matcher.match(
            live_embedding=embedding,
            enrolled_templates=self.cached_template_embeddings,
            template_poses=self.cached_template_poses,
            estimated_pose=pose_label,
            threshold=self.active_profile.matching_threshold if self.active_profile else None
        )

        # Step 7: Rolling temporal verification confirmation (M-of-N sliding window)
        temporal_res = self.temporal_verifier.update(
            track_id=dominant_target.track_id,
            is_frame_match=match_result.is_match,
            frame_confidence=match_result.confidence
        )

        # Determine state label based on rolling confirmation
        if temporal_res.is_temporally_confirmed:
            state_label = "MATCH"
            details = f"Verified: {temporal_res.details}"
        elif match_result.is_match:
            state_label = "VERIFYING"
            details = f"Verifying ({temporal_res.positive_matches_in_window}/{temporal_res.required_matches})..."
        else:
            state_label = "NO_MATCH"
            details = match_result.details

        # If multiple faces present in frame, append bystander notice
        if len(all_tracks) > 1:
            details += f" ({len(all_tracks)} faces tracked)"

        # SECURITY CONSTRAINT #1:
        # Biometric matching (even temporally confirmed) alone is NEVER authorized to unlock.
        # Layer C (Liveness) must independently pass in Phase 4.
        return EngineFrameResult(
            has_face=True,
            detection=dominant_detection,
            track_id=dominant_target.track_id,
            all_tracks=all_tracks,
            embedding=embedding,
            match_result=match_result,
            temporal_result=temporal_res,
            is_temporally_confirmed=temporal_res.is_temporally_confirmed,
            estimated_pose=pose_label,
            state_label=state_label,
            is_authorized_to_unlock=False,  # Strictly False without independent liveness
            details=details
        )

    def enroll_from_frame(
        self,
        frame: np.ndarray,
        pose_label: str = "CENTER"
    ) -> Tuple[bool, Optional[PeekTemplate], str]:
        """
        Enrolls a single template from the given camera frame.
        Rejects frames with multiple faces, small faces, or poor confidence.
        """
        detections = self.detector.detect(frame)
        if not detections:
            return False, None, "No face detected in frame."
        if len(detections) > 1:
            return False, None, "Multiple faces detected. Single face required for enrollment."

        det = detections[0]
        if det.confidence < 0.65:
            return False, None, f"Detection confidence too low ({det.confidence:.2f})."
        if det.width < 50 or det.height < 50:
            return False, None, "Face too small in frame. Move closer."

        aligned_face = self.aligner.align(frame, det.landmarks)
        embedding = self.embedder.extract_embedding(aligned_face)
        del aligned_face  # Discard immediately

        template = PeekTemplate(
            template_id=f"tmpl_{int(time.time()*1000)}",
            embedding=embedding.tolist(),
            pose_label=pose_label,
            quality_score=float(det.confidence)
        )
        return True, template, "Enrollment sample captured successfully."
