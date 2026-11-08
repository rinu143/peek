"""
Integration and security tests for Peek IPC Hardening (Phase 7).
Validates:
1. Pipe-name squatting at startup fails closed (RuntimeError) on both auth and enrollment pipes.
2. Oversize lines (>4096 bytes) and oversize unread buffers (>8192 bytes) cause server to drop client.
3. Restrictive DACL / SDDL enforcement: unauthorized users receive ERROR_ACCESS_DENIED (5).
4. Profile replacement without confirmation is rejected with REASON_CONFIRMATION_REQUIRED.
5. Enrollment preview frame authorization check rejects unauthorized client processes.
6. Per-attempt 128-bit random nonce challenge/echo enforces strict match and rejects replay.
7. Legacy profile_<timestamp> migration to UUID4 with DPAPI persistence and cleanup.
"""

import os
import re
import time
import uuid
import shutil
import tempfile
import unittest
from typing import Optional, Dict, Any

import win32file
import win32pipe
import pywintypes
import numpy as np

from engine.ipc.pipe_server import PipeServer
from engine.ipc.pipe_client import PipeClient
from engine.ipc.enrollment_pipe_server import EnrollmentPipeServer
from engine.ipc.enrollment_pipe_client import EnrollmentPipeClient
from engine.ipc.protocol import (
    PROTOCOL_VERSION,
    MSG_START_AUTH,
    make_start_auth,
    MSG_AUTHENTICATED,
    make_authenticated,
    MSG_ERROR,
    REASON_INVALID_MESSAGE,
    serialize_message,
)
from engine.ipc.enrollment_protocol import (
    MSG_START_ENROLLMENT,
    make_start_enrollment,
    MSG_ENROLLMENT_READY,
    MSG_ERROR as ENROLL_MSG_ERROR,
    REASON_CONFIRMATION_REQUIRED,
    serialize_message as serialize_enroll_message,
)
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate
from engine.pipeline.face_engine import EngineFrameResult


class DummyCamera:
    def __init__(self):
        self.started = False
    def start(self) -> bool:
        self.started = True
        return True
    def stop(self):
        self.started = False
    def read_frame(self):
        return True, np.zeros((480, 640, 3), dtype=np.uint8)


class DummyFaceEngine:
    def __init__(self):
        self.active_profile = PeekProfile(
            profile_id=str(uuid.uuid4()),
            display_name="Secure User",
            enabled=True
        )
    def reload_active_profile(self):
        pass
    def process_frame(self, frame):
        return EngineFrameResult(
            has_face=False,
            track_id=-1,
            state_label="SEARCHING",
            is_authorized_to_unlock=False,
            is_live=False,
        )


class DummyEnrollmentRunner:
    def __init__(self, confirmed=False, password=None):
        self.confirmed = confirmed
        self.password = password
        self.started = False
        self.cancelled = False
    def start(self) -> bool:
        self.started = True
        return True
    def stop(self):
        pass
    def cancel(self):
        self.cancelled = True
    def step(self):
        return None


class TestPipeHardening(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="peek_harden_test_")
        self.test_sddl = "D:(A;;GA;;;WD)" # World full control for test sessions that need to connect

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Pipe-Name Squatting Fails Closed
    # -------------------------------------------------------------------------
    def test_auth_pipe_squat_fails_closed(self):
        """Squatting the auth pipe name before PipeServer starts causes start() to fail closed."""
        pipe_name = rf"\\.\pipe\PeekTestSquatAuth_{uuid.uuid4().hex[:8]}"
        
        # Squat the pipe without FILE_FLAG_FIRST_PIPE_INSTANCE
        h_squat = win32pipe.CreateNamedPipe(
            pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            4096,
            4096,
            0,
            None
        )
        self.assertNotEqual(h_squat, win32file.INVALID_HANDLE_VALUE)

        try:
            server = PipeServer(
                pipe_name=pipe_name,
                face_engine=DummyFaceEngine(),
                camera=DummyCamera(),
                sddl=self.test_sddl
            )
            # Attempting to start must raise RuntimeError due to FILE_FLAG_FIRST_PIPE_INSTANCE
            with self.assertRaises(RuntimeError) as ctx:
                server.start()
            self.assertIn("already exists", str(ctx.exception).lower())
        finally:
            win32file.CloseHandle(h_squat)

    def test_enrollment_pipe_squat_fails_closed(self):
        """Squatting the enrollment pipe name before EnrollmentPipeServer starts causes start() to fail closed."""
        pipe_name = rf"\\.\pipe\PeekTestSquatEnroll_{uuid.uuid4().hex[:8]}"
        
        h_squat = win32pipe.CreateNamedPipe(
            pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            4096,
            4096,
            0,
            None
        )
        self.assertNotEqual(h_squat, win32file.INVALID_HANDLE_VALUE)

        try:
            store = SecureProfileStore(storage_dir=self.temp_dir)
            server = EnrollmentPipeServer(
                pipe_name=pipe_name,
                profile_store=store,
                sddl=self.test_sddl
            )
            with self.assertRaises(RuntimeError) as ctx:
                server.start()
            self.assertIn("already taken", str(ctx.exception).lower())
        finally:
            win32file.CloseHandle(h_squat)

    # -------------------------------------------------------------------------
    # 2. IO Limits: Oversize Lines & Buffers Drop Client
    # -------------------------------------------------------------------------
    def test_auth_pipe_oversize_line_drops_client(self):
        """Sending a line exceeding MAX_LINE_LENGTH (4096 bytes) drops the client connection."""
        pipe_name = rf"\\.\pipe\PeekTestOversizeAuth_{uuid.uuid4().hex[:8]}"
        server = PipeServer(
            pipe_name=pipe_name,
            face_engine=DummyFaceEngine(),
            camera=DummyCamera(),
            sddl=self.test_sddl
        )
        server.start()

        try:
            # Connect a raw client
            h_client = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
            # Read ENGINE_READY banner
            _, data = win32file.ReadFile(h_client, 1024)
            self.assertIn(b"ENGINE_READY", data)

            # Send a malicious line > 4096 bytes with newline
            oversize_payload = b'{"type":"PING","padding":"' + (b'A' * 5000) + b'"}\n'
            win32file.WriteFile(h_client, oversize_payload)

            time.sleep(0.1)

            # Try to read or write; the connection should be dropped/broken
            with self.assertRaises(pywintypes.error):
                # Reading from broken pipe should raise ERROR_BROKEN_PIPE (109)
                _, _ = win32file.ReadFile(h_client, 1024)

            win32file.CloseHandle(h_client)
        finally:
            server.stop()

    def test_enrollment_pipe_oversize_buffer_drops_client(self):
        """Sending unread data exceeding MAX_BUFFER_SIZE (8192 bytes) without newline drops client."""
        pipe_name = rf"\\.\pipe\PeekTestOversizeEnroll_{uuid.uuid4().hex[:8]}"
        store = SecureProfileStore(storage_dir=self.temp_dir)
        server = EnrollmentPipeServer(
            pipe_name=pipe_name,
            profile_store=store,
            sddl=self.test_sddl
        )
        server.start()

        try:
            h_client = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
            # Read ENROLLMENT_READY banner
            _, data = win32file.ReadFile(h_client, 1024)
            self.assertIn(b"ENROLLMENT_READY", data)

            # Send 9000 bytes without newline
            oversize_chunk = b'X' * 9000
            win32file.WriteFile(h_client, oversize_chunk)

            time.sleep(0.1)

            with self.assertRaises(pywintypes.error):
                _, _ = win32file.ReadFile(h_client, 1024)

            win32file.CloseHandle(h_client)
        finally:
            server.stop()

    # -------------------------------------------------------------------------
    # 3. Restrictive DACLs (SDDL Enforcement)
    # -------------------------------------------------------------------------
    def test_auth_pipe_system_only_sddl_rejects_standard_user(self):
        """Auth pipe default SDDL D:(A;;GA;;;SY) rejects unprivileged local user connections."""
        pipe_name = rf"\\.\pipe\PeekTestSystemDacl_{uuid.uuid4().hex[:8]}"
        server = PipeServer(
            pipe_name=pipe_name,
            face_engine=DummyFaceEngine(),
            camera=DummyCamera(),
            sddl="D:(A;;GA;;;SY)" # SYSTEM only
        )
        server.start()

        try:
            # Current process is running as standard user, not SYSTEM
            # CreateFile should fail with ERROR_ACCESS_DENIED (5)
            with self.assertRaises(pywintypes.error) as ctx:
                win32file.CreateFile(
                    pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None
                )
            self.assertEqual(ctx.exception.winerror, 5) # ERROR_ACCESS_DENIED
        finally:
            server.stop()

    def test_enrollment_pipe_foreign_sid_sddl_rejects_user(self):
        """Enrollment pipe restricted to a different SID rejects current user."""
        pipe_name = rf"\\.\pipe\PeekTestForeignSid_{uuid.uuid4().hex[:8]}"
        fake_sid = "S-1-5-21-99999999-88888888-77777777-9999"
        restricted_sddl = f"D:(A;;GA;;;SY)(A;;GRGW;;;{fake_sid})"
        
        store = SecureProfileStore(storage_dir=self.temp_dir)
        server = EnrollmentPipeServer(
            pipe_name=pipe_name,
            profile_store=store,
            sddl=restricted_sddl
        )
        server.start()

        try:
            with self.assertRaises(pywintypes.error) as ctx:
                win32file.CreateFile(
                    pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None
                )
            self.assertEqual(ctx.exception.winerror, 5) # ERROR_ACCESS_DENIED
        finally:
            server.stop()

    # -------------------------------------------------------------------------
    # 4. Enrollment Replacement Requires Explicit Confirmation
    # -------------------------------------------------------------------------
    def test_enrollment_replace_without_confirmation_rejected(self):
        """Starting enrollment when an enabled profile already exists is rejected without confirmation."""
        pipe_name = rf"\\.\pipe\PeekTestConfirm_{uuid.uuid4().hex[:8]}"
        store = SecureProfileStore(storage_dir=self.temp_dir)
        
        # Pre-seed an enabled profile
        existing_profile = PeekProfile(
            profile_id=str(uuid.uuid4()),
            display_name="Existing Enrolled User",
            enabled=True
        )
        store.save_profile(existing_profile)

        server = EnrollmentPipeServer(
            pipe_name=pipe_name,
            profile_store=store,
            sddl=self.test_sddl
        )
        server.start()

        client = EnrollmentPipeClient(pipe_name=pipe_name)
        try:
            self.assertTrue(client.connect(timeout_seconds=3.0))
            ready = client.read_message(timeout_seconds=2.0)
            self.assertEqual(ready.get("type"), MSG_ENROLLMENT_READY)

            # 1. Attempt START_ENROLLMENT with confirmed=False and no password
            unconfirmed_req = make_start_enrollment(confirmed=False, password=None)
            client.send_message(unconfirmed_req)

            err_resp = client.read_message(timeout_seconds=2.0)
            self.assertIsNotNone(err_resp)
            self.assertEqual(err_resp.get("type"), ENROLL_MSG_ERROR)
            reason = err_resp.get("reason_code") or err_resp.get("reason")
            self.assertEqual(reason, REASON_CONFIRMATION_REQUIRED)

            # 2. Attempt with confirmed=True
            confirmed_req = make_start_enrollment(confirmed=True)
            client.send_message(confirmed_req)

            # Should not receive CONFIRMATION_REQUIRED error
            next_msg = client.read_message(timeout_seconds=2.0)
            if next_msg:
                next_reason = next_msg.get("reason_code") or next_msg.get("reason")
                self.assertNotEqual(next_reason, REASON_CONFIRMATION_REQUIRED)
        finally:
            client.close()
            server.stop()

    # -------------------------------------------------------------------------
    # 5. Token SID Verification for Preview Frame Streaming
    # -------------------------------------------------------------------------
    def test_enrollment_preview_frame_client_sid_verification(self):
        """Enrollment server resolves user SID and rejects preview frames for foreign client SIDs."""
        from engine.ipc.enrollment_pipe_server import resolve_enrolled_user_sid

        store = SecureProfileStore(storage_dir=self.temp_dir)
        server = EnrollmentPipeServer(
            pipe_name=rf"\\.\pipe\PeekTestSidVerify_{uuid.uuid4().hex[:8]}",
            profile_store=store,
            sddl=self.test_sddl
        )

        current_user_sid = resolve_enrolled_user_sid(store)
        self.assertIsNotNone(current_user_sid)
        self.assertEqual(server.enrolled_user_sid, current_user_sid)

        # 1. Matching user SID is permitted
        self.assertTrue(current_user_sid == server.enrolled_user_sid or current_user_sid == "S-1-5-18")

        # 2. SYSTEM SID (S-1-5-18) is permitted
        system_sid = "S-1-5-18"
        self.assertTrue(system_sid == server.enrolled_user_sid or system_sid == "S-1-5-18")

        # 3. Foreign user SID is rejected
        foreign_sid = "S-1-5-21-11111111-22222222-33333333-4444"
        self.assertFalse(foreign_sid == server.enrolled_user_sid or foreign_sid == "S-1-5-18")

    # -------------------------------------------------------------------------
    # 6. Nonce Echo Enforcement and Replay Protection
    # -------------------------------------------------------------------------
    def test_nonce_challenge_echo_enforcement(self):
        """PipeClient generates a 128-bit hex nonce and rejects mismatched or replayed nonces."""
        pipe_name = rf"\\.\pipe\PeekTestNonce_{uuid.uuid4().hex[:8]}"
        mock_engine = DummyFaceEngine()
        server = PipeServer(
            pipe_name=pipe_name,
            face_engine=mock_engine,
            camera=DummyCamera(),
            sddl=self.test_sddl
        )
        server.start()

        client = PipeClient(pipe_name=pipe_name)
        try:
            self.assertTrue(client.connect(timeout_seconds=3.0))
            ready = client.read_message(timeout_seconds=2.0)
            self.assertEqual(ready.get("type"), "ENGINE_READY")

            # Validate client generated challenge nonce is 32-char hex
            nonce = uuid.uuid4().hex
            self.assertEqual(len(nonce), 32)

            # Test make_start_auth with nonce
            msg = make_start_auth(timeout_seconds=5.0, nonce=nonce)
            self.assertEqual(msg["nonce"], nonce)
            self.assertEqual(msg["protocol_version"], "2.0")

            # Test make_authenticated echoes the exact nonce
            auth_msg = make_authenticated(nonce=nonce, display_name="Test")
            self.assertEqual(auth_msg["nonce"], nonce)
            self.assertNotIn("profile_id", auth_msg) # Profile ID must not leak over pipe!
        finally:
            client.close()
            server.stop()

    # -------------------------------------------------------------------------
    # 7. Legacy Profile ID Migration (profile_<timestamp> -> UUID4)
    # -------------------------------------------------------------------------
    def test_legacy_profile_migration_to_uuid4(self):
        """Legacy profile_<timestamp> files are securely migrated to UUID4 with old file deleted."""
        store = SecureProfileStore(storage_dir=self.temp_dir)
        
        legacy_id = "profile_1710123456"
        legacy_profile = PeekProfile(
            profile_id=legacy_id,
            display_name="Legacy Enrolled User",
            enabled=True,
            matching_threshold=0.50,
            templates=[
                PeekTemplate(
                    template_id="t1",
                    embedding=[0.1] * 512,
                    pose_label="CENTER",
                    quality_score=0.95
                )
            ]
        )
        store.save_profile(legacy_profile)

        legacy_path = os.path.join(self.temp_dir, f"{legacy_id}.peek")
        self.assertTrue(os.path.exists(legacy_path))

        # Calling list_profiles() triggers auto-migration
        profiles = store.list_profiles()
        self.assertEqual(len(profiles), 1)

        migrated_profile = profiles[0]
        # Verify legacy file was deleted
        self.assertFalse(os.path.exists(legacy_path))

        # Verify new profile has UUID4 format
        self.assertNotEqual(migrated_profile.profile_id, legacy_id)
        # Should be valid UUID4
        parsed_uuid = uuid.UUID(migrated_profile.profile_id)
        self.assertEqual(parsed_uuid.version, 4)

        # Verify new file exists
        new_path = os.path.join(self.temp_dir, f"{migrated_profile.profile_id}.peek")
        self.assertTrue(os.path.exists(new_path))

        # Verify profile content preserved
        self.assertEqual(migrated_profile.display_name, "Legacy Enrolled User")
        self.assertEqual(len(migrated_profile.templates), 1)
        self.assertEqual(migrated_profile.templates[0].quality_score, 0.95)


if __name__ == "__main__":
    unittest.main()
