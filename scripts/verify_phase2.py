"""
Peek — Phase 2 Verification Script
Validates 9-direction pose classification, quality gating, multi-frame candidate selection,
and DPAPI multi-template profile generation.
"""

import os
import sys
import numpy as np
import cv2
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.pose.pose_estimator import HeadPose, HeadPoseEstimator, ORDERED_ENROLLMENT_POSES
from engine.quality.quality_checker import FaceQualityChecker
from engine.detection.scrfd_detector import FaceDetection
from engine.alignment.aligner import FaceAligner, ARCFACE_CANONICAL_5PTS
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.recognition.matcher import FaceMatcher
from engine.enrollment.enrollment_session import EnrollmentSession, EnrollmentState
from storage.template_store import SecureProfileStore

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.VerifyPhase2")


def create_pose_landmarks(base_pts: np.ndarray, pose: HeadPose) -> np.ndarray:
    """Generates synthetic landmarks shifted to simulate specific head directions."""
    pts = base_pts.copy()
    shift_map = {
        HeadPose.CENTER: (0.0, 0.0),
        HeadPose.LEFT: (-10.0, 0.0),
        HeadPose.RIGHT: (10.0, 0.0),
        HeadPose.UP: (0.0, -8.0),
        HeadPose.DOWN: (0.0, 8.0),
        HeadPose.UPPER_LEFT: (-8.0, -6.0),
        HeadPose.UPPER_RIGHT: (8.0, -6.0),
        HeadPose.LOWER_LEFT: (-8.0, 6.0),
        HeadPose.LOWER_RIGHT: (8.0, 6.0),
    }
    dx, dy = shift_map.get(pose, (0.0, 0.0))
    pts[2, 0] += dx
    pts[2, 1] += dy
    return pts


def run_verification():
    logger.info("==================================================")
    logger.info("   PEEK — PHASE 2 GUIDED ENROLLMENT VERIFICATION  ")
    logger.info("==================================================")

    # 1. Pose Estimator Verification
    logger.info("1. Verifying 9-direction head pose classification...")
    estimator = HeadPoseEstimator()
    for expected_pose in ORDERED_ENROLLMENT_POSES:
        pts = create_pose_landmarks(ARCFACE_CANONICAL_5PTS, expected_pose)
        estimate = estimator.estimate(pts)
        logger.info(f"   Target: {expected_pose.value:12s} -> Classified: {estimate.pose.value:12s} (yaw: {estimate.yaw:+.2f}, pitch: {estimate.pitch:+.2f})")
        assert estimate.pose == expected_pose, f"Pose mismatch: expected {expected_pose}, got {estimate.pose}"
    logger.info("✓ All 9 head poses accurately classified.")

    # 2. Quality Checker Verification
    logger.info("2. Verifying frame quality gating rules...")
    checker = FaceQualityChecker(min_sharpness=60.0, min_brightness=40.0, min_face_size=80)
    dummy_frame = np.full((480, 640, 3), 150, dtype=np.uint8)

    # Multi-face test
    d1 = FaceDetection(bbox=np.array([50, 50, 160, 160]), confidence=0.95, landmarks=np.zeros((5,2)))
    d2 = FaceDetection(bbox=np.array([200, 50, 310, 160]), confidence=0.95, landmarks=np.zeros((5,2)))
    res_multi = checker.evaluate(dummy_frame, [d1, d2])
    assert not res_multi.passed and "Multiple faces" in res_multi.rejection_reason
    logger.info("✓ Rejection: Multiple faces correctly blocked.")

    # Face too small test
    d_small = FaceDetection(bbox=np.array([50, 50, 100, 100]), confidence=0.95, landmarks=np.zeros((5,2)))
    res_small = checker.evaluate(dummy_frame, [d_small])
    assert not res_small.passed and "too far" in res_small.rejection_reason
    logger.info("✓ Rejection: Small/distant faces blocked.")

    # 3. Simulated 9-Direction Enrollment Session
    logger.info("3. Running simulated 9-direction enrollment session...")
    models_dir = os.path.join(PROJECT_ROOT, "engine", "models")
    det_path = os.path.join(models_dir, "scrfd_500m_bnkps.onnx")
    emb_path = os.path.join(models_dir, "w600k_mbf.onnx")

    aligner = FaceAligner()
    embedder = ArcFaceEmbedder(emb_path)
    store = SecureProfileStore()

    # Create mock detector feeding correct pose landmarks per step
    class ScriptedDetector:
        def __init__(self):
            self.current_landmarks = ARCFACE_CANONICAL_5PTS.copy()
        def set_pose(self, pose):
            # In mirrored view, LEFT requires turning nose to user's left
            shift_pts = create_pose_landmarks(ARCFACE_CANONICAL_5PTS, pose)
            # Adapt coordinates to image scale (center face at 320, 240)
            self.current_landmarks = shift_pts * 2.5 + np.array([200.0, 120.0])
        def detect(self, frame):
            bbox = np.array([180.0, 100.0, 460.0, 380.0])
            return [FaceDetection(bbox=bbox, confidence=0.98, landmarks=self.current_landmarks)]

    scripted_det = ScriptedDetector()
    session = EnrollmentSession(
        detector=scripted_det,
        aligner=aligner,
        embedder=embedder,
        profile_store=store,
        candidates_per_pose=3,
        keep_best_per_pose=2
    )

    session.start()
    test_face_frame = np.full((480, 640, 3), 160, dtype=np.uint8)
    # Add high contrast texture to pass blur/sharpness checks
    test_face_frame[120:360, 200:440] = np.random.randint(60, 220, (240, 240, 3), dtype=np.uint8)

    for step_idx, pose in enumerate(ORDERED_ENROLLMENT_POSES):
        scripted_det.set_pose(pose)
        logger.info(f"   Guiding Step {step_idx+1}/9: {pose.value}...")
        
        # Feed frames until pose completes
        for _ in range(3):
            session.pose_transition_cooldown = 0.0
            progress = session.process_frame(test_face_frame, mirrored=False)

        logger.info(f"   ✓ Pose {pose.value} captured. Progress: {progress.progress_percentage:.1f}%")

    assert session.is_complete, "Enrollment session did not complete all 9 poses!"
    logger.info("✓ 9-Direction enrollment session completed successfully.")

    # 4. Profile Finalization and DPAPI Encryption
    logger.info("4. Finalizing and saving DPAPI-encrypted profile...")
    profile = session.finalize_and_save_profile(
        display_name="Phase 2 Multi-Angle User",
        profile_id="verify_phase2_profile"
    )

    logger.info(f"   Profile ID: {profile.profile_id}")
    logger.info(f"   Templates stored: {len(profile.templates)} (2 per pose across 9 directions = 18 total)")
    assert len(profile.templates) == 18, f"Expected 18 templates, got {len(profile.templates)}"

    # Check DPAPI encrypted on disk
    path = store._get_profile_path("verify_phase2_profile")
    with open(path, "rb") as f:
        data = f.read()
    assert b"Phase 2 Multi-Angle User" not in data, "Biometric profile is unencrypted!"
    logger.info("✓ Verified DPAPI encryption on disk.")

    # Verify reload
    loaded = store.load_profile("verify_phase2_profile")
    assert loaded is not None
    assert len(loaded.templates) == 18
    logger.info("✓ Verified DPAPI profile decryption & integrity.")

    # 5. Multi-Angle Template Matching Test
    logger.info("5. Validating multi-angle matching against enrolled profile...")
    matcher = FaceMatcher(default_threshold=0.50)
    enrolled_embeddings = [np.array(t.embedding, dtype=np.float32) for t in loaded.templates]

    # Test matching with one of the enrolled vectors
    query_vector = enrolled_embeddings[0]
    match_res = matcher.match(query_vector, enrolled_embeddings)
    logger.info(f"   Multi-angle match confidence: {match_res.confidence:.4f} (is_match={match_res.is_match})")
    assert match_res.is_match is True
    assert match_res.is_authorized_to_unlock is False, "Security constraint #1 violated!"
    logger.info("✓ Security constraint #1 verified: Match alone cannot authorize unlock.")

    # Cleanup test profile
    store.delete_profile("verify_phase2_profile")
    logger.info("Cleaned up verification profile.")

    logger.info("==================================================")
    logger.info("   ALL PHASE 2 VERIFICATIONS PASSED SUCCESSFULLY! ")
    logger.info("==================================================")


if __name__ == "__main__":
    run_verification()
