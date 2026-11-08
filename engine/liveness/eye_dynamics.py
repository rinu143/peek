"""
Peek Face Engine - Eye Dynamics & Blink Liveness Detector
Monitors eye region gradient energy, intensity variance, and blink transitions
over a rolling temporal window to identify genuine living eyes.
"""

import cv2
import numpy as np
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Tuple, Deque


@dataclass
class EyeDynamicsResult:
    is_live: bool
    dynamics_score: float             # 0.0 to 1.0 physiological dynamic variation
    openness_score: float             # Estimated openness of eyes (0.0 closed to 1.0 open)
    blink_detected: bool = False      # Whether at least one blink event occurred
    blink_count: int = 0              # Total blinks in current tracking session
    details: str = ""


class EyeDynamicsDetector:
    """
    Analyzes left and right eye regions extracted from 5 facial landmarks
    to track eye openness, physiological micro-variations, and blink signatures.
    """

    def __init__(
        self,
        history_size: int = 20,
        blink_dip_ratio: float = 0.65,
        min_blink_frames: int = 1,
        max_blink_frames: int = 5
    ):
        self.history_size = history_size
        self.blink_dip_ratio = blink_dip_ratio
        self.min_blink_frames = min_blink_frames
        self.max_blink_frames = max_blink_frames

        self._history_openness: Deque[float] = deque(maxlen=history_size)
        self._current_track_id: Optional[int] = None
        self._blink_count: int = 0
        self._dip_active_frames: int = 0
        self._baseline_openness: float = 0.0

    def reset(self):
        """Resets tracking state and history."""
        self._history_openness.clear()
        self._current_track_id = None
        self._blink_count = 0
        self._dip_active_frames = 0
        self._baseline_openness = 0.0

    def _extract_eye_metric(self, gray_frame: np.ndarray, eye_center: np.ndarray, patch_w: int, patch_h: int) -> float:
        """Computes vertical gradient energy and contrast in the eye patch."""
        cx, cy = int(round(eye_center[0])), int(round(eye_center[1]))
        half_w = max(4, patch_w // 2)
        half_h = max(3, patch_h // 2)

        h, w = gray_frame.shape[:2]
        x1 = max(0, cx - half_w)
        y1 = max(0, cy - half_h)
        x2 = min(w, cx + half_w)
        y2 = min(h, cy + half_h)

        if (x2 - x1) < 6 or (y2 - y1) < 4:
            return 0.0

        patch = gray_frame[y1:y2, x1:x2]
        
        # Vertical gradient (Sobel Y): captures upper and lower eyelid edges against dark pupil
        sobely = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
        grad_energy = float(np.mean(np.abs(sobely)))
        
        # Intensity standard deviation: contrast between pupil, iris, and sclera
        std_intensity = float(np.std(patch))

        return 0.6 * grad_energy + 0.4 * std_intensity

    def update(
        self,
        frame: np.ndarray,
        landmarks: np.ndarray,
        track_id: Optional[int] = None
    ) -> EyeDynamicsResult:
        """
        Processes the current frame's eye landmarks.
        
        Args:
            frame: BGR frame
            landmarks: 5x2 array [left_eye, right_eye, nose, left_mouth, right_mouth]
            track_id: Current face track ID
        """
        if track_id is not None and track_id != self._current_track_id:
            self.reset()
            self._current_track_id = track_id

        pts = np.asarray(landmarks, dtype=np.float32)
        if pts.shape != (5, 2):
            return EyeDynamicsResult(is_live=False, dynamics_score=0.0, openness_score=0.0, details="Invalid landmarks")

        left_eye = pts[0]
        right_eye = pts[1]

        inter_eye_dist = float(np.linalg.norm(right_eye - left_eye))
        patch_w = int(max(10, inter_eye_dist * 0.38))
        patch_h = int(max(8, inter_eye_dist * 0.26))

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame

        metric_left = self._extract_eye_metric(gray, left_eye, patch_w, patch_h)
        metric_right = self._extract_eye_metric(gray, right_eye, patch_w, patch_h)
        raw_openness = (metric_left + metric_right) / 2.0

        self._history_openness.append(raw_openness)

        # Baseline openness estimation (rolling 90th percentile)
        if len(self._history_openness) >= 5:
            self._baseline_openness = float(np.percentile(self._history_openness, 85))
        else:
            self._baseline_openness = max(raw_openness, 1e-3)

        # Normalized openness relative to baseline
        norm_openness = raw_openness / max(self._baseline_openness, 1e-3)
        norm_openness = float(np.clip(norm_openness, 0.0, 1.2))

        # --- Blink Transition Detection ---
        blink_detected_this_frame = False
        is_dipping = (norm_openness < self.blink_dip_ratio)

        if is_dipping:
            self._dip_active_frames += 1
        else:
            # If we just exited a dip lasting 1 to 5 frames, register as a blink!
            if self.min_blink_frames <= self._dip_active_frames <= self.max_blink_frames:
                self._blink_count += 1
                blink_detected_this_frame = True
            self._dip_active_frames = 0

        # Physiological micro-dynamics score:
        # In a living human, eye metrics exhibit continuous micro-fluctuation over time.
        # In a photo, eye metrics remain 100% constant over time.
        if len(self._history_openness) >= 6:
            openness_std = float(np.std(self._history_openness))
            # Reward subtle variations (std between 0.3 and 4.0)
            if openness_std < 0.10:
                dynamics_score = openness_std / 0.10 * 0.3  # Very static (photo)
            elif openness_std <= 5.0:
                dynamics_score = 0.70 + min(0.30, (openness_std / 5.0) * 0.30)
            else:
                dynamics_score = 0.65
        else:
            dynamics_score = 0.50

        # If a verified blink occurred, boost dynamics score
        if self._blink_count > 0:
            dynamics_score = max(dynamics_score, 0.95)

        is_live = (dynamics_score >= 0.60) or (self._blink_count > 0)
        details = f"Eye Dynamics: openness={norm_openness:.2f}, blinks={self._blink_count}, dyn={dynamics_score:.2f}"

        return EyeDynamicsResult(
            is_live=is_live,
            dynamics_score=dynamics_score,
            openness_score=norm_openness,
            blink_detected=(self._blink_count > 0 or blink_detected_this_frame),
            blink_count=self._blink_count,
            details=details
        )
