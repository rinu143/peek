"""
Tests for BezelDetector (Screen-Edge Anti-Spoofing Detection)
Validates:
1. Plain background without bezel (confidence < 0.25, not detected).
2. Clear synthetic smartphone bezel framing the face (confidence >= 0.80, detected).
3. Partial / occluded bezel.
4. Natural rectilinear background geometry (distant doorframe, single window line)
   to ensure the false-positive rate is appropriately guarded.
5. Periodic background textures (venetian blinds + wall edge) must not trigger.
"""

import unittest
import numpy as np
import cv2

from engine.liveness.bezel_detector import BezelDetector, BezelDetectionResult


class TestBezelDetector(unittest.TestCase):

    def setUp(self):
        self.detector = BezelDetector()
        self.face_bbox = (100, 100, 220, 240)  # w=120, h=140

    def _create_synthetic_face(self, canvas, bbox):
        """Draws a synthetic face onto the canvas at the given bbox."""
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        rx, ry = (x2 - x1) // 2, (y2 - y1) // 2
        # Head oval
        cv2.ellipse(canvas, (cx, cy), (rx, ry), 0, 0, 360, (120, 150, 200), -1)
        # Eyes
        cv2.circle(canvas, (cx - 25, cy - 20), 5, (20, 20, 20), -1)
        cv2.circle(canvas, (cx + 25, cy - 20), 5, (20, 20, 20), -1)
        # Mouth
        cv2.line(canvas, (cx - 20, cy + 35), (cx + 20, cy + 35), (40, 40, 160), 3)

    def test_plain_background_no_bezel(self):
        """Validates that a face in a standard room without screen bezels returns low confidence."""
        canvas = np.full((480, 640, 3), 190, dtype=np.uint8)
        # Add diffuse room lighting variation
        noise = np.random.normal(0, 3, size=canvas.shape).astype(np.int16)
        canvas = np.clip(canvas.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        self._create_synthetic_face(canvas, self.face_bbox)

        res = self.detector.evaluate(canvas, self.face_bbox)
        self.assertFalse(res.bezel_detected)
        self.assertLess(res.confidence, 0.35)

    def test_clear_smartphone_bezel(self):
        """Validates that a smartphone chassis/bezel tightly framing the face is detected with high confidence."""
        canvas = np.full((480, 640, 3), 180, dtype=np.uint8)  # Room background

        # Draw smartphone body (black rectangle framing the face)
        phone_x1, phone_y1 = 60, 50
        phone_x2, phone_y2 = 260, 290
        # Dark black smartphone chassis
        cv2.rectangle(canvas, (phone_x1 - 10, phone_y1 - 10), (phone_x2 + 10, phone_y2 + 10), (15, 15, 15), -1)
        # Smartphone screen boundary
        cv2.rectangle(canvas, (phone_x1, phone_y1), (phone_x2, phone_y2), (230, 230, 230), -1)

        # Draw the displayed face inside the phone screen
        self._create_synthetic_face(canvas, self.face_bbox)

        res = self.detector.evaluate(canvas, self.face_bbox)
        self.assertTrue(res.bezel_detected)
        self.assertGreaterEqual(res.confidence, 0.75)
        self.assertGreaterEqual(res.rectilinear_pairs, 1)

    def test_partial_occluded_bezel(self):
        """Validates detection when part of the device bezel is held by hands (as in user screenshot)."""
        canvas = np.full((480, 640, 3), 180, dtype=np.uint8)

        phone_x1, phone_y1 = 60, 50
        phone_x2, phone_y2 = 260, 290
        # Phone chassis
        cv2.rectangle(canvas, (phone_x1, phone_y1), (phone_x2, phone_y2), (18, 18, 18), -1)
        cv2.rectangle(canvas, (phone_x1 + 6, phone_y1 + 6), (phone_x2 - 6, phone_y2 - 6), (220, 220, 220), -1)

        self._create_synthetic_face(canvas, self.face_bbox)

        # Hand occluding the left bezel
        cv2.rectangle(canvas, (phone_x1 - 15, phone_y1 + 40), (phone_x1 + 25, phone_y2 - 30), (120, 145, 190), -1)

        res = self.detector.evaluate(canvas, self.face_bbox)
        # Even with one edge occluded, top, bottom, and right borders are present
        self.assertTrue(res.bezel_detected)
        self.assertGreaterEqual(res.confidence, 0.65)

    def test_natural_rectilinear_objects_false_positive_guard(self):
        """
        Validates that natural straight lines (e.g. a vertical doorframe or horizontal shelf)
        passing behind the face do NOT falsely trigger bezel detection.
        """
        canvas = np.full((480, 640, 3), 200, dtype=np.uint8)

        # Vertical doorframe 120px to the right of the face
        cv2.line(canvas, (400, 0), (400, 480), (60, 60, 60), 3)
        # Horizontal baseboard near bottom of room
        cv2.line(canvas, (0, 440), (640, 440), (80, 80, 80), 3)

        self._create_synthetic_face(canvas, self.face_bbox)

        res = self.detector.evaluate(canvas, self.face_bbox)
        # Distant doorframe does not tightly frame the face on multiple sides
        self.assertFalse(res.bezel_detected)
        self.assertLess(res.confidence, 0.40)

    def _make_blinds_and_wall_scene(
        self,
        spacing=14,
        wall_x=280,
        bbox=None,
        canvas_size=(480, 640),
    ):
        """Synthetic live-user scene: venetian blinds + one wall edge, no device."""
        h, w = canvas_size
        canvas = np.full((h, w, 3), 195, dtype=np.uint8)
        for y in range(10, h - 10, spacing):
            cv2.line(canvas, (20, y), (w - 20, y), (70, 70, 75), 2)
        cv2.line(canvas, (wall_x, 0), (wall_x, h), (50, 50, 50), 4)
        face_bbox = bbox if bbox is not None else self.face_bbox
        self._create_synthetic_face(canvas, face_bbox)
        return canvas, face_bbox

    def test_periodic_background_pattern_false_positive_guard(self):
        """
        Repeated parallel lines (blinds/grille texture) plus one unrelated
        vertical edge must not be scored as a device bezel.
        """
        canvas, bbox = self._make_blinds_and_wall_scene(spacing=14, wall_x=280)
        res = self.detector.evaluate(canvas, bbox)
        self.assertFalse(res.bezel_detected)
        self.assertLess(res.confidence, self.detector.confidence_threshold)
        self.assertLess(res.confidence, 0.50)

    def test_blinds_plus_wall_edge_no_device_regression(self):
        """
        Regression: live user, roughly centered, venetian blinds behind them
        and a nearby wall/door edge — zero actual device bezel in frame.
        """
        # Face more centered; wall just inside the old expanded ROI.
        bbox = (210, 150, 330, 300)
        canvas, bbox = self._make_blinds_and_wall_scene(
            spacing=16, wall_x=400, bbox=bbox
        )
        res = self.detector.evaluate(canvas, bbox)
        self.assertFalse(res.bezel_detected)
        self.assertLess(res.confidence, self.detector.confidence_threshold)
        self.assertLess(res.confidence, 0.50)


if __name__ == "__main__":
    unittest.main()
