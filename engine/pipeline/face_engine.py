"""
Peek Face Engine - Core Pipeline
Coordinates Camera, Detector, Aligner, Embedder, Matcher, and Secure Profile Storage.

NON-NEGOTIABLE SECURITY CONSTRAINTS:
1. Face match alone can never unlock. Liveness is a mandatory independent gate.
2. Raw camera frames are processed in-memory and discarded immediately; only embeddings are stored.
3. Fail open, never fail closed.
"""

import os
import cv2
import time
import numpy as np
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from engine.detection.scrfd_detector import SCRFDDetector, FaceDetection
from engine.alignment.aligner import FaceAligner
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.recognition.matcher import FaceMatcher, MatchResult
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate

logger = logging.getLogger("Peek.FaceEngine")

@dataclass
class EngineFrameResult:
    has_face: bool
    detection: Optional[FaceDetection] = None
    embedding: Optional[np.ndarray] = None
    match_result: Optional[MatchResult] = None
    state_label: str = "SEARCHING"
    # SECURITY FLAG: Enforces that even a 100% biometric match cannot unlock without liveness
    is_authorized_to_unlock: bool = False
    details: str = ""


class FaceEngine:
    """
    Unified Face Recognition Engine for Peek.
    """

    def __init__(
        self,
        detector_model_path: Optional[str] = None,
        embedder_model_path: Optional[str] = None,
        profile_store: Optional[SecureProfileStore] = None,
        match_threshold: float = 0.50
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

        self.aligner = FaceAligner(target_size=(112, 112))
        self.matcher = FaceMatcher(default_threshold=match_threshold)
        self.profile_store = profile_store or SecureProfileStore()
        
        # In-memory enrolled templates cache for fast real-time matching
        self.active_profile: Optional[PeekProfile] = None
        self.cached_template_embeddings: List[np.ndarray] = []
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
            logger.info(f"Active profile loaded: '{self.active_profile.display_name}' with {len(self.cached_template_embeddings)} templates.")
        else:
            self.active_profile = None
            self.cached_template_embeddings = []
            logger.info("No active profile enrolled.")

    def set_active_profile(self, profile: PeekProfile):
        """Sets active profile in memory."""
        self.active_profile = profile
        self.cached_template_embeddings = [
            np.array(t.embedding, dtype=np.float32) for t in profile.templates
        ]

    def process_frame(self, frame: np.ndarray) -> EngineFrameResult:
        """
        Executes the detection -> alignment -> embedding -> matching pipeline on a single frame.
        
        SECURITY GUARANTEE:
        Aligned crops are processed in memory and discarded. No raw camera frames persist.
        Match alone NEVER sets is_authorized_to_unlock to True.
        """
        detections = self.detector.detect(frame)
        
        if not detections:
            return EngineFrameResult(
                has_face=False,
                state_label="SEARCHING",
                is_authorized_to_unlock=False,
                details="Looking for face..."
            )

        # Dominant face selection: largest face in frame
        dominant_face = detections[0]
        
        # Face quality sanity check: minimum size 40x40
        if dominant_face.width < 40 or dominant_face.height < 40:
            return EngineFrameResult(
                has_face=True,
                detection=dominant_face,
                state_label="FACE_TOO_SMALL",
                is_authorized_to_unlock=False,
                details="Move closer to camera"
            )

        # Step 2: 5-point alignment to 112x112 canonical ArcFace geometry
        aligned_face = self.aligner.align(frame, dominant_face.landmarks)

        # Step 3: Extract 512-D L2-normalized ArcFace embedding
        embedding = self.embedder.extract_embedding(aligned_face)

        # SECURITY CONSTRAINT #2: Aligned frame is immediately deleted from memory
        del aligned_face

        # Step 4: Biometric template matching (if active profile exists)
        if not self.cached_template_embeddings:
            return EngineFrameResult(
                has_face=True,
                detection=dominant_face,
                embedding=embedding,
                state_label="FACE_FOUND",
                is_authorized_to_unlock=False,
                details="Face detected (No enrolled profile)"
            )

        match_result = self.matcher.match(
            live_embedding=embedding,
            enrolled_templates=self.cached_template_embeddings,
            threshold=self.active_profile.matching_threshold if self.active_profile else None
        )

        state_label = "MATCH" if match_result.is_match else "NO_MATCH"
        
        # SECURITY CONSTRAINT #1:
        # Match decision alone is NEVER authorized to unlock.
        # Layer C (Liveness) must independently pass in later phases.
        return EngineFrameResult(
            has_face=True,
            detection=dominant_face,
            embedding=embedding,
            match_result=match_result,
            state_label=state_label,
            is_authorized_to_unlock=False,  # Strictly False without independent liveness
            details=match_result.details
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
        if det.confidence < 0.70:
            return False, None, f"Detection confidence too low ({det.confidence:.2f})."
        if det.width < 60 or det.height < 60:
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
