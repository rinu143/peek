"""
Peek Face Engine - Threshold Calibrator
Layer B: Analyzes cosine similarity distributions between genuine and impostor pairs
to calibrate optimal matching thresholds and evaluate operating points (FAR vs FRR).
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Dict

@dataclass
class CalibrationReport:
    recommended_threshold: float
    genuine_mean: float
    genuine_std: float
    impostor_mean: float
    impostor_std: float
    far_at_recommended: float
    frr_at_recommended: float
    threshold_metrics: Dict[float, Dict[str, float]]


class ThresholdCalibrator:
    """
    Evaluates ArcFace similarity score distributions to determine optimal matching thresholds.
    """

    @staticmethod
    def compute_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
        return float(np.dot(v1, v2))

    def evaluate(
        self,
        genuine_pairs: List[Tuple[np.ndarray, np.ndarray]],
        impostor_pairs: List[Tuple[np.ndarray, np.ndarray]],
        target_max_far: float = 0.01  # Target max 1% False Accept Rate
    ) -> CalibrationReport:
        """
        Evaluates genuine and impostor pairs to establish calibrated threshold.
        """
        genuine_scores = [self.compute_similarity(a, b) for a, b in genuine_pairs]
        impostor_scores = [self.compute_similarity(a, b) for a, b in impostor_pairs]

        gen_mean = float(np.mean(genuine_scores)) if genuine_scores else 0.0
        gen_std = float(np.std(genuine_scores)) if genuine_scores else 0.0
        imp_mean = float(np.mean(impostor_scores)) if impostor_scores else 0.0
        imp_std = float(np.std(impostor_scores)) if impostor_scores else 0.0

        test_thresholds = [0.35, 0.40, 0.45, 0.48, 0.50, 0.55, 0.60]
        metrics = {}
        recommended = 0.48

        for thr in test_thresholds:
            # FAR: fraction of impostor scores >= thr
            far = float(np.mean([1.0 if s >= thr else 0.0 for s in impostor_scores])) if impostor_scores else 0.0
            # FRR: fraction of genuine scores < thr
            frr = float(np.mean([1.0 if s < thr else 0.0 for s in genuine_scores])) if genuine_scores else 0.0

            metrics[thr] = {"FAR": far, "FRR": frr}
            if far <= target_max_far and frr < 0.10:
                recommended = thr

        return CalibrationReport(
            recommended_threshold=recommended,
            genuine_mean=gen_mean,
            genuine_std=gen_std,
            impostor_mean=imp_mean,
            impostor_std=imp_std,
            far_at_recommended=metrics.get(recommended, {}).get("FAR", 0.0),
            frr_at_recommended=metrics.get(recommended, {}).get("FRR", 0.0),
            threshold_metrics=metrics
        )
