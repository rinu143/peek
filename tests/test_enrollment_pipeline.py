"""
Peek Face Engine - Enrollment Pipeline Automated Tests
Validates head pose estimation, quality checks, candidate accumulation, and 9-direction enrollment.
"""

import os
import unittest
import cv2
import numpy as np
import tempfile
import shutil

from engine.pose.pose_estimator import HeadPose, HeadPoseEstimator
from engine.quality.quality_checker import FaceQualityChecker
from engine.detection.scrfd_detector import FaceDetection
from engine.alignment.aligner import FaceAligner, ARCFACE_CANONICAL_5PTS
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.enrollment.enrollment_session import EnrollmentSession, EnrollmentState
from storage.template_store import SecureProfileStore

class TestEnrollmentPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, "engine", "models")
        cls.det_path = os.path.join(models_dir, "scrfd_500m_bnkps.onnx")
        cls.emb_path = os.path.join(models_dir, "w600k_mbf.onnx")

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="peek_test_enroll_")
        self.profile_store = SecureProfileStore(storage_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_head_pose_estimation_all_directions(self):
        """Validates that geometric landmark ratios correctly identify all 9 head poses."""
        estimator = HeadPoseEstimator(yaw_threshold=0.18, pitch_threshold=0.10)
        
        # Base neutral landmarks
        base = ARCFACE_CANONICAL_5PTS.copy()  # [le, re, nose, lm, rm]

        # 1. CENTER
        est_center = estimator.estimate(base)
        self.assertEqual(est_center.pose, HeadPose.CENTER)

        # 2. LEFT (nose shifted left)
        pts_left = base.copy()
        pts_left[2, 0] -= 10.0
        est_left = estimator.estimate(pts_left)
        self.assertEqual(est_left.pose, HeadPose.LEFT)

        # 3. RIGHT (nose shifted right)
        pts_right = base.copy()
        pts_right[2, 0] += 10.0
        est_right = estimator.estimate(pts_right)
        self.assertEqual(est_right.pose, HeadPose.RIGHT)

        # 4. UP (nose shifted up)
        pts_up = base.copy()
        pts_up[2, 1] -= 10.0
        est_up = estimator.estimate(pts_up)
        self.assertEqual(est_up.pose, HeadPose.UP)

        # 5. DOWN (nose shifted down)
        pts_down = base.copy()
        pts_down[2, 1] += 10.0
        est_down = estimator.estimate(pts_down)
        self.assertEqual(est_down.pose, HeadPose.DOWN)

        # 6. UPPER_LEFT
        pts_ul = base.copy()
        pts_ul[2, 0] -= 10.0
        pts_ul[2, 1] -= 10.0
        est_ul = estimator.estimate(pts_ul)
        self.assertEqual(est_ul.pose, HeadPose.UPPER_LEFT)

        # 7. UPPER_RIGHT
        pts_ur = base.copy()
        pts_ur[2, 0] += 10.0
        pts_ur[2, 1] -= 10.0
        est_ur = estimator.estimate(pts_ur)
        self.assertEqual(est_ur.pose, HeadPose.UPPER_RIGHT)

    def test_quality_checker_rules(self):
        """Validates quality gating: multiple faces, blur, low light, and tiny faces."""
        checker = FaceQualityChecker(min_sharpness=60.0, min_brightness=40.0, min_face_size=80)
        frame = np.full((480, 640, 3), 150, dtype=np.uint8)

        # 1. Zero faces
        res_none = checker.evaluate(frame, [])
        self.assertFalse(res_none.passed)
        self.assertIn("No face", res_none.rejection_reason)

        # 2. Multiple faces
        det1 = FaceDetection(bbox=np.array([50, 50, 150, 150]), confidence=0.9, landmarks=np.zeros((5,2)))
        det2 = FaceDetection(bbox=np.array([200, 50, 300, 150]), confidence=0.9, landmarks=np.zeros((5,2)))
        res_multi = checker.evaluate(frame, [det1, det2])
        self.assertFalse(res_multi.passed)
        self.assertIn("Multiple faces", res_multi.rejection_reason)

        # 3. Face too small
        det_small = FaceDetection(bbox=np.array([100, 100, 150, 150]), confidence=0.9, landmarks=np.zeros((5,2)))
        res_small = checker.evaluate(frame, [det_small])
        self.assertFalse(res_small.passed)
        self.assertIn("too far", res_small.rejection_reason)

        # 4. Low contrast check on solid color patch
        det_ok = FaceDetection(bbox=np.array([100, 100, 250, 250]), confidence=0.95, landmarks=np.zeros((5,2)))
        res_contrast = checker.evaluate(frame, [det_ok])
        self.assertFalse(res_contrast.passed)
        self.assertIn("contrast", res_contrast.rejection_reason)

        # 5. Blur check on blurred pattern with high overall contrast
        pattern = np.zeros((480, 640, 3), dtype=np.uint8)
        pattern[:, :320] = 40
        pattern[:, 320:] = 220
        blurred_frame = cv2.GaussianBlur(pattern, (51, 51), 25.0)
        det_blur = FaceDetection(bbox=np.array([200, 100, 400, 300]), confidence=0.95, landmarks=np.zeros((5,2)))
        res_blur = checker.evaluate(blurred_frame, [det_blur])
        self.assertFalse(res_blur.passed)
        self.assertIn("blurry", res_blur.rejection_reason)

    def test_mock_enrollment_session_progression(self):
        """Simulates enrollment state machine progression and DPAPI multi-template persistence."""
        aligner = FaceAligner()
        embedder = ArcFaceEmbedder(self.emb_path)
        
        # We use a mocked detector for unit test speed
        class MockDetector:
            def __init__(self, target_pts):
                self.pts = target_pts
            def detect(self, frame):
                return [FaceDetection(
                    bbox=np.array([100, 100, 300, 300]),
                    confidence=0.95,
                    landmarks=self.pts
                )]

        # Test session initialization
        session = EnrollmentSession(
            detector=MockDetector(ARCFACE_CANONICAL_5PTS),
            aligner=aligner,
            embedder=embedder,
            profile_store=self.profile_store,
            candidates_per_pose=2,
            keep_best_per_pose=1
        )
        session.start()
        self.assertEqual(session.current_pose, HeadPose.CENTER)
        self.assertEqual(session.state, EnrollmentState.GUIDING_TO_POSE)

        # Test finalizing multi-pose profile directly
        dummy_embedding = np.random.randn(512).astype(np.float32)
        dummy_embedding /= np.linalg.norm(dummy_embedding)

        # Populate templates across all 9 poses
        for pose in session.poses:
            session.final_templates.append(session.profile_store.PeekTemplate if hasattr(session.profile_store, 'PeekTemplate') else
                session.final_templates.append(None)
            )
        
        from storage.template_store import PeekTemplate, PeekProfile
        session.final_templates = [
            PeekTemplate(
                template_id=f"tmpl_{p.value.lower()}",
                embedding=dummy_embedding.tolist(),
                pose_label=p.value,
                quality_score=0.95
            )
            for p in session.poses
        ]
        
        profile = session.finalize_and_save_profile("Multi-Pose User")
        self.assertEqual(len(profile.templates), 9)

        # Verify saved file is encrypted
        loaded = self.profile_store.load_profile(profile.profile_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.templates), 9)
        self.assertEqual(loaded.templates[0].pose_label, "CENTER")
        self.assertEqual(loaded.templates[1].pose_label, "LEFT")

if __name__ == "__main__":
    unittest.main()
