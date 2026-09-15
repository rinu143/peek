"""
Peek Face Engine - Bezel & Screen-Edge Liveness Detector
Directly mitigates screen-replay attacks by detecting high-contrast, rectilinear
bezel boundaries and device chassis framing the face at close range.
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple, List


@dataclass
class BezelDetectionResult:
    bezel_detected: bool
    confidence: float                  # 0.0 (natural background) to 1.0 (clear device bezel)
    edge_count: int = 0
    rectilinear_pairs: int = 0
    has_framing_box: bool = False
    details: str = ""


class BezelDetector:
    """
    Detects physical device bezels (smartphones, tablets, laptop screens)
    held in front of the camera framing a displayed face.
    """

    def __init__(
        self,
        expansion_margin: float = 2.2,
        canny_thresh1: int = 40,
        canny_thresh2: int = 120,
        min_line_length_ratio: float = 0.35,
        confidence_threshold: float = 0.65
    ):
        self.expansion_margin = expansion_margin
        self.canny_thresh1 = canny_thresh1
        self.canny_thresh2 = canny_thresh2
        self.min_line_length_ratio = min_line_length_ratio
        self.confidence_threshold = confidence_threshold

    def evaluate(
        self,
        frame: np.ndarray,
        bbox: Tuple[float, float, float, float]
    ) -> BezelDetectionResult:
        """
        Scans the immediate contextual margin around the face bounding box
        for rectilinear device edges and high-contrast screen bezels.
        
        Args:
            frame: Full BGR camera frame
            bbox: (x1, y1, x2, y2) of the detected face
        """
        h_img, w_img = frame.shape[:2]
        fx1, fy1, fx2, fy2 = [int(v) for v in bbox]
        fw = max(1, fx2 - fx1)
        fh = max(1, fy2 - fy1)

        # Expand region of interest to inspect the framing perimeter
        cx = (fx1 + fx2) // 2
        cy = (fy1 + fy2) // 2
        half_rw = int(fw * self.expansion_margin * 0.5)
        half_rh = int(fh * self.expansion_margin * 0.5)

        rx1 = max(0, cx - half_rw)
        ry1 = max(0, cy - half_rh)
        rx2 = min(w_img, cx + half_rw)
        ry2 = min(h_img, cy + half_rh)

        if (rx2 - rx1) < 50 or (ry2 - ry1) < 50:
            return BezelDetectionResult(bezel_detected=False, confidence=0.0, details="ROI too small")

        roi = frame[ry1:ry2, rx1:rx2]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 1.2)

        # Edge detection
        edges = cv2.Canny(blurred, self.canny_thresh1, self.canny_thresh2)

        # Mask out the inner face features (eyes, nose, mouth) so we only evaluate
        # the perimeter where a device bezel would appear
        inner_fx1 = max(0, fx1 - rx1 + int(fw * 0.15))
        inner_fy1 = max(0, fy1 - ry1 + int(fh * 0.15))
        inner_fx2 = min(roi.shape[1], fx2 - rx1 - int(fw * 0.15))
        inner_fy2 = min(roi.shape[0], fy2 - ry1 - int(fh * 0.15))
        if inner_fx2 > inner_fx1 and inner_fy2 > inner_fy1:
            edges[inner_fy1:inner_fy2, inner_fx1:inner_fx2] = 0

        # Run Hough Line Transform
        min_line_len = int(min(fw, fh) * self.min_line_length_ratio)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=28,
            minLineLength=min_line_len,
            maxLineGap=12
        )

        if lines is None or len(lines) == 0:
            return BezelDetectionResult(
                bezel_detected=False,
                confidence=0.05,
                edge_count=0,
                details="No straight edges in perimeter"
            )

        horizontal_lines = []
        vertical_lines = []

        for line in lines:
            pts = [int(v) for v in line.ravel()[:4]]
            x1, y1, x2, y2 = pts
            dx = x2 - x1
            dy = y2 - y1
            length = np.sqrt(dx * dx + dy * dy)
            if length < min_line_len:
                continue

            angle_deg = np.abs(np.degrees(np.arctan2(dy, dx)))
            # Near-horizontal: angle close to 0 or 180
            if angle_deg <= 15 or angle_deg >= 165:
                horizontal_lines.append((x1, y1, x2, y2, length))
            # Near-vertical: angle close to 90
            elif 75 <= angle_deg <= 105:
                vertical_lines.append((x1, y1, x2, y2, length))

        # Face bounds relative to ROI
        rel_fx1 = fx1 - rx1
        rel_fy1 = fy1 - ry1
        rel_fx2 = fx2 - rx1
        rel_fy2 = fy2 - ry1

        # Check for lines that physically frame the face (above, below, left, right)
        has_top_border = any(min(y1, y2) < rel_fy1 for x1, y1, x2, y2, l in horizontal_lines)
        has_bottom_border = any(max(y1, y2) > rel_fy2 for x1, y1, x2, y2, l in horizontal_lines)
        has_left_border = any(min(x1, x2) < rel_fx1 for x1, y1, x2, y2, l in vertical_lines)
        has_right_border = any(max(x1, x2) > rel_fx2 for x1, y1, x2, y2, l in vertical_lines)

        framing_sides = sum([has_top_border, has_bottom_border, has_left_border, has_right_border])

        # Right-angle corner detection between horizontal and vertical lines
        rectilinear_pairs = 0
        for hx1, hy1, hx2, hy2, hl in horizontal_lines:
            for vx1, vy1, vx2, vy2, vl in vertical_lines:
                # Check distance between line endpoints
                d11 = (hx1 - vx1)**2 + (hy1 - vy1)**2
                d12 = (hx1 - vx2)**2 + (hy1 - vy2)**2
                d21 = (hx2 - vx1)**2 + (hy2 - vy1)**2
                d22 = (hx2 - vx2)**2 + (hy2 - vy2)**2
                min_dist = np.sqrt(min(d11, d12, d21, d22))
                if min_dist <= 25.0:
                    rectilinear_pairs += 1

        # Check for rectangular contours enclosing the face
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        has_framing_box = False
        box_aspect_valid = False

        for cnt in contours:
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                bx, by, bw, bh = cv2.boundingRect(approx)
                # Check if bounding rect encloses face with margin
                if (bx <= rel_fx1 + 10 and by <= rel_fy1 + 10 and
                    bx + bw >= rel_fx2 - 10 and by + bh >= rel_fy2 - 10):
                    has_framing_box = True
                    aspect = max(bw, bh) / float(max(1, min(bw, bh)))
                    if 1.3 <= aspect <= 2.4:
                        box_aspect_valid = True
                    break

        # Edge contrast analysis: verify if borders are high-contrast plastic/metal bezel
        high_contrast_bezel = False
        if len(horizontal_lines) > 0 or len(vertical_lines) > 0:
            # Sample edge intensity gradients
            sobelx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
            sobely = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
            edge_magnitudes = np.sqrt(sobelx**2 + sobely**2)
            edge_mean = np.mean(edge_magnitudes[edges > 0]) if np.any(edges > 0) else 0.0
            if edge_mean >= 55.0:
                high_contrast_bezel = True

        # Confidence calculation
        confidence = 0.0

        if has_framing_box and box_aspect_valid:
            confidence = 0.95
        elif framing_sides >= 3 and rectilinear_pairs >= 2:
            confidence = 0.90
        elif framing_sides >= 2 and rectilinear_pairs >= 1 and high_contrast_bezel:
            confidence = 0.80
        elif framing_sides >= 3:
            confidence = 0.72
        elif (has_left_border and has_right_border) or (has_top_border and has_bottom_border):
            confidence = 0.65 if high_contrast_bezel else 0.45
        elif rectilinear_pairs >= 2 and high_contrast_bezel:
            confidence = 0.60
        else:
            confidence = min(0.35, 0.05 * framing_sides + 0.05 * rectilinear_pairs)

        bezel_detected = confidence >= self.confidence_threshold
        details = (f"Bezel: detected={bezel_detected} (conf={confidence:.2f}, "
                   f"framing={framing_sides}/4, corners={rectilinear_pairs}, box={has_framing_box})")

        return BezelDetectionResult(
            bezel_detected=bezel_detected,
            confidence=confidence,
            edge_count=len(horizontal_lines) + len(vertical_lines),
            rectilinear_pairs=rectilinear_pairs,
            has_framing_box=has_framing_box,
            details=details
        )
