"""
Tests for Active Challenge-Response Liveness Mode
Validates:
1. Randomized, non-repeating physiological challenge generation.
2. Correct user compliance verification within time budget (head turns, tilt, blink).
3. Wrong direction rejection after repeated contrary motion.
4. Timeout expiration handling when user fails to comply in time.
5. State transitions and reset behavior.
"""

import unittest
import time
from engine.liveness.challenge import ChallengeManager, ChallengeResult


class TestChallengeResponse(unittest.TestCase):

    def setUp(self):
        self.manager = ChallengeManager(timeout_seconds=2.0, max_frames=30)

    def test_challenge_initialization_and_prompt(self):
        """Validates that starting a challenge produces a valid pending challenge and prompt."""
        res = self.manager.start_challenge()
        self.assertFalse(res.passed)
        self.assertEqual(res.state, "PENDING")
        self.assertIn(res.challenge_type, ["TURN_LEFT", "TURN_RIGHT", "TILT_UP", "BLINK"])
        self.assertTrue(len(res.prompt_text) > 0)
        self.assertTrue(self.manager.is_active)

    def test_non_repeating_consecutive_challenges(self):
        """Validates that consecutive challenges never repeat the immediately preceding one."""
        last_type = None
        for _ in range(20):
            res = self.manager.start_challenge()
            self.assertNotEqual(res.challenge_type, last_type)
            last_type = res.challenge_type

    def test_turn_left_success(self):
        """Validates head turn left passes when asked for left."""
        self.manager.start_challenge()
        self.manager._current_challenge = "TURN_LEFT"

        # Initially neutral
        res = self.manager.update(pose_label="CENTER")
        self.assertFalse(res.passed)
        self.assertEqual(res.state, "PENDING")

        # Now turn left
        res = self.manager.update(pose_label="LEFT")
        self.assertTrue(res.passed)
        self.assertEqual(res.state, "PASSED")

    def test_turn_right_success(self):
        """Validates head turn right passes when asked for right."""
        self.manager.start_challenge()
        self.manager._current_challenge = "TURN_RIGHT"

        res = self.manager.update(pose_label="RIGHT")
        self.assertTrue(res.passed)
        self.assertEqual(res.state, "PASSED")

    def test_tilt_up_success(self):
        """Validates head tilt up passes when asked for up."""
        self.manager.start_challenge()
        self.manager._current_challenge = "TILT_UP"

        res = self.manager.update(pose_label="UP")
        self.assertTrue(res.passed)
        self.assertEqual(res.state, "PASSED")

    def test_blink_success(self):
        """Validates blink passes when eye dynamics record a blink."""
        self.manager.start_challenge(initial_blink_count=2)
        self.manager._current_challenge = "BLINK"

        # Frame with same blink count
        res = self.manager.update(pose_label="CENTER", current_blink_count=2, blink_detected_this_frame=False)
        self.assertFalse(res.passed)

        # Frame with incremented blink count
        res = self.manager.update(pose_label="CENTER", current_blink_count=3, blink_detected_this_frame=True)
        self.assertTrue(res.passed)
        self.assertEqual(res.state, "PASSED")

    def test_wrong_direction_failure(self):
        """Validates that turning in the opposite direction repeatedly fails the challenge."""
        self.manager.start_challenge()
        self.manager._current_challenge = "TURN_LEFT"

        for _ in range(4):
            res = self.manager.update(pose_label="RIGHT")
            self.assertFalse(res.passed)
            self.assertEqual(res.state, "PENDING")

        # 5th frame of opposite direction -> FAIL
        res = self.manager.update(pose_label="RIGHT")
        self.assertFalse(res.passed)
        self.assertEqual(res.state, "FAILED")

    def test_timeout_expiration(self):
        """Validates that exceeding max frames or time results in TIMEOUT state."""
        manager = ChallengeManager(timeout_seconds=0.05, max_frames=5)
        manager.start_challenge()

        time.sleep(0.06)
        res = manager.update(pose_label="CENTER")
        self.assertFalse(res.passed)
        self.assertEqual(res.state, "TIMEOUT")
        self.assertIn("Timed Out", res.prompt_text)

    def test_reset_behavior(self):
        """Validates that reset clears active challenge and returns to IDLE."""
        self.manager.start_challenge()
        self.assertTrue(self.manager.is_active)
        self.manager.reset()
        self.assertFalse(self.manager.is_active)
        res = self.manager.update(pose_label="LEFT")
        self.assertEqual(res.state, "IDLE")

    def test_passed_state_is_single_use(self):
        """
        Validates that a PASSED state becomes CONSUMED after consume_pass() is called,
        preventing reuse of the same challenge pass across multiple frames.
        """
        self.manager.start_challenge()
        self.manager._current_challenge = "TURN_LEFT"

        # Pass the challenge
        res = self.manager.update(pose_label="LEFT")
        self.assertTrue(res.passed)
        self.assertEqual(res.state, "PASSED")

        # Consume the pass (simulating authorization)
        self.manager.consume_pass()

        # Next update should return CONSUMED state with passed=False
        res = self.manager.update(pose_label="LEFT")
        self.assertFalse(res.passed)
        self.assertEqual(res.state, "CONSUMED")
        self.assertIn("already used", res.details)

    def test_consumed_state_blocks_reuse(self):
        """
        Validates that once in CONSUMED state, the challenge cannot be reused
        until reset() is called.
        """
        self.manager.start_challenge()
        self.manager._current_challenge = "TURN_LEFT"

        # Pass and consume
        res = self.manager.update(pose_label="LEFT")
        self.assertTrue(res.passed)
        self.manager.consume_pass()

        # Multiple subsequent updates should all return CONSUMED
        for _ in range(5):
            res = self.manager.update(pose_label="LEFT")
            self.assertFalse(res.passed)
            self.assertEqual(res.state, "CONSUMED")

        # Reset should clear to IDLE
        self.manager.reset()
        res = self.manager.update(pose_label="LEFT")
        self.assertEqual(res.state, "IDLE")

    def test_blink_bias_preference(self):
        """
        Validates that when bias_blink=True, BLINK is chosen significantly more often
        than when bias_blink=False, while still respecting the non-repeat-previous-challenge guarantee.
        """
        # Test with bias_blink=True
        blink_count_biased = 0
        total_trials = 100

        for _ in range(total_trials):
            res = self.manager.start_challenge(bias_blink=True)
            if res.challenge_type == "BLINK":
                blink_count_biased += 1

        blink_ratio_biased = blink_count_biased / total_trials

        # Test with bias_blink=False (reset manager first)
        self.manager.reset()
        blink_count_unbiased = 0

        for _ in range(total_trials):
            res = self.manager.start_challenge(bias_blink=False)
            if res.challenge_type == "BLINK":
                blink_count_unbiased += 1

        blink_ratio_unbiased = blink_count_unbiased / total_trials

        # Biased mode should have significantly higher blink ratio
        self.assertGreater(blink_ratio_biased, blink_ratio_unbiased * 1.5,
                         f"Biased blink ratio {blink_ratio_biased:.2f} should be > 1.5x unbiased {blink_ratio_unbiased:.2f}")

        # Biased mode should still have reasonable blink rate (>40% accounting for non-repeat)
        self.assertGreater(blink_ratio_biased, 0.4, f"BLINK ratio {blink_ratio_biased:.2f} should be > 0.4")

    def test_no_blink_bias_when_false(self):
        """
        Validates that when bias_blink=False, challenges are distributed
        approximately evenly among available options.
        """
        from collections import Counter
        challenge_counts = Counter()
        total_trials = 100

        for _ in range(total_trials):
            res = self.manager.start_challenge(bias_blink=False)
            challenge_counts[res.challenge_type] += 1

        # Each challenge should appear roughly equally (15-35% each for 4 options)
        for challenge_type, count in challenge_counts.items():
            ratio = count / total_trials
            self.assertGreater(ratio, 0.15, f"{challenge_type} ratio {ratio:.2f} should be > 0.15")
            self.assertLess(ratio, 0.35, f"{challenge_type} ratio {ratio:.2f} should be < 0.35")


if __name__ == "__main__":
    unittest.main()
