"""
Peek Face Engine - Quality Checker
Layer D: Pre-enrollment quality evaluation to reject blurry, poorly lit, too-small, or multi-face frames.
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional, List
from engine.detection.scrfd_detector import FaceDetection

@dataclass
class QualityCheckResult:
    passed: bool
    composite_score: float  # [0.0 - 1.0]
    sharpness: float
    brightness: float
    rejection_reason: Optional[str] = None


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
        all_detections: List[FaceDetection]
    ) -> QualityCheckResult:
        """
        Runs comprehensive quality validation on detected face in frame.
        
        Args:
            frame: Full BGR camera image.
            all_detections: List of all detected faces in the frame.
            
        Returns:
            QualityCheckResult with pass/fail and metrics.
        """
        # 1. Single-face check
        if len(all_detections) == 0:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=0.0,
                rejection_reason="No face detected. Please look into camera."
            )

        if len(all_detections) > 1:
            return QualityCheckResult(
                passed=False,
                composite_score=0.0,
                sharpness=0.0,
                brightness=0.0,
                rejection_reason="Multiple faces visible. Only one person must be present."
            )

        det = all_detections[0]
        h_img, w_img = frame.shape[:2]
        x1, y1, x2, y2 = det.bbox.astype(int)

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
