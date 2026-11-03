"""
Unit and Integration Tests for Pipe Server Nonce Echoing, Attempt Throttling, and First-Instance Hardening.
Requirements:
1. First pipe instance: refuses to start if pipe name is already taken.
2. Nonce binding: echoes random challenge nonce in every state/result message.
3. Attempt throttling: 5 failures within 10 minutes replies LOCKED_OUT for 5 minutes; resets on success.
"""

import unittest
import time
import uuid
import numpy as np
import win32pipe
import win32file
import win32security
import win32process
import win32con

from engine.ipc.pipe_server import PipeServer, UserLockoutTracker, DEFAULT_SDDL
from engine.ipc.pipe_client import PipeClient
from engine.ipc.protocol import (
    MSG_START_AUTH,
    make_start_auth,
    MSG_AUTHENTICATED,
    MSG_AUTH_FAILED,
    MSG_TIMEOUT,
    MSG_ERROR,
    MSG_LOCKED_OUT,
    MSG_CAMERA_STARTING,
    REASON_SPOOF_DETECTED,
    REASON_NO_PROFILE,
    REASON_LOCKED_OUT,
)
from engine.pipeline.face_engine import EngineFrameResult
from engine.liveness.liveness_detector import LivenessResult
from storage.template_store import PeekProfile


class DummyCamera:
    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.started = False

    def start(self) -> bool:
        self.started = self.succeed
        return self.succeed

    def stop(self):
        self.started = False

    def read_frame(self):
        return True, np.zeros((480, 640, 3), dtype=np.uint8)


class ControllableFaceEngine:
    def __init__(self, fail_with_spoof: bool = False, succeed_on_frame: int = 1):
        self.fail_with_spoof = fail_with_spoof
        self.succeed_on_frame = succeed_on_frame
        self.frames = 0
        self.active_profile = PeekProfile(
            profile_id="test_profile_id",
            display_name="Test User",
            user_sid="S-1-5-21-99999-1001",
            enabled=True
        )

    def reload_active_profile(self):
        pass

    def process_frame(self, frame) -> EngineFrameResult:
        self.frames += 1
        if self.fail_with_spoof:
            return EngineFrameResult(
                has_face=True,
                liveness_result=LivenessResult(
                    is_live=False,
                    fused_score=0.1,
                    motion_score=0.1,
                    texture_score=0.1,
                    eye_score=0.1,
                    state="SPOOF_DETECTED",
                    spoof_reason="Print photo attack"
                ),
                is_authorized_to_unlock=False
            )

        if self.frames >= self.succeed_on_frame:
            return EngineFrameResult(
                has_face=True,
                is_authorized_to_unlock=True,
                matched_profile_id=self.active_profile.profile_id if self.active_profile else "test_profile_id",
                matched_profile_name=self.active_profile.display_name if self.active_profile else "Test User"
            )

        return EngineFrameResult(
            has_face=False,
            is_authorized_to_unlock=False
        )


def get_current_user_sddl():
    try:
        token = win32security.OpenProcessToken(win32process.GetCurrentProcess(), win32con.TOKEN_QUERY)
        tok_sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        sddl = f"D:(A;;GA;;;SY)(A;;GA;;;{win32security.ConvertSidToStringSid(tok_sid)})"
        token.Close()
        return sddl
    except Exception:
        return "D:(A;;GA;;;WD)"


class TestUserLockoutTracker(unittest.TestCase):
    def test_lockout_trigger_after_5_failures(self):
        tracker = UserLockoutTracker(max_failures=5, window_seconds=600.0, lockout_duration_seconds=300.0)
        user = "S-1-5-21-TEST-1001"

        # First 4 failures do not lock out
        for _ in range(4):
            self.assertFalse(tracker.record_failure(user))
            locked, rem = tracker.is_locked_out(user)
            self.assertFalse(locked)
            self.assertEqual(rem, 0.0)

        # 5th failure triggers lockout
        self.assertTrue(tracker.record_failure(user))
        locked, rem = tracker.is_locked_out(user)
        self.assertTrue(locked)
        self.assertGreater(rem, 290.0)
        self.assertLessEqual(rem, 300.0)

    def test_lockout_reset_on_success(self):
        tracker = UserLockoutTracker(max_failures=5, window_seconds=600.0, lockout_duration_seconds=300.0)
        user = "S-1-5-21-TEST-1001"

        for _ in range(4):
            tracker.record_failure(user)

        # Successful auth resets failure counter
        tracker.record_success(user)

        # 4 more failures still do not lock out (counter was reset)
        for _ in range(4):
            self.assertFalse(tracker.record_failure(user))
            locked, _ = tracker.is_locked_out(user)
            self.assertFalse(locked)

    def test_window_sliding_expiry(self):
        tracker = UserLockoutTracker(max_failures=5, window_seconds=1.0, lockout_duration_seconds=2.0)
        user = "S-1-5-21-TEST-1001"

        # 4 failures
        for _ in range(4):
            tracker.record_failure(user)

        # Wait for window to expire (>1s)
        time.sleep(1.1)

        # 1 new failure should NOT trigger lockout (previous 4 rolled off)
        self.assertFalse(tracker.record_failure(user))
        locked, _ = tracker.is_locked_out(user)
        self.assertFalse(locked)


class TestPipeServerThrottlingAndNonce(unittest.TestCase):
    def setUp(self):
        self.pipe_name = rf"\\.\pipe\PeekTestThrottling_{uuid.uuid4().hex[:8]}"
        self.sddl = get_current_user_sddl()

    def test_first_pipe_instance_rejects_squatter(self):
        """FILE_FLAG_FIRST_PIPE_INSTANCE causes second server to fail if pipe already exists."""
        server1 = PipeServer(pipe_name=self.pipe_name, sddl=self.sddl)
        server1.start()

        try:
            server2 = PipeServer(pipe_name=self.pipe_name, sddl=self.sddl)
            with self.assertRaises(RuntimeError):
                server2.start()
        finally:
            server1.stop()

    def test_nonce_echoed_in_state_and_auth_messages(self):
        """Engine echoes challenge nonce in intermediate states and terminal AUTHENTICATED message."""
        camera = DummyCamera(succeed=True)
        engine = ControllableFaceEngine(fail_with_spoof=False, succeed_on_frame=2)
        server = PipeServer(pipe_name=self.pipe_name, face_engine=engine, camera=camera, sddl=self.sddl)
        server.start()

        client = PipeClient(pipe_name=self.pipe_name)
        try:
            self.assertTrue(client.connect(timeout_seconds=3.0))
            client.read_message(timeout_seconds=2.0)  # ENGINE_READY banner

            test_nonce = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
            start_msg = make_start_auth(timeout_seconds=5.0, nonce=test_nonce)
            client.send_message(start_msg)

            received_nonces = []
            final_type = None

            for _ in range(15):
                msg = client.read_message(timeout_seconds=1.0)
                if not msg:
                    continue
                mtype = msg.get("type")
                if "nonce" in msg:
                    received_nonces.append((mtype, msg["nonce"]))
                if mtype in (MSG_AUTHENTICATED, MSG_AUTH_FAILED, MSG_TIMEOUT, MSG_ERROR):
                    final_type = mtype
                    break

            self.assertEqual(final_type, MSG_AUTHENTICATED)
            self.assertGreater(len(received_nonces), 0)
            # All messages with nonce must echo the exact challenge nonce
            for mtype, n in received_nonces:
                self.assertEqual(n, test_nonce, f"Nonce mismatch on message {mtype}")
        finally:
            client.close()
            server.stop()

    def test_five_failures_trigger_locked_out_response(self):
        """5 failed sessions result in subsequent START_AUTH replying LOCKED_OUT without starting camera."""
        camera = DummyCamera(succeed=True)
        engine = ControllableFaceEngine(fail_with_spoof=True)  # Always fails with spoof
        server = PipeServer(pipe_name=self.pipe_name, face_engine=engine, camera=camera, sddl=self.sddl)
        server.start()

        target_sid = "S-1-5-21-THROTTLE-USER-1"

        try:
            # Run 5 failed attempts
            for i in range(5):
                client = PipeClient(pipe_name=self.pipe_name)
                self.assertTrue(client.connect(timeout_seconds=3.0))
                client.read_message(timeout_seconds=2.0)  # ready

                nonce = f"attempt_nonce_{i:02d}_1234567890abcdef"
                start_msg = make_start_auth(timeout_seconds=3.0, nonce=nonce, user_sid=target_sid)
                client.send_message(start_msg)

                # Read until terminal message
                done = False
                for _ in range(10):
                    msg = client.read_message(timeout_seconds=1.0)
                    if msg and msg.get("type") == MSG_AUTH_FAILED:
                        self.assertEqual(msg.get("nonce"), nonce)
                        done = True
                        break
                self.assertTrue(done, f"Attempt {i+1} did not end in AUTH_FAILED")
                client.close()

            # 6th attempt: Must be LOCKED_OUT
            client6 = PipeClient(pipe_name=self.pipe_name)
            self.assertTrue(client6.connect(timeout_seconds=3.0))
            client6.read_message(timeout_seconds=2.0)

            nonce6 = "sixth_attempt_nonce_1234567890ab"
            start_msg6 = make_start_auth(timeout_seconds=3.0, nonce=nonce6, user_sid=target_sid)
            client6.send_message(start_msg6)

            resp = client6.read_message(timeout_seconds=2.0)
            self.assertIsNotNone(resp)
            self.assertEqual(resp.get("type"), MSG_LOCKED_OUT)
            self.assertEqual(resp.get("reason_code"), REASON_LOCKED_OUT)
            self.assertEqual(resp.get("nonce"), nonce6)
            self.assertIn("Sign in with your PIN or password", resp.get("detail", ""))
            self.assertGreater(resp.get("retry_after", 0), 0)
            client6.close()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
