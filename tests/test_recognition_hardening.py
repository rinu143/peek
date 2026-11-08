"""
Peek Face Engine - Recognition Hardening Automated Tests (Phase 3)
Validates dominant-face tracking, IoU association, target stickiness/hysteresis,
and rolling temporal verification window.
"""

import unittest
import numpy as np

from engine.tracking.face_tracker import FaceTracker, TrackedFace, compute_iou
from engine.temporal.temporal_verifier import TemporalVerifier, TemporalVerificationResult
from engine.recognition.matcher import FaceMatcher
from engine.recognition.calibrator import ThresholdCalibrator
from engine.detection.scrfd_detector import FaceDetection

class TestRecognitionHardening(unittest.TestCase):

    def test_compute_iou(self):
        """Validates bounding box IoU calculation."""
        box1 = np.array([0, 0, 100, 100])
        box2 = np.array([0, 0, 100, 100])
        self.assertAlmostEqual(compute_iou(box1, box2), 1.0)

        # 50% horizontal overlap
        box3 = np.array([50, 0, 150, 100])
        # Intersection: 50x100 = 5000. Union: 10000 + 10000 - 5000 = 15000. IoU = 1/3
        self.assertAlmostEqual(compute_iou(box1, box3), 1.0 / 3.0, places=4)

        # Disjoint
        box4 = np.array([200, 200, 300, 300])
        self.assertEqual(compute_iou(box1, box4), 0.0)

    def test_tracker_continuity_and_hysteresis(self):
        """Validates that FaceTracker maintains track_id continuity and resists target switching."""
        tracker = FaceTracker(min_hits_to_confirm=2, max_missing_frames=3, hysteresis_area_margin=1.25)
        dummy_kps = np.zeros((5, 2))

        # Frame 1: User face appears at [100, 100, 200, 200] (Area = 10,000)
        d1 = FaceDetection(bbox=np.array([100, 100, 200, 200]), confidence=0.95, landmarks=dummy_kps)
        dom1, all1 = tracker.update([d1])
        self.assertIsNotNone(dom1)
        initial_id = dom1.track_id

        # Frame 2: User moves slightly to [105, 105, 205, 205]
        d2 = FaceDetection(bbox=np.array([105, 105, 205, 205]), confidence=0.95, landmarks=dummy_kps)
        dom2, all2 = tracker.update([d2])
        self.assertEqual(dom2.track_id, initial_id, "Track ID must remain continuous for moving face")
        self.assertTrue(dom2.is_confirmed)

        # Frame 3: Bystander face appears with slightly larger area (+15%, below 1.25 hysteresis margin)
        # Area of d_bystander = 110 x 105 = 11,550 (< 1.25 * 10,000)
        d_user = FaceDetection(bbox=np.array([110, 110, 210, 210]), confidence=0.95, landmarks=dummy_kps)
        d_bystander = FaceDetection(bbox=np.array([300, 100, 410, 205]), confidence=0.95, landmarks=dummy_kps)
        dom3, all3 = tracker.update([d_user, d_bystander])
        self.assertEqual(len(all3), 2)
        self.assertEqual(dom3.track_id, initial_id, "Hysteresis must prevent switching to bystander face")

    def test_temporal_verification_rolling_window(self):
        """Validates M-of-N sliding window temporal confirmation."""
        # Requires 5 positive frames out of last 7
        verifier = TemporalVerifier(window_size=7, min_matches_required=5, ema_alpha=0.4)
        target_id = 1

        # Feed 4 matching frames: not yet confirmed
        for _ in range(4):
            res = verifier.update(target_id, is_frame_match=True, frame_confidence=0.85)
            self.assertFalse(res.is_temporally_confirmed)
            self.assertFalse(res.is_authorized_to_unlock)

        # Feed 5th matching frame: now confirmed!
        res_5 = verifier.update(target_id, is_frame_match=True, frame_confidence=0.85)
        self.assertTrue(res_5.is_temporally_confirmed)
        self.assertEqual(res_5.positive_matches_in_window, 5)

        # CRITICAL SECURITY CONSTRAINT #1 CHECK:
        # Temporal confirmation alone can NEVER authorize unlock!
        self.assertFalse(
            res_5.is_authorized_to_unlock,
            "Security Constraint #1 violated: Temporal confirmation must never authorize unlock directly!"
        )

        # Frame with mismatch
        res_6 = verifier.update(target_id, is_frame_match=False, frame_confidence=0.30)
        # Still 5 positive matches out of 6 frames in window -> remains confirmed
        self.assertTrue(res_6.is_temporally_confirmed)

        # Track ID changes (e.g. face lost or swapped) -> window must reset immediately
        res_swapped = verifier.update(track_id=2, is_frame_match=True, frame_confidence=0.90)
        self.assertFalse(res_swapped.is_temporally_confirmed, "Window must reset when track ID changes")
        self.assertEqual(res_swapped.positive_matches_in_window, 1)

    def test_enhanced_matcher_multi_template_scoring(self):
        """Validates multi-template scoring and top-K aggregation."""
        matcher = FaceMatcher(default_threshold=0.50)

        # Synthetic templates
        v_center = np.zeros(512, dtype=np.float32)
        v_center[0] = 1.0  # Unit vector along axis 0

        v_left = np.zeros(512, dtype=np.float32)
        v_left[1] = 1.0    # Unit vector along axis 1

        enrolled = [v_center, v_left]
        poses = ["CENTER", "LEFT"]

        # Query matching v_left with estimated pose "LEFT"
        query = v_left.copy()
        res = matcher.match(query, enrolled, template_poses=poses, estimated_pose="LEFT")
        self.assertTrue(res.is_match)
        self.assertEqual(res.best_template_idx, 1)
        self.assertEqual(res.best_template_pose, "LEFT")
        self.assertAlmostEqual(res.confidence, 1.0, places=3)
        self.assertFalse(res.is_authorized_to_unlock)

    def test_threshold_calibrator(self):
        """Validates threshold calibrator metrics generation."""
        calibrator = ThresholdCalibrator()

        v1 = np.random.randn(512).astype(np.float32)
        v1 /= np.linalg.norm(v1)

        # Genuine pair with small noise (high similarity ~0.95)
        noise = np.random.randn(512).astype(np.float32)
        noise /= np.linalg.norm(noise)
        v1_noisy = v1 + noise * 0.15
        v1_noisy /= np.linalg.norm(v1_noisy)

        # Impostor pair (orthogonal, similarity ~0.0)
        v2 = np.random.randn(512).astype(np.float32)
        v2 /= np.linalg.norm(v2)

        report = calibrator.evaluate(
            genuine_pairs=[(v1, v1_noisy)],
            impostor_pairs=[(v1, v2)]
        )

        self.assertGreater(report.genuine_mean, 0.70)
        self.assertLess(report.impostor_mean, 0.30)
        self.assertIn(0.48, report.threshold_metrics)

if __name__ == "__main__":
    unittest.main()
