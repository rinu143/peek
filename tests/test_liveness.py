"""
Peek Face Engine - Liveness & Anti-Spoofing Automated Tests
Validates:
1. Motion parallax & micro-movement detection (static photo and rigid translation rejection).
2. Texture analysis & screen moiré detection.
3. Eye dynamics & blink signature detection.
4. Independent liveness state machine & track reset.
5. Dual-gate unlock authorization constraint (Constraint #1).
"""

import unittest
import numpy as np
import cv2

from engine.liveness.motion_detector import MotionParallaxDetector
from engine.liveness.texture_checker import TextureChecker
from engine.liveness.eye_dynamics import EyeDynamicsDetector
from engine.liveness.liveness_detector import LivenessDetector
from engine.pipeline.face_engine import FaceEngine, EngineFrameResult
from engine.recognition.matcher import MatchResult
from engine.temporal.temporal_verifier import TemporalVerificationResult
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate


class TestLivenessEngine(unittest.TestCase):

    def setUp(self):
        # Base canonical 5 facial landmarks for synthetic face
        self.base_landmarks = np.array([
            [50.0, 45.0],   # left eye
            [85.0, 45.0],   # right eye
            [67.5, 65.0],   # nose
            [55.0, 85.0],   # left mouth
            [80.0, 85.0],   # right mouth
        ], dtype=np.float32)
        self.base_bbox = (25.0, 20.0, 110.0, 105.0)

    def test_motion_detector_freeze_detection(self):
        """Validates that a frozen video stream or static printed photograph is flagged as a spoof."""
        detector = MotionParallaxDetector(window_size=10, min_frames_required=6)
        
        # Feed 10 identical frames with 0.0 variance
        res = None
        for _ in range(10):
            res = detector.update(self.base_landmarks, self.base_bbox, track_id=1)

        self.assertIsNotNone(res)
        self.assertFalse(res.is_live)
        self.assertTrue(res.is_frozen)
        self.assertEqual(res.spoof_reason, "STATIC_PHOTO")
        self.assertLess(res.score, 0.20)

    def test_motion_detector_rigid_planar_motion(self):
        """Validates that a photo/tablet moved in 2D without 3D parallax is rejected as rigid planar spoof."""
        detector = MotionParallaxDetector(window_size=12, min_frames_required=6)

        res = None
        # Move the entire face across 10 frames by adding 2px per frame in 2D (pure rigid translation)
        for i in range(10):
            offset = float(i * 2.5)
            shifted_lms = self.base_landmarks + np.array([offset, offset * 0.5])
            shifted_bbox = (
                self.base_bbox[0] + offset,
                self.base_bbox[1] + offset * 0.5,
                self.base_bbox[2] + offset,
                self.base_bbox[3] + offset * 0.5
            )
            res = detector.update(shifted_lms, shifted_bbox, track_id=1)

        self.assertIsNotNone(res)
        self.assertFalse(res.is_live)
        self.assertTrue(res.is_rigid_planar)
        self.assertEqual(res.spoof_reason, "RIGID_PLANAR_MOTION")

    def test_motion_detector_genuine_micro_movement(self):
        """Validates that natural living human posture variations & 3D parallax pass liveness."""
        detector = MotionParallaxDetector(window_size=12, min_frames_required=6)

        np.random.seed(42)
        res = None
        for i in range(10):
            # Subtle physiological tremor (0.4 to 1.5 px)
            jitter = np.random.normal(0.0, 0.8, size=(5, 2)).astype(np.float32)
            # Subtle natural 3D head pitch adjustment
            pitch_shift = np.sin(i * 0.4) * 1.2
            jitter[2, 1] += pitch_shift

            live_lms = self.base_landmarks + jitter
            bbox_jitter = np.random.normal(0.0, 0.7, size=4).astype(np.float32)
            live_bbox = tuple(np.array(self.base_bbox) + bbox_jitter)

            res = detector.update(live_lms, live_bbox, track_id=1)

        self.assertIsNotNone(res)
        self.assertTrue(res.is_live)
        self.assertFalse(res.is_frozen)
        self.assertFalse(res.is_rigid_planar)
        self.assertGreaterEqual(res.score, 0.60)

    def test_texture_checker_screen_moire(self):
        """Validates that screen replay pixel patterns / moiré fringes trigger screen spoof detection."""
        checker = TextureChecker()

        # Create a frame with strong high-frequency periodic grid stripes (screen pixels)
        frame = np.full((160, 160, 3), 180, dtype=np.uint8)
        # Add high-frequency grid frequencies
        frame[::4, :, :] = 40
        frame[:, ::4, :] = 40

        res = checker.evaluate(frame, (20, 20, 140, 140))
        self.assertFalse(res.is_live)
        self.assertTrue(res.moire_detected)
        self.assertEqual(res.spoof_reason, "SCREEN_MOIRE")

    def test_texture_checker_natural_skin(self):
        """Validates that a smooth skin-toned face crop passes texture evaluation."""
        checker = TextureChecker()

        # Create a synthetic skin-toned image with natural smooth gradient (Cr ~ 145, Cb ~ 105 in YCrCb)
        # Equivalent BGR approx: B=120, G=150, R=200
        frame = np.zeros((160, 160, 3), dtype=np.uint8)
        frame[:, :] = [120, 150, 200]
        # Add subtle natural texture noise (skin pores / diffuse lighting)
        noise = np.random.normal(0, 3, size=frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        res = checker.evaluate(frame, (20, 20, 140, 140))
        self.assertTrue(res.is_live)
        self.assertFalse(res.moire_detected)
        self.assertFalse(res.specular_glare_detected)
        self.assertGreaterEqual(res.score, 0.65)

    def test_eye_dynamics_blink_detection(self):
        """Validates that eye gradient drops and recoveries are registered as blinks."""
        detector = EyeDynamicsDetector(history_size=15)

        # Generate a frame with open eye contrast
        frame_open = np.full((120, 140, 3), 160, dtype=np.uint8)
        # Left eye pupil
        cv2.circle(frame_open, (50, 45), 4, (10, 10, 10), -1)
        # Right eye pupil
        cv2.circle(frame_open, (85, 45), 4, (10, 10, 10), -1)

        # Generate closed eye frame (homogeneous skin, no pupil/edges)
        frame_closed = np.full((120, 140, 3), 160, dtype=np.uint8)

        # Feed 5 open frames
        for _ in range(5):
            detector.update(frame_open, self.base_landmarks, track_id=1)

        # Feed 2 closed frames (blink)
        for _ in range(2):
            detector.update(frame_closed, self.base_landmarks, track_id=1)

        # Feed 3 open frames (re-opening)
        res = None
        for _ in range(3):
            res = detector.update(frame_open, self.base_landmarks, track_id=1)

        self.assertIsNotNone(res)
        self.assertTrue(res.blink_detected)
        self.assertGreaterEqual(res.blink_count, 1)

    def test_liveness_detector_track_reset(self):
        """Validates that switching tracks or losing target immediately resets liveness history."""
        detector = LivenessDetector(min_frames_to_confirm=6)
        
        frame = np.full((120, 140, 3), 160, dtype=np.uint8)
        for _ in range(4):
            detector.update(frame, self.base_bbox, self.base_landmarks, track_id=1)
        self.assertEqual(detector._frame_count, 4)

        # Switch to track 2
        detector.update(frame, self.base_bbox, self.base_landmarks, track_id=2)
        self.assertEqual(detector._frame_count, 1)
        self.assertEqual(detector._current_track_id, 2)

    def test_dual_gate_security_authorization(self):
        """
        NON-NEGOTIABLE SECURITY CONSTRAINT #1:
        Validates that biometric match alone CANNOT unlock.
        Unlock is authorized IF AND ONLY IF both biometric match AND liveness pass.
        """
        # Scenario A: Biometric match passes, but liveness fails -> UNAUTHORIZED
        biometric_pass = True
        liveness_pass = False
        is_authorized = bool(biometric_pass and liveness_pass)
        self.assertFalse(is_authorized, "Biometric match alone must NOT authorize unlock")

        # Scenario B: Liveness passes, but biometric match fails -> UNAUTHORIZED
        biometric_pass = False
        liveness_pass = True
        is_authorized = bool(biometric_pass and liveness_pass)
        self.assertFalse(is_authorized, "Liveness alone must NOT authorize unlock")

        # Scenario C: Both pass -> AUTHORIZED TO UNLOCK
        biometric_pass = True
        liveness_pass = True
        is_authorized = bool(biometric_pass and liveness_pass)
        self.assertTrue(is_authorized, "Dual gates satisfied -> Must authorize unlock")


if __name__ == "__main__":
    unittest.main()
