"""
Tests for 3D Perspective Parallax & Micro-Movement Liveness Verification
Validates that MotionParallaxDetector distinguishes:
1. Genuine 3D head rotation / posture adjustments (non-uniform relative landmark movement).
2. Hand-held flat 2D replay with hand-tremor (pure 2D rigid translation with invariant relative ratios).
"""

import unittest
import numpy as np

from engine.liveness.motion_detector import MotionParallaxDetector, MotionLivenessResult


class TestMotionParallax(unittest.TestCase):

    def setUp(self):
        self.detector = MotionParallaxDetector(window_size=12, min_frames_required=6)
        self.base_landmarks = np.array([
            [50.0, 45.0],   # left eye
            [85.0, 45.0],   # right eye
            [67.5, 65.0],   # nose
            [55.0, 85.0],   # left mouth
            [80.0, 85.0],   # right mouth
        ], dtype=np.float32)
        self.base_bbox = (25.0, 20.0, 110.0, 105.0)

    def test_genuine_3D_head_movement_passes(self):
        """
        Simulates genuine 3D living head motion:
        Nose tip undergoes differential 3D perspective foreshortening relative to eye/mouth plane.
        """
        np.random.seed(42)
        res = None
        for i in range(12):
            jitter = np.random.normal(0.0, 0.6, size=(5, 2)).astype(np.float32)
            # 3D out-of-plane head pitch/yaw rotation causes non-uniform nose movement
            jitter[2, 1] += np.sin(i * 0.5) * 1.5
            jitter[2, 0] += np.cos(i * 0.4) * 0.9

            lms = self.base_landmarks + jitter
            bbox_jitter = np.random.normal(0.0, 0.5, size=4).astype(np.float32)
            bbox = tuple(np.array(self.base_bbox) + bbox_jitter)

            res = self.detector.update(lms, bbox, track_id=1)

        self.assertIsNotNone(res)
        self.assertTrue(res.is_live)
        self.assertFalse(res.lacks_parallax)
        self.assertFalse(res.is_frozen)
        self.assertGreaterEqual(res.score, 0.60)

    def test_hand_held_2D_tremor_fails_parallax(self):
        """
        Simulates hand-held phone replay attack:
        Hand tremor causes 2D motion (centroid moves 0.8-2.0px), but internal relative
        spacing between landmarks remains invariant (lacks 3D depth parallax).
        """
        np.random.seed(101)
        res = None
        for i in range(12):
            # Hand tremor jitter applied to the ENTIRE phone/face as a rigid 2D translation
            hand_tremor_dx = np.sin(i * 1.2) * 1.8 + np.random.normal(0, 0.3)
            hand_tremor_dy = np.cos(i * 0.9) * 1.4 + np.random.normal(0, 0.3)

            # All 5 landmarks shift identically in 2D (zero non-rigid differential motion)
            lms = self.base_landmarks + np.array([hand_tremor_dx, hand_tremor_dy], dtype=np.float32)
            bbox = (
                self.base_bbox[0] + hand_tremor_dx,
                self.base_bbox[1] + hand_tremor_dy,
                self.base_bbox[2] + hand_tremor_dx,
                self.base_bbox[3] + hand_tremor_dy
            )

            res = self.detector.update(lms, bbox, track_id=1)

        self.assertIsNotNone(res)
        self.assertFalse(res.is_live)
        self.assertTrue(res.lacks_parallax)
        self.assertEqual(res.spoof_reason, "LACKS_PARALLAX")
        self.assertLess(res.score, 0.30)


if __name__ == "__main__":
    unittest.main()
