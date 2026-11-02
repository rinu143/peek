"""
Peek Face Engine - Quality Checker
Layer D: Pre-enrollment quality evaluation to reject blurry, poorly lit, too-small, or multi-face frames.
Provides smart primary enrollee resolution to disambiguate the user from background bystanders.
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Tuple
from engine.detection.scrfd_detector import FaceDetection

@dataclass
class QualityCheckResult:
    passed: bool
    composite_score: float  # [0.0 - 1.0]
    sharpness: float
    brightness: float
    rejection_reason: Optional[str] = None


def resolve_primary_face(
    all_detections: List[FaceDetection],
    frame_shape: Tuple[int, int]
) -> Tuple[Optional[FaceDetection], List[FaceDetection]]:
    """
    Identifies the primary enrolling user from detected faces based on
    prominence scoring (area and proximity to center of aperture).
    Returns (primary_face, secondary_faces).
    """
    if not all_detections:
        return None, []
    if len(all_detections) == 1:
        return all_detections[0], []

    h, w = frame_shape[:2]
    cx_frame, cy_frame = w / 2.0, h / 2.0
    diag = np.hypot(cx_frame, cy_frame) + 1e-6

    def score_detection(d: FaceDetection) -> float:
        bx1, by1, bx2, by2 = d.bbox
        fcx = (bx1 + bx2) / 2.0
        fcy = (by1 + by2) / 2.0
        dist_norm = np.hypot(fcx - cx_frame, fcy - cy_frame) / diag
        center_factor = max(0.2, 1.0 - 0.6 * dist_norm)
        return float(d.area * center_factor)

    scored = sorted(all_detections, key=score_detection, reverse=True)
    return scored[0], scored[1:]


class FaceQualityChecker:
    """
    Evaluates face image quality prior to candidate acceptance during enrollment.
    """

    def __init__(
        self,
        min_sharpness: float = 20.0,
        min_brightness: float = 25.0,
        max_brightness: float = 235.0,
        min_face_size: int = 50,
        min_contrast: float = 8.0
    ):
        self.min_sharpness = min_sharpness
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.min_face_size = min_face_size
        self.min_contrast = min_contrast

    def evaluate(
        self,
        frame: np.ndarray,
        all_detections: List[FaceDetection],
        allow_background_bystanders: bool = False,
        max_bystander_area_ratio: float = 0.40
    ) -> QualityCheckResult:
        """
        Runs comprehensive quality validation on detected face in frame.
        
        Args:
            frame: Full BGR camera image.
            all_detections: List of all detected faces in the frame.
            allow_background_bystanders: If True, tolerates distant/smaller background faces.
            max_bystander_area_ratio: Max allowed area ratio of bystander vs primary face (default 0.40).
            
        Returns:
            QualityCheckResult with pass/fail and metrics.
        """
        # 1. Single-face check & bystander resolution
        if len(all_detections) == 0:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=0.0,
                rejection_reason="No face detected. Please look into camera."
            )

        if len(all_detections) > 1:
            if not allow_background_bystanders:
                return QualityCheckResult(
                    passed=False,
                    composite_score=0.0,
                    sharpness=0.0,
                    brightness=0.0,
                    rejection_reason="Multiple faces visible. Only one person must be present."
                )

            primary_det, secondary_dets = resolve_primary_face(all_detections, frame.shape)
            if primary_det is None:
                return QualityCheckResult(
                    passed=False,
                    composite_score=0.0,
                    sharpness=0.0,
                    brightness=0.0,
                    rejection_reason="No face detected. Please look into camera."
                )

            # Check if any secondary face is too prominent (competing foreground person)
            primary_area = max(1.0, primary_det.area)
            for sec in secondary_dets:
                ratio = sec.area / primary_area
                if ratio > max_bystander_area_ratio:
                    return QualityCheckResult(
                        passed=False,
                        composite_score=0.0,
                        sharpness=0.0,
                        brightness=0.0,
                        rejection_reason="Multiple faces visible. Only one person must be present."
                    )
            det = primary_det
        else:
            det = all_detections[0]

        h_img, w_img = frame.shape[:2]
        x1, y1, x2, y2 = np.asarray(det.bbox, dtype=int)

        # 2. Face size check
        w_face = x2 - x1
        h_face = y2 - y1
        if w_face < self.min_face_size or h_face < self.min_face_size:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=0.0,
                rejection_reason="Face is too far. Please move closer."
            )

        # 3. Boundary margin check (avoid partial/clipped faces)
        if x1 < 5 or y1 < 5 or x2 > w_img - 5 or y2 > h_img - 5:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=0.0,
                rejection_reason="Face is cut off at the edge. Please center face."
            )

        # Crop face for pixel-level quality checks
        face_crop = frame[max(0, y1):min(h_img, y2), max(0, x1):min(w_img, x2)]
        if face_crop.size == 0:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=0.0,
                rejection_reason="Invalid face crop."
            )

        gray_face = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)

        # 4. Illumination & Contrast
        mean_brightness = float(np.mean(gray_face))
        contrast = float(np.std(gray_face))

        if mean_brightness < self.min_brightness:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=mean_brightness,
                rejection_reason="Lighting too dark. Increase ambient light."
            )

        if mean_brightness > self.max_brightness:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=mean_brightness,
                rejection_reason="Overexposed lighting. Reduce glare or bright background."
            )

        if contrast < self.min_contrast:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=mean_brightness,
                rejection_reason="Low contrast face. Improve frontal lighting."
            )

        # 5. Sharpness (Laplacian variance)
        lap_var = float(cv2.Laplacian(gray_face, cv2.CV_64F).var())
        if lap_var < self.min_sharpness:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=lap_var,
                brightness=mean_brightness,
                rejection_reason="Frame is blurry. Please hold steady."
            )

        # Compute normalized composite score [0.0 - 1.0]
        norm_sharpness = min(1.0, lap_var / 300.0)
        norm_brightness = 1.0 - abs(mean_brightness - 128.0) / 128.0
        norm_confidence = min(1.0, det.confidence)
        
        composite_score = float(0.4 * norm_sharpness + 0.3 * norm_brightness + 0.3 * norm_confidence)

        return QualityCheckResult(
            passed=True,
            composite_score=composite_score,
            sharpness=lap_var,
            brightness=mean_brightness,
            rejection_reason=None
        )
