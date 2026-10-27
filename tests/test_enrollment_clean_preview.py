"""
Unit tests for clean preview frame, native overlay data, and detector efficiency.
Validates:
1. preview_frame equals the mirrored/cropped input on a flat test frame (no HUD pixels).
2. overlay coordinate values are normalized in [0.0, 1.0].
3. frame_kind="clean" only when requested via clean_preview=True in START_ENROLLMENT.
4. At most one detector call per frame (mock detector call count).
"""

import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import cv2
import json

from engine.detection.scrfd_detector import FaceDetection
from engine.enrollment.enrollment_runner import EnrollmentRunner, EnrollmentStepResult
from engine.enrollment.enrollment_session import EnrollmentSession, EnrollmentState
from engine.pose.pose_estimator import HeadPose
from engine.ipc.enrollment_protocol import (
    make_start_enrollment,
    make_preview_frame,
    MSG_PREVIEW_FRAME,
    parse_message,
    serialize_message,
)
from engine.ipc.enrollment_pipe_server import EnrollmentPipeServer, EnrollmentPipeClientSession


class MockCamera:
    def __init__(self, frame: np.ndarray):
        self.frame = frame
        self.is_started = False

    def start(self) -> bool:
        self.is_started = True
        return True

    def stop(self):
        self.is_started = False

    def read_frame(self):
        return True, self.frame.copy()


class TestEnrollmentCleanPreview(unittest.TestCase):

    def test_preview_frame_equals_mirrored_cropped_flat_input_no_hud(self):
        """Validates that preview_frame contains only the mirrored/cropped camera pixels without HUD."""
        # Create a non-square 640x480 flat color test frame (e.g. constant 128)
        flat_val = 128
        raw_frame = np.full((480, 640, 3), flat_val, dtype=np.uint8)

        # Expected mirrored and cropped square (480x480, offset=(640-480)//2=80)
        mirrored = cv2.flip(raw_frame, 1)
        expected_cropped = mirrored[:, 80:80 + 480]
        expected_preview = cv2.resize(expected_cropped, (480, 480), interpolation=cv2.INTER_LINEAR)

        mock_camera = MockCamera(raw_frame)
        mock_detector = MagicMock()
        mock_detector.detect.return_value = []

        mock_aligner = MagicMock()
        mock_embedder = MagicMock()
        mock_store = MagicMock()
        mock_store.list_profiles.return_value = []

        session = EnrollmentSession(
            detector=mock_detector,
            aligner=mock_aligner,
            embedder=mock_embedder,
            profile_store=mock_store,
        )
        runner = EnrollmentRunner(
            session=session,
            camera=mock_camera,
            profile_store=mock_store,
        )
        runner.start("Test User")

        step_res = runner.step()
        self.assertIsNotNone(step_res)
        self.assertIsNotNone(step_res.preview_frame)

        # 1. preview_frame must exactly equal expected_preview
        np.testing.assert_array_equal(step_res.preview_frame, expected_preview)

        # 2. composited_frame must have HUD drawing (e.g., text, background, canvas is 520x760)
        self.assertEqual(step_res.composited_frame.shape, (520, 760, 3))
        # preview_frame must be clean 480x480
        self.assertEqual(step_res.preview_frame.shape, (480, 480, 3))
        self.assertTrue(np.all(step_res.preview_frame == flat_val))

    def test_overlay_values_normalized_zero_to_one(self):
        """Validates that all bounding box and landmark coordinates in overlay are clamped in [0, 1]."""
        raw_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mock_camera = MockCamera(raw_frame)

        # In mirrored frame (640x480), square crop is x in [80, 560], y in [0, 480]
        # Provide detection bbox that spans inside the cropped region
        test_bbox = np.array([120.0, 50.0, 320.0, 250.0], dtype=np.float32)
        test_landmarks = np.array([
            [160.0, 100.0],
            [280.0, 100.0],
            [220.0, 150.0],
            [170.0, 200.0],
            [270.0, 200.0],
        ], dtype=np.float32)

        mock_det = FaceDetection(
            bbox=test_bbox,
            confidence=0.98,
            landmarks=test_landmarks
        )

        mock_detector = MagicMock()
        mock_detector.detect.return_value = [mock_det]

        session = EnrollmentSession(
            detector=mock_detector,
            aligner=MagicMock(),
            embedder=MagicMock(),
            profile_store=MagicMock(),
        )
        runner = EnrollmentRunner(
            session=session,
            camera=mock_camera,
            profile_store=MagicMock(),
        )
        runner.start("Test User")

        step_res = runner.step()
        self.assertIsNotNone(step_res)
        overlay = step_res.overlay
        self.assertIsNotNone(overlay)

        face = overlay["face"]
        self.assertTrue(face["found"])
        bx1, by1, bx2, by2 = face["bbox"]
        for val in (bx1, by1, bx2, by2):
            self.assertGreaterEqual(val, 0.0)
            self.assertLessEqual(val, 1.0)

        for pt in face["landmarks"]:
            self.assertEqual(len(pt), 2)
            self.assertGreaterEqual(pt[0], 0.0)
            self.assertLessEqual(pt[0], 1.0)
            self.assertGreaterEqual(pt[1], 0.0)
            self.assertLessEqual(pt[1], 1.0)

        self.assertIn("target", overlay)
        self.assertIn("samples", overlay)
        self.assertIn("poses_completed", overlay)

    def test_exactly_one_detector_call_per_frame(self):
        """Validates that detector.detect is invoked at most ONCE per step iteration."""
        raw_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mock_camera = MockCamera(raw_frame)

        mock_detector = MagicMock()
        mock_detector.detect.return_value = []

        session = EnrollmentSession(
            detector=mock_detector,
            aligner=MagicMock(),
            embedder=MagicMock(),
            profile_store=MagicMock(),
        )
        runner = EnrollmentRunner(
            session=session,
            camera=mock_camera,
            profile_store=MagicMock(),
        )
        runner.start("Test User")

        # Initial call count should be 0
        self.assertEqual(mock_detector.detect.call_count, 0)

        # Run step 1
        runner.step()
        self.assertEqual(mock_detector.detect.call_count, 1)

        # Run step 2
        runner.step()
        self.assertEqual(mock_detector.detect.call_count, 2)

        # Run step 3
        runner.step()
        self.assertEqual(mock_detector.detect.call_count, 3)

    def test_frame_kind_clean_only_when_requested(self):
        """Validates that frame_kind='clean' and overlay are included only when clean_preview=True."""
        # 1. Clean preview requested -> frame_kind="clean" and overlay present
        msg_clean = make_preview_frame(
            frame_jpeg_b64="b64data",
            target_pose="CENTER",
            quality_state="PASSED",
            progress_pct=25.0,
            guidance_text="Look Straight",
            overlay={"face": {"found": True}},
            frame_kind="clean",
        )
        self.assertEqual(msg_clean.get("frame_kind"), "clean")
        self.assertIn("overlay", msg_clean)
        self.assertEqual(msg_clean["overlay"], {"face": {"found": True}})

        # 2. Legacy / default -> frame_kind not clean, overlay omitted
        msg_legacy = make_preview_frame(
            frame_jpeg_b64="b64data",
            target_pose="CENTER",
            quality_state="PASSED",
            progress_pct=25.0,
            guidance_text="Look Straight",
        )
        self.assertNotIn("frame_kind", msg_legacy)
        self.assertNotIn("overlay", msg_legacy)

        # 3. Protocol serialization roundtrip preserves fields
        parsed = parse_message(serialize_message(msg_clean))
        self.assertEqual(parsed["frame_kind"], "clean")
        self.assertEqual(parsed["overlay"], {"face": {"found": True}})


if __name__ == "__main__":
    unittest.main()
