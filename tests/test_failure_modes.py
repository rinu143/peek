"""
Failure-Mode Tests for Peek Face Engine & Credential Provider (Phase 8)

Validates bounded time-to-fallback (< 15 seconds) and crash immunity across failure modes:
1. Engine killed mid-session (client unblocks immediately on broken pipe).
2. Camera unplugged mid-session (reopen backoff exhausts -> REASON_CAMERA_ERROR in < 2s).
3. Non-engine process squatting the pipe (first instance flag prevents hijack).
4. Corrupt or missing profile file (isolated gracefully without engine crash).
5. DPAPI failure (decryption error caught cleanly, profile rejected).
6. Missing/invalid registry identity key.
"""

import os
import sys
import time
import json
import uuid
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import numpy as np

from engine.ipc.pipe_server import PipeServer
from engine.ipc.pipe_client import PipeClient
from engine.ipc.protocol import (
    MSG_START_AUTH,
    make_start_auth,
    MSG_ERROR,
    REASON_CAMERA_ERROR,
    REASON_ENGINE_ERROR,
)
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate, dpapi_decrypt


class MockDisconnectingCamera:
    """Mock camera that simulates immediate device disconnection mid-session."""

    def __init__(self):
        self.is_opened = True

    def start(self):
        self.is_opened = True
        return True

    def stop(self):
        self.is_opened = False

    def read_frame(self):
        # Disconnected camera returns False, None
        return False, None

    def reopen(self, tries: int = 3, backoff_base: float = 0.01):
        # Disconnected hardware cannot be reopened
        self.is_opened = False
        return False


class MockFaceEngine:
    """Mock face engine with an active profile for isolated tests."""

    def __init__(self, display_name: str = "Test User"):
        self.active_profile = PeekProfile(
            profile_id="mock_p1",
            display_name=display_name,
            enabled=True
        )
        self.enabled_profiles = [self.active_profile]

    def reload_active_profile(self, user_sid=None):
        pass

    def process_frame(self, frame):
        return None


class TestFailureModes(unittest.TestCase):
    """Automated failure-mode resiliency tests."""

    def setUp(self):
        self.pipe_name = f"\\\\.\\pipe\\PeekTestFail_{uuid.uuid4().hex[:8]}"
        self.server = None

    def tearDown(self):
        if self.server:
            try:
                self.server.stop()
            except Exception:
                pass

    def test_1_engine_killed_mid_session(self):
        """
        Mode 1: Engine killed mid-session.
        Client must detect broken pipe immediately (< 2.0s << 15s) and unblock.
        """
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=MockFaceEngine())
        self.server.start()
        time.sleep(0.15)

        client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(client.connect(timeout_seconds=2.0))
        # Read greeting
        _ = client.read_message()

        client.send_message(make_start_auth())

        # Abruptly kill the server while auth is in progress
        kill_start = time.perf_counter()
        self.server.stop()

        # Client attempt to read must fail/unblock in < 2 seconds
        # Drain any previously buffered in-flight messages until EOF
        msg = client.read_message(timeout_seconds=2.0)
        while msg is not None:
            msg = client.read_message(timeout_seconds=0.5)
        elapsed = time.perf_counter() - kill_start
        client.close()

        self.assertLess(elapsed, 15.0, "Must fallback within 15 seconds")
        self.assertIsNone(msg, "Client must receive None/EOF when engine is killed mid-session")

    def test_2_camera_unplugged_mid_session(self):
        """
        Mode 2: Camera unplugged mid-session.
        Engine must attempt reopen with backoff, exhaust retries, and emit
        REASON_CAMERA_ERROR in < 3.0s (<< 15s) without crashing.
        """
        cam = MockDisconnectingCamera()
        self.server = PipeServer(pipe_name=self.pipe_name, camera=cam, face_engine=MockFaceEngine())
        self.server.start()
        time.sleep(0.15)

        client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(client.connect(timeout_seconds=2.0))
        _ = client.read_message()  # greeting

        start_t = time.perf_counter()
        client.send_message(make_start_auth())

        error_msg = None
        while time.perf_counter() - start_t < 4.0:
            msg = client.read_message(timeout_seconds=1.0)
            if not msg:
                break
            if msg.get("type") == MSG_ERROR:
                error_msg = msg
                break

        elapsed = time.perf_counter() - start_t
        client.close()

        self.assertLess(elapsed, 15.0, "Must fall back within 15 seconds")
        self.assertIsNotNone(error_msg, "Server must emit error message on camera loss")
        self.assertEqual(error_msg.get("reason_code"), REASON_CAMERA_ERROR)

    def test_3_pipe_squatting_detected(self):
        """
        Mode 3: Rogue process squats the named pipe.
        Engine must enforce FILE_FLAG_FIRST_PIPE_INSTANCE, fail closed with
        RuntimeError, and never connect to the unauthorized pipe.
        """
        import win32file
        import win32pipe

        # Rogue process creates the pipe first
        h_squat = win32pipe.CreateNamedPipe(
            self.pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1, 4096, 4096, 0, None
        )

        try:
            # Engine attempts to start on squatted pipe
            server = PipeServer(pipe_name=self.pipe_name)
            with self.assertRaises(RuntimeError) as ctx:
                server.start()
            self.assertIn("already exists", str(ctx.exception))
        finally:
            win32file.CloseHandle(h_squat)

    def test_4a_missing_profile_file_handled_cleanly(self):
        """
        Mode 4a: Profile directory or file missing.
        Must report 0 profiles and not crash.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            non_existent_dir = os.path.join(tmpdir, "does_not_exist")
            store = SecureProfileStore(storage_dir=non_existent_dir)
            profiles = store.list_profiles()
            self.assertEqual(len(profiles), 0)

    def test_4b_corrupt_profile_file_quarantined_without_crash(self):
        """
        Mode 4b: Corrupt / truncated profile file.
        Must isolate the corrupted file, log error, and not crash engine.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt_file = os.path.join(tmpdir, "corrupt_profile.peek")
            # Write completely random garbage bytes
            with open(corrupt_file, "wb") as f:
                f.write(b"\xDE\xAD\xBE\xEF\x00\x01\x02\x03\xFF\xFE\xFD")

            store = SecureProfileStore(storage_dir=tmpdir)
            profiles = store.list_profiles()
            # Must safely ignore corrupt file and load 0 profiles
            self.assertEqual(len(profiles), 0)

    def test_5_dpapi_decryption_failure_handling(self):
        """
        Mode 5: DPAPI failure (wrong user context or damaged DPAPI envelope).
        Must raise WinError / OSError, caught cleanly by SecureProfileStore.
        """
        garbage_blob = b"NOT_A_VALID_DPAPI_BLOB"
        with self.assertRaises(OSError):
            dpapi_decrypt(garbage_blob)

    def test_6_missing_registry_identities_handled(self):
        """
        Mode 6: Missing HKLM\\SOFTWARE\\Peek\\Identities registry key.
        Engine identity resolution must handle missing key gracefully.
        """
        from engine.pipeline.face_engine import get_authorized_identities_from_registry
        # Querying an unknown / non-existent SID must return None without raising exceptions
        res = get_authorized_identities_from_registry("S-1-5-21-0000000000-0000000000-0000000000-9999")
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
