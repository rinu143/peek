"""
Peek Face Engine - Active Challenge-Response Liveness Mode
Provides an optional interactive challenge-response verification against
pre-recorded video replay loops and physical masks.
"""

import time
import random
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class ChallengeResult:
    passed: bool
    state: str                          # "IDLE", "PENDING", "PASSED", "FAILED", "TIMEOUT"
    challenge_type: Optional[str] = None # "TURN_LEFT", "TURN_RIGHT", "TILT_UP", "BLINK"
    prompt_text: str = ""
    time_remaining: float = 0.0
    details: str = ""


CHALLENGE_PROMPTS = {
    "TURN_LEFT": "Turn your head slightly Left",
    "TURN_RIGHT": "Turn your head slightly Right",
    "TILT_UP": "Tilt your head slightly Up",
    "BLINK": "Blink your eyes",
}


class ChallengeManager:
    """
    Issues unpredictable, randomized physiological challenges (head turns, blinks)
    and validates real-time user response within a strict time limit.
    """

    def __init__(self, timeout_seconds: float = 3.5, max_frames: int = 70):
        self.timeout_seconds = timeout_seconds
        self.max_frames = max_frames
        self.available_challenges = ["TURN_LEFT", "TURN_RIGHT", "TILT_UP", "BLINK"]

        self._current_challenge: Optional[str] = None
        self._last_challenge: Optional[str] = None
        self._start_time: float = 0.0
        self._frame_count: int = 0
        self._initial_blink_count: int = 0
        self._state: str = "IDLE"
        self._wrong_direction_count: int = 0

    @property
    def is_active(self) -> bool:
        return self._state == "PENDING"

    def start_challenge(self, initial_blink_count: int = 0) -> ChallengeResult:
        """Starts a new unpredictable challenge guaranteed not to repeat the previous one."""
        choices = [c for c in self.available_challenges if c != self._last_challenge]
        self._current_challenge = random.choice(choices)
        self._last_challenge = self._current_challenge
        self._start_time = time.time()
        self._frame_count = 0
        self._initial_blink_count = initial_blink_count
        self._state = "PENDING"
        self._wrong_direction_count = 0

        prompt = CHALLENGE_PROMPTS.get(self._current_challenge, "Look at camera")
        return ChallengeResult(
            passed=False,
            state="PENDING",
            challenge_type=self._current_challenge,
            prompt_text=prompt,
            time_remaining=self.timeout_seconds,
            details=f"Challenge issued: {prompt}"
        )

    def reset(self):
        """Resets challenge state to idle."""
        self._current_challenge = None
        self._state = "IDLE"
        self._frame_count = 0
        self._wrong_direction_count = 0

    def update(
        self,
        pose_label: Optional[str],
        current_blink_count: int = 0,
        blink_detected_this_frame: bool = False
    ) -> ChallengeResult:
        """
        Evaluates the current frame against the active challenge prompt.
        """
        if self._state != "PENDING" or not self._current_challenge:
            return ChallengeResult(
                passed=(self._state == "PASSED"),
                state=self._state,
                challenge_type=self._current_challenge,
                prompt_text="",
                time_remaining=0.0,
                details="No challenge active"
            )

        self._frame_count += 1
        elapsed = time.time() - self._start_time
        remaining = max(0.0, self.timeout_seconds - elapsed)

        # Check for timeout
        if elapsed > self.timeout_seconds or self._frame_count > self.max_frames:
            self._state = "TIMEOUT"
            return ChallengeResult(
                passed=False,
                state="TIMEOUT",
                challenge_type=self._current_challenge,
                prompt_text="Challenge Timed Out",
                time_remaining=0.0,
                details=f"Failed to complete {self._current_challenge} in {self.timeout_seconds:.1f}s"
            )

        prompt = CHALLENGE_PROMPTS.get(self._current_challenge, "")

        # --- Evaluate Response ---
        if self._current_challenge == "TURN_LEFT":
            if pose_label in ("LEFT", "UPPER_LEFT", "LOWER_LEFT"):
                self._state = "PASSED"
                return ChallengeResult(
                    passed=True,
                    state="PASSED",
                    challenge_type=self._current_challenge,
                    prompt_text="✓ Verified!",
                    time_remaining=remaining,
                    details="Head turn left verified"
                )
            elif pose_label in ("RIGHT", "UPPER_RIGHT", "LOWER_RIGHT"):
                self._wrong_direction_count += 1
                if self._wrong_direction_count >= 5:
                    self._state = "FAILED"
                    return ChallengeResult(
                        passed=False,
                        state="FAILED",
                        challenge_type=self._current_challenge,
                        prompt_text="Incorrect Direction",
                        time_remaining=0.0,
                        details="User turned right when asked for left"
                    )

        elif self._current_challenge == "TURN_RIGHT":
            if pose_label in ("RIGHT", "UPPER_RIGHT", "LOWER_RIGHT"):
                self._state = "PASSED"
                return ChallengeResult(
                    passed=True,
                    state="PASSED",
                    challenge_type=self._current_challenge,
                    prompt_text="✓ Verified!",
                    time_remaining=remaining,
                    details="Head turn right verified"
                )
            elif pose_label in ("LEFT", "UPPER_LEFT", "LOWER_LEFT"):
                self._wrong_direction_count += 1
                if self._wrong_direction_count >= 5:
                    self._state = "FAILED"
                    return ChallengeResult(
                        passed=False,
                        state="FAILED",
                        challenge_type=self._current_challenge,
                        prompt_text="Incorrect Direction",
                        time_remaining=0.0,
                        details="User turned left when asked for right"
                    )

        elif self._current_challenge == "TILT_UP":
            if pose_label in ("UP", "UPPER_LEFT", "UPPER_RIGHT"):
                self._state = "PASSED"
                return ChallengeResult(
                    passed=True,
                    state="PASSED",
                    challenge_type=self._current_challenge,
                    prompt_text="✓ Verified!",
                    time_remaining=remaining,
                    details="Head tilt up verified"
                )
            elif pose_label in ("DOWN", "LOWER_LEFT", "LOWER_RIGHT"):
                self._wrong_direction_count += 1
                if self._wrong_direction_count >= 5:
                    self._state = "FAILED"
                    return ChallengeResult(
                        passed=False,
                        state="FAILED",
                        challenge_type=self._current_challenge,
                        prompt_text="Incorrect Direction",
                        time_remaining=0.0,
                        details="User tilted down when asked for up"
                    )

        elif self._current_challenge == "BLINK":
            # Check if blink count increased since challenge started
            if (current_blink_count > self._initial_blink_count) or blink_detected_this_frame:
                self._state = "PASSED"
                return ChallengeResult(
                    passed=True,
                    state="PASSED",
                    challenge_type=self._current_challenge,
                    prompt_text="✓ Verified!",
                    time_remaining=remaining,
                    details="Blink verified"
                )

        return ChallengeResult(
            passed=False,
            state="PENDING",
            challenge_type=self._current_challenge,
            prompt_text=prompt,
            time_remaining=remaining,
            details=f"Waiting for {self._current_challenge} ({remaining:.1f}s remaining)"
        )
