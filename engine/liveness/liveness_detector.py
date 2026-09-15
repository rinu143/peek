"""
Peek Face Engine - Liveness & Anti-Spoofing Evaluator
Orchestrates multi-cue RGB liveness verification:
- Motion Parallax & Micro-Movement
- Visual Texture & Screen Moiré Analysis
- Eye Dynamics & Blink Signatures

NON-NEGOTIABLE SECURITY CONSTRAINTS:
Biometric match alone can NEVER unlock. is_live is an independent, mandatory gate.
Only when is_live is True and face match is verified will an unlock be authorized.
"""

import numpy as np
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple, Deque

from engine.liveness.motion_detector import MotionParallaxDetector, MotionLivenessResult
from engine.liveness.texture_checker import TextureChecker, TextureLivenessResult
from engine.liveness.eye_dynamics import EyeDynamicsDetector, EyeDynamicsResult


@dataclass
class LivenessResult:
    is_live: bool
    state: str                          # "CHECKING", "LIVE", "SPOOF_DETECTED"
    fused_score: float                  # 0.0 to 1.0
    motion_score: float
    texture_score: float
    eye_score: float
    blink_detected: bool = False
    spoof_reason: Optional[str] = None  # "STATIC_PHOTO", "RIGID_PLANAR_MOTION", "SCREEN_MOIRE", etc.
    details: str = ""


class LivenessDetector:
    """
    Unified Liveness and Anti-Spoofing Detector.
    Evaluates multi-frame passive RGB signals across rolling frames to ensure
    the target is a physical, living human in 3D space.
    """

    def __init__(
        self,
        min_frames_to_confirm: int = 8,
        liveness_threshold: float = 0.58,
        w_motion: float = 0.40,
        w_texture: float = 0.35,
        w_eye: float = 0.25
    ):
        self.min_frames_to_confirm = min_frames_to_confirm
        self.liveness_threshold = liveness_threshold
        self.w_motion = w_motion
        self.w_texture = w_texture
        self.w_eye = w_eye

        self.motion_detector = MotionParallaxDetector(window_size=12, min_frames_required=6, frozen_std_threshold=0.12)
        self.texture_checker = TextureChecker(moire_peak_threshold=100.0, specular_ratio_threshold=0.20)
        self.eye_dynamics = EyeDynamicsDetector(history_size=20)

        self._frame_count: int = 0
        self._current_track_id: Optional[int] = None
        self._recent_scores: Deque[float] = deque(maxlen=10)
        self._consecutive_spoof_frames: int = 0
        self._active_spoof_reason: Optional[str] = None

    def reset(self):
        """Resets all internal sub-detector states on track loss or target switch."""
        self.motion_detector.reset()
        self.eye_dynamics.reset()
        self._frame_count = 0
        self._current_track_id = None
        self._recent_scores.clear()
        self._consecutive_spoof_frames = 0
        self._active_spoof_reason = None

    def update(
        self,
        frame: np.ndarray,
        bbox: Tuple[float, float, float, float],
        landmarks: np.ndarray,
        track_id: Optional[int] = None
    ) -> LivenessResult:
        """
        Processes one camera frame for the tracked face and updates liveness decision.
        """
        # If target changed, reset state immediately
        if track_id is not None and track_id != self._current_track_id:
            self.reset()
            self._current_track_id = track_id

        self._frame_count += 1

        # 1. Evaluate Motion Parallax & Micro-Movement
        motion_res = self.motion_detector.update(landmarks, bbox, track_id)

        # 2. Evaluate Texture & Screen Moiré
        texture_res = self.texture_checker.evaluate(frame, bbox)

        # 3. Evaluate Eye Dynamics & Blinks
        eye_res = self.eye_dynamics.update(frame, landmarks, track_id)

        # Check for immediate spoof triggers
        spoof_reason = None
        if motion_res.is_frozen:
            spoof_reason = motion_res.spoof_reason or "STATIC_PHOTO"
        elif motion_res.is_rigid_planar:
            spoof_reason = motion_res.spoof_reason or "RIGID_PLANAR_MOTION"
        elif texture_res.moire_detected:
            spoof_reason = texture_res.spoof_reason or "SCREEN_MOIRE"
        elif texture_res.specular_glare_detected:
            spoof_reason = texture_res.spoof_reason or "SPECULAR_GLARE"

        if spoof_reason is not None:
            self._consecutive_spoof_frames += 1
            self._active_spoof_reason = spoof_reason
        else:
            self._consecutive_spoof_frames = max(0, self._consecutive_spoof_frames - 1)

        # Multi-cue fused score computation
        fused = (
            self.w_motion * motion_res.score +
            self.w_texture * texture_res.score +
            self.w_eye * eye_res.dynamics_score
        )
        fused = float(np.clip(fused, 0.0, 1.0))
        self._recent_scores.append(fused)

        smoothed_score = float(np.mean(self._recent_scores))

        # Handle persistent spoof condition (requires at least 4 consecutive frames flagging spoof)
        if self._consecutive_spoof_frames >= 4:
            return LivenessResult(
                is_live=False,
                state="SPOOF_DETECTED",
                fused_score=0.10,
                motion_score=motion_res.score,
                texture_score=texture_res.score,
                eye_score=eye_res.dynamics_score,
                blink_detected=eye_res.blink_detected,
                spoof_reason=self._active_spoof_reason,
                details=f"Spoof detected: {self._active_spoof_reason} (motion: {motion_res.score:.2f}, text: {texture_res.score:.2f})"
            )

        # Observation ramp-up phase
        if self._frame_count < self.min_frames_to_confirm:
            pct = int((self._frame_count / float(self.min_frames_to_confirm)) * 100)
            return LivenessResult(
                is_live=False,
                state="CHECKING",
                fused_score=smoothed_score,
                motion_score=motion_res.score,
                texture_score=texture_res.score,
                eye_score=eye_res.dynamics_score,
                blink_detected=eye_res.blink_detected,
                details=f"Evaluating liveness ({pct}%)..."
            )

        # After ramp-up: verify smoothed score against threshold
        is_live = smoothed_score >= self.liveness_threshold
        state = "LIVE" if is_live else "CHECKING"

        # If after 18+ frames score remains persistently low (< 0.35), flag spoof
        if self._frame_count >= 18 and smoothed_score < 0.35:
            state = "SPOOF_DETECTED"
            is_live = False
            spoof_reason = "UNNATURAL_PASSIVE_CUES"

        details = f"Liveness: {state} (score={smoothed_score:.2f}, mot={motion_res.score:.2f}, tex={texture_res.score:.2f}, eye={eye_res.dynamics_score:.2f})"

        return LivenessResult(
            is_live=is_live,
            state=state,
            fused_score=smoothed_score,
            motion_score=motion_res.score,
            texture_score=texture_res.score,
            eye_score=eye_res.dynamics_score,
            blink_detected=eye_res.blink_detected,
            spoof_reason=spoof_reason,
            details=details
        )
