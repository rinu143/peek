"""
Peek Face Engine - Rolling Temporal Verifier
Layer B: Multi-frame temporal confirmation window (M-of-N sliding window).

NON-NEGOTIABLE SECURITY CONSTRAINT:
Face match alone can never unlock. Even when temporal verification passes 100%,
is_authorized_to_unlock remains strictly False. Liveness is an independent gate.
"""

from collections import deque
from dataclasses import dataclass
from typing import Optional, Deque

@dataclass
class TemporalVerificationResult:
    is_temporally_confirmed: bool
    smoothed_confidence: float
    window_length: int
    positive_matches_in_window: int
    required_matches: int
    match_ratio: float
    # SECURITY CONSTRAINT #1:
    # Even 100% temporal biometric match CANNOT authorize an unlock.
    is_authorized_to_unlock: bool = False
    details: str = ""


class TemporalVerifier:
    """
    Maintains a rolling window of recent match decisions for a persistently tracked face.
    Requires consistent matching over time rather than trusting a single lucky frame.
    """

    def __init__(
        self,
        window_size: int = 7,
        min_matches_required: int = 5,
        ema_alpha: float = 0.35
    ):
        self.window_size = window_size
        self.min_matches = min_matches_required
        self.alpha = ema_alpha

        self.history: Deque[bool] = deque(maxlen=window_size)
        self.smoothed_score: Optional[float] = None
        self.active_track_id: Optional[int] = None

    def update(
        self,
        track_id: Optional[int],
        is_frame_match: bool,
        frame_confidence: float
    ) -> TemporalVerificationResult:
        """
        Updates the rolling verification window with the latest frame match decision.
        
        Args:
            track_id: Persistent track ID from FaceTracker.
            is_frame_match: Instantaneous match decision for current frame.
            frame_confidence: Instantaneous cosine similarity for current frame.
            
        Returns:
            TemporalVerificationResult detailing rolling window state and confirmation.
        """
        # Reset window immediately if tracked target changes or is lost
        if track_id is None or track_id != self.active_track_id:
            self.reset()
            self.active_track_id = track_id
            if track_id is None:
                return TemporalVerificationResult(
                    is_temporally_confirmed=False,
                    smoothed_confidence=0.0,
                    window_length=0,
                    positive_matches_in_window=0,
                    required_matches=self.min_matches,
                    match_ratio=0.0,
                    is_authorized_to_unlock=False,
                    details="No tracked target face"
                )

        # Update EMA smoothed confidence
        if self.smoothed_score is None:
            self.smoothed_score = frame_confidence
        else:
            self.smoothed_score = self.alpha * frame_confidence + (1.0 - self.alpha) * self.smoothed_score

        # Append latest decision to sliding window
        self.history.append(is_frame_match)

        positive_count = sum(1 for m in self.history if m)
        current_len = len(self.history)
        match_ratio = float(positive_count / current_len) if current_len > 0 else 0.0

        # Confirmed iff at least min_matches positive frames in window
        is_confirmed = positive_count >= self.min_matches

        if is_confirmed:
            details = f"Verified across {positive_count}/{current_len} frames (score: {self.smoothed_score:.2f})"
        elif positive_count > 0:
            details = f"Confirming identity: {positive_count}/{self.min_matches} samples..."
        else:
            details = "No match in recent window"

        return TemporalVerificationResult(
            is_temporally_confirmed=is_confirmed,
            smoothed_confidence=self.smoothed_score,
            window_length=current_len,
            positive_matches_in_window=positive_count,
            required_matches=self.min_matches,
            match_ratio=match_ratio,
            is_authorized_to_unlock=False,  # Strictly False (Constraint #1)
            details=details
        )

    def reset(self):
        """Clears sliding window and score history."""
        self.history.clear()
        self.smoothed_score = None
        self.active_track_id = None
