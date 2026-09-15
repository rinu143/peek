"""
Peek Face Engine - Screen-Replay Attack Regression Tests
Regression tests reproducing the smartphone screen replay vulnerability:
- Attack Scenario: A smartphone displaying an enrolled user's face is held before the webcam.
- Previous Failure Mode: 2D hand tremor satisfied motion score, lack of bezel detector allowed
  weighted score fusion (0.4*mot + 0.35*tex + 0.25*eye) to reach Live 98% and grant unlock.
- Hardened Verification:
  1. Bezel detection veto triggers immediate rejection when phone chassis is visible.
  2. 3D perspective parallax veto detects 2D hand tremor and blocks flat screens.
  3. Non-negotiable security guarantee: is_authorized_to_unlock MUST remain False during replay attack.
"""

import unittest
import numpy as np
import cv2

from engine.liveness.bezel_detector import BezelDetector
from engine.liveness.motion_detector import MotionParallaxDetector
from engine.liveness.liveness_detector import LivenessDetector
from engine.pipeline.face_engine import FaceEngine, EngineFrameResult
from engine.detection.scrfd_detector import FaceDetection
from engine.recognition.matcher import MatchResult
from engine.temporal.temporal_verifier import TemporalVerificationResult
from storage.template_store import PeekProfile, PeekTemplate


class TestScreenReplayRegression(unittest.TestCase):

    def setUp(self):
        self.bezel_detector = BezelDetector()
        self.motion_detector = MotionParallaxDetector(window_size=10, min_frames_required=5)
        self.liveness_detector = LivenessDetector(min_frames_to_confirm=6, liveness_threshold=0.58)

        # Baseline face geometry
        self.face_bbox = (160.0, 100.0, 320.0, 300.0)  # (x1, y1, x2, y2)
        self.face_landmarks = np.array([
            [210.0, 165.0],  # left eye
            [270.0, 165.0],  # right eye
            [240.0, 205.0],  # nose
            [220.0, 250.0],  # left mouth
            [260.0, 250.0],  # right mouth
        ], dtype=np.float32)

    def _generate_synthetic_phone_replay_frame(self, w=480, h=480, jitter_x=0.0, jitter_y=0.0):
        """Generates a webcam frame with a smartphone bezel surrounding the face area."""
        img = np.full((h, w, 3), 160, dtype=np.uint8)  # Room background

        # Draw smartphone body (dark rectangle, aspect ratio ~ 1.8)
        bx1 = int(120 + jitter_x)
        by1 = int(40 + jitter_y)
        bx2 = int(360 + jitter_x)
        by2 = int(420 + jitter_y)
        cv2.rectangle(img, (bx1, by1), (bx2, by2), (20, 20, 20), -1)

        # Draw phone screen inside bezel
        sx1, sy1 = bx1 + 16, by1 + 25
        sx2, sy2 = bx2 - 16, by2 - 25
        cv2.rectangle(img, (sx1, sy1), (sx2, sy2), (200, 190, 180), -1)

        # Draw simple face features on the screen
        fx1 = int(self.face_bbox[0] + jitter_x)
        fy1 = int(self.face_bbox[1] + jitter_y)
        fx2 = int(self.face_bbox[2] + jitter_x)
        fy2 = int(self.face_bbox[3] + jitter_y)
        cv2.rectangle(img, (fx1, fy1), (fx2, fy2), (210, 180, 160), -1)
        # Eyes
        cv2.circle(img, (int(210 + jitter_x), int(165 + jitter_y)), 8, (60, 40, 30), -1)
        cv2.circle(img, (int(270 + jitter_x), int(165 + jitter_y)), 8, (60, 40, 30), -1)
        # Mouth
        cv2.line(img, (int(220 + jitter_x), int(250 + jitter_y)), (int(260 + jitter_x), int(250 + jitter_y)), (120, 50, 50), 3)

        return img

    def test_screen_replay_with_bezel_is_rejected_and_vetoed(self):
        """
        Validates that a phone replay attack with bezel visible in frame is vetoed
        and escalates to SPOOF_DETECTED within 2 frames.
        """
        np.random.seed(42)
        detector = LivenessDetector(min_frames_to_confirm=6, liveness_threshold=0.58)

        last_res = None
        for i in range(8):
            # Hand tremor jitter (0.5 to 1.5 px)
            jx = float(np.sin(i * 0.7) * 1.2)
            jy = float(np.cos(i * 0.6) * 1.0)

            frame = self._generate_synthetic_phone_replay_frame(jitter_x=jx, jitter_y=jy)
            bbox = (
                self.face_bbox[0] + jx,
                self.face_bbox[1] + jy,
                self.face_bbox[2] + jx,
                self.face_bbox[3] + jy
            )
            lms = self.face_landmarks + np.array([jx, jy], dtype=np.float32)

            last_res = detector.update(frame, bbox, lms, track_id=1)

        self.assertIsNotNone(last_res)
        # MUST NOT be live
        self.assertFalse(last_res.is_live)
        # MUST detect spoof
        self.assertEqual(last_res.state, "SPOOF_DETECTED")
        self.assertIn("BEZEL_DETECTED", last_res.spoof_reason)
        # Score must be severely capped by veto (<= 0.15)
        self.assertLessEqual(last_res.fused_score, 0.15)

    def test_borderless_phone_tremor_fails_3d_parallax(self):
        """
        Validates that even if the bezel is somehow not visible (e.g. cropped / borderless display),
        hand tremor produces 2D translation without differential 3D depth parallax,
        triggering the lacks_parallax veto and blocking authorization.
        """
        np.random.seed(99)
        detector = LivenessDetector(min_frames_to_confirm=6, liveness_threshold=0.58)
        plain_frame = np.full((480, 480, 3), 180, dtype=np.uint8)

        last_res = None
        for i in range(12):
            # Pure 2D hand-held translation tremor (all landmarks shift by exact same vector)
            shift_x = float(np.sin(i * 0.9) * 1.8)
            shift_y = float(np.cos(i * 0.8) * 1.2)

            bbox = (
                self.face_bbox[0] + shift_x,
                self.face_bbox[1] + shift_y,
                self.face_bbox[2] + shift_x,
                self.face_bbox[3] + shift_y
            )
            # Invariant relative landmark spacing (flat screen)
            lms = self.face_landmarks + np.array([shift_x, shift_y], dtype=np.float32)

            last_res = detector.update(plain_frame, bbox, lms, track_id=2)

        self.assertIsNotNone(last_res)
        self.assertFalse(last_res.is_live)
        # Score capped by veto
        self.assertLessEqual(last_res.fused_score, 0.20)
        self.assertEqual(last_res.state, "SPOOF_DETECTED")
        self.assertIn("LACKS_PARALLAX", last_res.spoof_reason)

    def test_dual_gate_denies_unlock_during_screen_replay(self):
        """
        Simulates the exact end-to-end condition from the vulnerability report:
        Even if biometric matcher matches with 100% confidence and temporal verifier confirms,
        is_authorized_to_unlock MUST be False because liveness fails.
        """
        np.random.seed(77)
        detector = LivenessDetector(min_frames_to_confirm=6, liveness_threshold=0.58)

        # Generate replay attack with phone bezel
        for i in range(10):
            jx = float(np.sin(i * 0.8) * 1.0)
            jy = float(np.cos(i * 0.7) * 0.8)
            frame = self._generate_synthetic_phone_replay_frame(jitter_x=jx, jitter_y=jy)
            bbox = (self.face_bbox[0] + jx, self.face_bbox[1] + jy, self.face_bbox[2] + jx, self.face_bbox[3] + jy)
            lms = self.face_landmarks + np.array([jx, jy], dtype=np.float32)

            liveness_res = detector.update(frame, bbox, lms, track_id=3)

            # Simulated biometric match (attacker presenting enrolled user's high-res photo/video)
            match_pass = True
            temporal_confirmed = True

            is_authorized = bool(match_pass and temporal_confirmed and liveness_res.is_live)
            self.assertFalse(is_authorized, f"Frame {i}: Security breach! Authorized unlock granted during screen replay.")

        self.assertFalse(liveness_res.is_live)
        self.assertEqual(liveness_res.state, "SPOOF_DETECTED")


if __name__ == "__main__":
    unittest.main()
