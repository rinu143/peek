"""
Peek Face Engine - Rolling Temporal Verifier
Layer B: Multi-frame temporal confirmation window (M-of-N sliding window) with per-identity tracking.

NON-NEGOTIABLE SECURITY CONSTRAINT:
Face match alone can never unlock. Even when temporal verification passes 100%,
is_authorized_to_unlock remains strictly False. Liveness is an independent gate.
"""

from collections import deque, Counter
from dataclasses import dataclass
from typing import Optional, Deque, Tuple

@dataclass
class TemporalVerificationResult:
    is_temporally_confirmed: bool
    smoothed_confidence: float
    window_length: int
    positive_matches_in_window: int
    required_matches: int
    match_ratio: float
    confirmed_profile_id: Optional[str] = None
    # SECURITY CONSTRAINT #1:
    # Even 100% temporal biometric match CANNOT authorize an unlock.
    is_authorized_to_unlock: bool = False
    details: str = ""


class TemporalVerifier:
    """
    Maintains a rolling window of recent match decisions for a persistently tracked face.
    Requires consistent matching over time of the SAME profile_id rather than trusting
    a single lucky frame or mixed identities across frames.
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

        # Stores (is_match: bool, profile_id: Optional[str])
        self.history: Deque[Tuple[bool, Optional[str]]] = deque(maxlen=window_size)
        self.smoothed_score: Optional[float] = None
        self.active_track_id: Optional[int] = None

    def update(
        self,
        track_id: Optional[int],
        is_frame_match: bool,
        frame_confidence: float,
        profile_id: Optional[str] = None
    ) -> TemporalVerificationResult:
        """
        Updates the rolling verification window with the latest frame match decision.
        
        Args:
            track_id: Persistent track ID from FaceTracker.
            is_frame_match: Instantaneous match decision for current frame.
            frame_confidence: Instantaneous cosine similarity for current frame.
            profile_id: Profile ID matched for the current frame (if match).
            
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
                    confirmed_profile_id=None,
                    is_authorized_to_unlock=False,
                    details="No tracked target face"
                )

        # Update EMA smoothed confidence
        if self.smoothed_score is None:
            self.smoothed_score = frame_confidence
        else:
            self.smoothed_score = self.alpha * frame_confidence + (1.0 - self.alpha) * self.smoothed_score

        # Append latest decision and identity to sliding window
        pid = profile_id if is_frame_match else None
        self.history.append((is_frame_match, pid))

        current_len = len(self.history)

        # Count positive matches per identity
        # REQUIREMENT 5b: Confirm ONLY when >= 5 of the last 7 frames match the SAME profile_id
        match_counts: Counter = Counter()
        for is_match, matched_pid in self.history:
            if is_match and matched_pid is not None:
                match_counts[matched_pid] += 1
            elif is_match:
                match_counts["__legacy__"] += 1

        is_confirmed = False
        confirmed_profile_id = None
        best_positive_count = 0

        for candidate_pid, count in match_counts.items():
            if count > best_positive_count:
                best_positive_count = count
            if count >= self.min_matches:
                is_confirmed = True
                confirmed_profile_id = candidate_pid if candidate_pid != "__legacy__" else None
                best_positive_count = count
                break

        match_ratio = float(best_positive_count / current_len) if current_len > 0 else 0.0

        if is_confirmed:
            details = f"Verified across {best_positive_count}/{current_len} frames (score: {self.smoothed_score:.2f})"
        elif best_positive_count > 0:
            details = f"Confirming identity: {best_positive_count}/{self.min_matches} samples..."
        else:
            details = "No match in recent window"

        return TemporalVerificationResult(
            is_temporally_confirmed=is_confirmed,
            smoothed_confidence=self.smoothed_score,
            window_length=current_len,
            positive_matches_in_window=best_positive_count,
            required_matches=self.min_matches,
            match_ratio=match_ratio,
            confirmed_profile_id=confirmed_profile_id,
            is_authorized_to_unlock=False,  # Strictly False (Constraint #1)
            details=details
        )

    def reset(self):
        """Clears sliding window and score history."""
        self.history.clear()
        self.smoothed_score = None
        self.active_track_id = None
