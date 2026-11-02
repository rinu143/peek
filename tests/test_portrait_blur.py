"""
Tests for Portrait Blur, Segmentation, and Primary Enrollee Disambiguation
"""

import os
import unittest
import numpy as np
import cv2

from engine.detection.scrfd_detector import FaceDetection, SCRFDDetector
from engine.quality.quality_checker import FaceQualityChecker, resolve_primary_face
from engine.segmentation.portrait_blur import PortraitBlurProcessor
from engine.enrollment.enrollment_runner import EnrollmentRunner


class TestPortraitBlurAndDisambiguation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine", "models"
        )
        cls.det_path = os.path.join(cls.models_dir, "scrfd_500m_bnkps.onnx")
        cls.seg_path = os.path.join(cls.models_dir, "selfie_segmentation.onnx")
        cls.user_img_path = r"C:\Users\Rinu\.gemini\antigravity-cli\brain\7cef18b1-515d-42fe-83af-f7071a38d82b\.user_uploaded\uploaded_media_1793592128375.png"

    def test_resolve_primary_face(self):
        """Tests that the larger, more centered face is resolved as primary enrollee."""
        frame_shape = (480, 640, 3)

        # Primary face: large, centered
        primary = FaceDetection(
            bbox=np.array([220, 140, 420, 340]),  # 200x200, center (320, 240)
            confidence=0.95,
            landmarks=np.zeros((5, 2))
        )
        # Bystander face: small, in the corner
        bystander = FaceDetection(
            bbox=np.array([500, 80, 560, 140]),   # 60x60, center (530, 110)
            confidence=0.88,
            landmarks=np.zeros((5, 2))
        )

        p, s = resolve_primary_face([bystander, primary], frame_shape)
        self.assertIsNotNone(p)
        self.assertEqual(len(s), 1)
        np.testing.assert_array_equal(p.bbox, primary.bbox)
        np.testing.assert_array_equal(s[0].bbox, bystander.bbox)

    def test_quality_checker_bystander_tolerance(self):
        """Tests quality checker behavior with allow_background_bystanders."""
        checker = FaceQualityChecker(min_sharpness=5.0, min_brightness=20.0, min_face_size=50)

        # Create a test frame with high contrast details in face area so sharpness/contrast pass
        frame = np.full((480, 640, 3), 120, dtype=np.uint8)
        # Draw high-contrast facial texture in primary face crop
        cv2.rectangle(frame, (230, 150), (410, 330), (200, 200, 200), -1)
        cv2.circle(frame, (280, 200), 10, (20, 20, 20), -1)
        cv2.circle(frame, (360, 200), 10, (20, 20, 20), -1)
        cv2.line(frame, (280, 270), (360, 270), (40, 40, 40), 3)

        primary = FaceDetection(
            bbox=np.array([220, 140, 420, 340]),  # Area = 40,000
            confidence=0.95,
            landmarks=np.zeros((5, 2))
        )
        bystander = FaceDetection(
            bbox=np.array([500, 80, 560, 140]),   # Area = 3,600 (ratio = 0.09)
            confidence=0.85,
            landmarks=np.zeros((5, 2))
        )
        competing = FaceDetection(
            bbox=np.array([40, 140, 220, 340]),   # Area = 36,000 (ratio = 0.90)
            confidence=0.95,
            landmarks=np.zeros((5, 2))
        )

        # 1. allow_background_bystanders=False strictly rejects any multi-face
        res_strict = checker.evaluate(frame, [primary, bystander], allow_background_bystanders=False)
        self.assertFalse(res_strict.passed)
        self.assertIn("Multiple faces", res_strict.rejection_reason)

        # 2. allow_background_bystanders=True tolerates the small background bystander
        res_tolerant = checker.evaluate(frame, [primary, bystander], allow_background_bystanders=True)
        self.assertTrue(res_tolerant.passed)
        self.assertIsNone(res_tolerant.rejection_reason)

        # 3. allow_background_bystanders=True still rejects competing large foreground faces
        res_competing = checker.evaluate(frame, [primary, competing], allow_background_bystanders=True)
        self.assertFalse(res_competing.passed)
        self.assertIn("Multiple faces", res_competing.rejection_reason)

    def test_portrait_blur_onnx_inference(self):
        """Verifies that PortraitBlurProcessor loads ONNX model and produces blurred composited frame."""
        if not os.path.exists(self.seg_path):
            self.skipTest("Segmentation model not downloaded.")

        processor = PortraitBlurProcessor(model_path=self.seg_path)
        self.assertTrue(processor.has_onnx_model)

        dummy = np.full((480, 640, 3), 100, dtype=np.uint8)
        output = processor.process(dummy)
        self.assertEqual(output.shape, dummy.shape)
        self.assertEqual(output.dtype, np.uint8)

    def test_portrait_blur_fallback_mode(self):
        """Verifies that PortraitBlurProcessor works smoothly in fallback mode without crashing."""
        processor = PortraitBlurProcessor(model_path="nonexistent_model.onnx")
        self.assertFalse(processor.has_onnx_model)

        dummy = np.full((480, 640, 3), 120, dtype=np.uint8)
        primary = FaceDetection(bbox=np.array([200, 150, 400, 350]), confidence=0.9, landmarks=np.zeros((5, 2)))
        output = processor.process(dummy, primary_face=primary)
        self.assertEqual(output.shape, dummy.shape)

    def test_secondary_face_suppression_on_user_photo(self):
        """
        Loads the user's actual photograph containing the enrolling user and a
        background coworker. Verifies that before blur, 2 faces are detected, and
        after PortraitBlurProcessor, the background face is suppressed so SCRFD
        detects exactly 1 face (the enrolling user).
        """
        if not os.path.exists(self.user_img_path):
            self.skipTest("User test image not found.")
        if not os.path.exists(self.det_path):
            self.skipTest("SCRFD model not found.")

        img = cv2.imread(self.user_img_path)
        self.assertIsNotNone(img)

        detector = SCRFDDetector(self.det_path)
        dets_before = detector.detect(img)
        self.assertEqual(len(dets_before), 2, "Expected 2 faces detected in original photo.")

        primary = dets_before[0]
        secondary = dets_before[1:]

        processor = PortraitBlurProcessor()
        blurred = processor.process(img, primary_face=primary, secondary_faces=secondary)

        dets_after = detector.detect(blurred)
        self.assertEqual(
            len(dets_after), 1,
            f"Expected exactly 1 face detected after background blur, got {len(dets_after)}"
        )
        # Verify detected face corresponds to primary user
        self.assertGreater(dets_after[0].area, 5000.0)

    def test_enrollment_runner_blur_default(self):
        """Verifies EnrollmentRunner has blur_background enabled by default."""
        runner = EnrollmentRunner(display_name="Test User", blur_background=True)
        self.assertTrue(runner.blur_background)
        self.assertIsNotNone(runner.portrait_processor)


if __name__ == "__main__":
    unittest.main()
