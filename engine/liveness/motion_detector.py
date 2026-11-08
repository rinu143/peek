"""
Peek Face Engine - Motion Parallax & Micro-Movement Liveness Detector
Analyzes 3D facial landmark dynamics over a rolling window to detect:
1. Static / frozen frames (printed photographs on stands, static digital images).
2. Rigid 2D planar motion (photographs or mobile screens waved in front of the lens).
3. Natural physiological micro-movements and 3D perspective foreshortening.
"""

import numpy as np
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Tuple, Deque


@dataclass
class MotionLivenessResult:
    is_live: bool
    score: float                      # 0.0 (synthetic/spoof) to 1.0 (natural 3D human)
    is_frozen: bool = False           # Static image / no micro-movement
    is_rigid_planar: bool = False     # Flat 2D planar movement (waved photo)
    lacks_parallax: bool = False      # Hand-held 2D replay with tremor lacking 3D relative depth
    spoof_reason: Optional[str] = None
    details: str = ""


class MotionParallaxDetector:
    """
    Detects natural physiological micro-movements and 3D facial depth parallax
    from 5 facial landmarks across rolling frames.
    """

    def __init__(
        self,
        window_size: int = 12,
        min_frames_required: int = 6,
        frozen_std_threshold: float = 0.12,
        rigid_ratio_std_threshold: float = 0.0022
    ):
        self.window_size = window_size
        self.min_frames_required = min_frames_required
        self.frozen_std_threshold = frozen_std_threshold
        self.rigid_ratio_std_threshold = rigid_ratio_std_threshold

        self._history_landmarks: Deque[np.ndarray] = deque(maxlen=window_size)
        self._history_centroids: Deque[Tuple[float, float]] = deque(maxlen=window_size)
        self._history_geom_ratios: Deque[float] = deque(maxlen=window_size)
        self._history_mouth_ratios: Deque[float] = deque(maxlen=window_size)
        self._current_track_id: Optional[int] = None

    def reset(self):
        """Clears rolling historical buffers on target switch or track loss."""
        self._history_landmarks.clear()
        self._history_centroids.clear()
        self._history_geom_ratios.clear()
        self._history_mouth_ratios.clear()
        self._current_track_id = None

    def update(
        self,
        landmarks: np.ndarray,
        bbox: Tuple[float, float, float, float],
        track_id: Optional[int] = None
    ) -> MotionLivenessResult:
        """
        Updates the motion detector with the current frame's face geometry.
        
        Args:
            landmarks: 5x2 array [left_eye, right_eye, nose, left_mouth, right_mouth]
            bbox: (x1, y1, x2, y2)
            track_id: ID of the currently tracked face
        """
        # If track changed, reset history immediately
        if track_id is not None and track_id != self._current_track_id:
            self.reset()
            self._current_track_id = track_id

        pts = np.asarray(landmarks, dtype=np.float32)
        if pts.shape != (5, 2):
            return MotionLivenessResult(
                is_live=False,
                score=0.0,
                details="Invalid landmark geometry"
            )

        left_eye = pts[0]
        right_eye = pts[1]
        nose = pts[2]
        left_mouth = pts[3]
        right_mouth = pts[4]

        eye_midpoint = (left_eye + right_eye) / 2.0
        mouth_midpoint = (left_mouth + right_mouth) / 2.0

        inter_eye_dist = float(np.linalg.norm(right_eye - left_eye))
        mouth_width = float(np.linalg.norm(right_mouth - left_mouth))
        nose_eye_dist = float(np.linalg.norm(nose - eye_midpoint))
        nose_mouth_dist = float(np.linalg.norm(nose - mouth_midpoint))

        # Geometric ratio rho: distance from eye-line to nose / inter-ocular distance
        geom_ratio = nose_eye_dist / max(inter_eye_dist, 1e-4)
        mouth_ratio = nose_mouth_dist / max(mouth_width, 1e-4)

        # Centroid of face bounding box
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0

        self._history_landmarks.append(pts)
        self._history_centroids.append((cx, cy))
        self._history_geom_ratios.append(geom_ratio)
        self._history_mouth_ratios.append(mouth_ratio)

        # Require a minimum frame observation window before making definitive judgment
        if len(self._history_landmarks) < self.min_frames_required:
            progress = len(self._history_landmarks) / float(self.min_frames_required)
            return MotionLivenessResult(
                is_live=False,
                score=0.5 * progress,
                details=f"Observing motion ({len(self._history_landmarks)}/{self.min_frames_required} frames)"
            )

        # --- Check 1: Freeze / Zero Micro-Movement Detection ---
        centroids_arr = np.array(self._history_centroids, dtype=np.float32)
        centroid_std = float(np.mean(np.std(centroids_arr, axis=0)))

        all_lms = np.array(self._history_landmarks, dtype=np.float32) # (N, 5, 2)
        lm_std = float(np.mean(np.std(all_lms, axis=0)))

        if centroid_std < self.frozen_std_threshold and lm_std < (self.frozen_std_threshold * 0.8):
            return MotionLivenessResult(
                is_live=False,
                score=0.05,
                is_frozen=True,
                spoof_reason="STATIC_PHOTO",
                details=f"Frozen frame detected (centroid std: {centroid_std:.3f}px, lm std: {lm_std:.3f}px)"
            )

        # --- Check 2: 3D Parallax vs 2D Hand-Held Replay (Tremor & Planar Check) ---
        ratio_arr = np.array(self._history_geom_ratios, dtype=np.float32)
        ratio_std = float(np.std(ratio_arr))
        mouth_ratio_arr = np.array(self._history_mouth_ratios, dtype=np.float32)
        mouth_ratio_std = float(np.std(mouth_ratio_arr))

        # In a 2D flat photo/screen moved by hand tremor or waved:
        # centroid_std is in [0.4, 30] px, but relative ratios have near-zero variance
        # because flat planar translation maintains constant landmark aspect ratios.
        if centroid_std >= 0.40 and ratio_std < self.rigid_ratio_std_threshold and mouth_ratio_std < self.rigid_ratio_std_threshold:
            is_large_translation = (centroid_std > 3.0)
            return MotionLivenessResult(
                is_live=False,
                score=0.12,
                lacks_parallax=True,
                is_rigid_planar=is_large_translation,
                spoof_reason="RIGID_PLANAR_MOTION" if is_large_translation else "LACKS_PARALLAX",
                details=(f"2D flat motion lacking 3D parallax detected "
                         f"(motion: {centroid_std:.2f}px, ratio std: {ratio_std:.5f})")
            )

        # --- Check 3: Rigid 2D Planar Motion (Waved Photo / Phone with larger variance) ---
        if centroid_std > 3.0 and ratio_std < (self.rigid_ratio_std_threshold * 1.5):
            return MotionLivenessResult(
                is_live=False,
                score=0.15,
                is_rigid_planar=True,
                lacks_parallax=True,
                spoof_reason="RIGID_PLANAR_MOTION",
                details=f"Rigid 2D planar motion detected (translation: {centroid_std:.1f}px, ratio std: {ratio_std:.5f})"
            )

        # --- Check 4: Natural Living Micro-Movement & 3D Depth Score ---
        if centroid_std < 0.25:
            jitter_score = centroid_std / 0.25
        elif centroid_std <= 4.0:
            jitter_score = 1.0
        elif centroid_std <= 15.0:
            jitter_score = max(0.5, 1.0 - ((centroid_std - 4.0) / 22.0))
        else:
            jitter_score = 0.3

        # 3D Parallax ratio score
        if ratio_std < self.rigid_ratio_std_threshold:
            parallax_score = ratio_std / self.rigid_ratio_std_threshold
        elif ratio_std <= 0.08:
            parallax_score = 1.0
        else:
            parallax_score = max(0.6, 1.0 - ((ratio_std - 0.08) / 0.15))

        motion_score = 0.5 * jitter_score + 0.5 * parallax_score
        motion_score = float(np.clip(motion_score, 0.0, 1.0))

        is_live = motion_score >= 0.60
        details = f"Natural 3D micro-movement confirmed (jitter: {centroid_std:.2f}px, parallax: {ratio_std:.4f})"

        return MotionLivenessResult(
            is_live=is_live,
            score=motion_score,
            details=details
        )
