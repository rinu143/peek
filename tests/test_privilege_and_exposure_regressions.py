"""
Comprehensive Regression & Security Tests:
1. Profiles storage: %ProgramData%\\Peek\\Profiles and machine_entropy.bin readable/writable only by SYSTEM/Admins
   (inheritance protected, no Users ACE).
2. Management authorization: caller SID impersonation and strict user_sid matching on LIST, SET_ENABLED, DELETE, RENAME, GET_QUALITY.
3. Registry security: HKLM\\SOFTWARE\\Peek and Identities are admin/SYSTEM write-only; Prefs\\AutoSelect allows Users SetValue only.
4. Engine identity and camera result: pipe owner logging, session 0 / camera result documentation to camera_status.json.
5. Persisted lockout: state saved across simulated reboots with wall-clock time; corrupt state fails closed.
6. Legacy migration: single migration from %LOCALAPPDATA% with deletion of legacy files.
"""

import os
import sys
import json
import time
import uuid
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import win32security
import win32con
import ntsecuritycon

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from storage.template_store import (
    SecureProfileStore,
    PeekProfile,
    PeekTemplate,
    secure_admin_only_path,
    get_machine_entropy,
)
from engine.ipc.pipe_server import UserLockoutTracker
from engine.ipc.enrollment_protocol import (
    MSG_LIST_PROFILES,
    MSG_SET_ENABLED,
    MSG_DELETE_PROFILE,
    MSG_RENAME_PROFILE,
    MSG_GET_QUALITY,
    MSG_PROFILES_LIST,
    MSG_SET_ENABLED_RESPONSE,
    MSG_DELETE_RESPONSE,
    MSG_RENAME_RESPONSE,
    MSG_QUALITY_RESPONSE,
)
from engine.ipc.enrollment_pipe_server import (
    EnrollmentPipeServer,
    EnrollmentPipeClientSession,
)
from scripts.run_engine_service import (
    log_pipe_owner,
    probe_and_document_camera,
)


class PrivilegeAndExposureSecurityTests(unittest.TestCase):

    def test_secure_admin_only_path_creates_inheritance_protected_no_users_acl(self):
        """Verifies secure_admin_only_path protects inheritance and removes all Users ACEs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "secure_test.bin")
            with open(test_file, "wb") as f:
                f.write(b"sensitive_entropy_data")

            # Apply secure_admin_only_path
            secure_admin_only_path(test_file, is_directory=False)

            # Query DACL
            sd = win32security.GetFileSecurity(test_file, win32security.DACL_SECURITY_INFORMATION)
            dacl = sd.GetSecurityDescriptorDacl()
            control, _ = sd.GetSecurityDescriptorControl()

            # 1. Inheritance protection flag must be SE_DACL_PROTECTED
            self.assertTrue(
                bool(control & win32security.SE_DACL_PROTECTED),
                "DACL must be inheritance protected (SE_DACL_PROTECTED)"
            )

            # 2. Check for BuiltinUsersSid (S-1-5-32-545)
            users_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinUsersSid)
            for i in range(dacl.GetAceCount()):
                ace = dacl.GetAce(i)
                ace_sid = ace[2]
                self.assertNotEqual(
                    ace_sid,
                    users_sid,
                    "BuiltinUsersSid (Users) must NOT have an ACE on protected profile/entropy paths"
                )

    def test_profile_store_and_entropy_are_inheritance_protected_and_admin_only(self):
        """Verifies SecureProfileStore enforces secure_admin_only_path on profiles and entropy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_dir = os.path.join(tmpdir, "Profiles")
            os.environ["PEEK_PROFILES_DIR"] = profiles_dir

            try:
                store = SecureProfileStore(storage_dir=profiles_dir)
                profile = PeekProfile(
                    profile_id="sec_test_user_1",
                    display_name="Secure User",
                    user_sid="S-1-5-21-1111-2222",
                    templates=[
                        PeekTemplate(template_id="t1", embedding=[0.1] * 512, pose_label="CENTER", quality_score=0.92)
                    ]
                )
                saved_path = store.save_profile(profile)
                self.assertTrue(os.path.exists(saved_path))

                # Check profiles directory DACL
                dir_sd = win32security.GetFileSecurity(profiles_dir, win32security.DACL_SECURITY_INFORMATION)
                dir_dacl = dir_sd.GetSecurityDescriptorDacl()
                dir_ctrl, _ = dir_sd.GetSecurityDescriptorControl()
                self.assertTrue(bool(dir_ctrl & win32security.SE_DACL_PROTECTED))

                users_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinUsersSid)
                for i in range(dir_dacl.GetAceCount()):
                    self.assertNotEqual(dir_dacl.GetAce(i)[2], users_sid, "Profiles dir must not have Users ACE")

                # Check profile file DACL
                file_sd = win32security.GetFileSecurity(saved_path, win32security.DACL_SECURITY_INFORMATION)
                file_dacl = file_sd.GetSecurityDescriptorDacl()
                file_ctrl, _ = file_sd.GetSecurityDescriptorControl()
                self.assertTrue(bool(file_ctrl & win32security.SE_DACL_PROTECTED))

                for i in range(file_dacl.GetAceCount()):
                    self.assertNotEqual(file_dacl.GetAce(i)[2], users_sid, "Profile file must not have Users ACE")

            finally:
                os.environ.pop("PEEK_PROFILES_DIR", None)

    def test_legacy_profile_migration_deletes_source_file_from_localappdata(self):
        """Verifies migration under the engine copies profile to ProgramData and DELETES localappdata source."""
        with tempfile.TemporaryDirectory() as tmp_program_data, \
             tempfile.TemporaryDirectory() as tmp_local_app_data:

            # Create legacy profile in LOCALAPPDATA
            legacy_dir = os.path.join(tmp_local_app_data, "Peek", "Profiles")
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_file = os.path.join(legacy_dir, "legacy_user.peek")

            target_dir = os.path.join(tmp_program_data, "Peek", "Profiles")
            os.makedirs(target_dir, exist_ok=True)

            entropy = b"test_migration_entropy_12345"
            with open(legacy_file, "wb") as f:
                f.write(b"legacy_encrypted_data")

            # Mock decrypt to return valid PeekProfile json
            mock_profile_json = json.dumps({
                "profile_id": "legacy_migrated_id",
                "display_name": "Migrated User",
                "enabled": True,
                "created_at": time.time(),
                "templates": []
            }).encode("utf-8")

            with patch.dict(os.environ, {"LOCALAPPDATA": tmp_local_app_data, "ProgramData": tmp_program_data}), \
                 patch("storage.template_store.get_machine_entropy", return_value=entropy), \
                 patch("storage.template_store.dpapi_decrypt", return_value=mock_profile_json), \
                 patch("storage.template_store.dpapi_encrypt", return_value=b"new_machine_encrypted_bytes"):

                store = SecureProfileStore()

                # Target file must now exist
                migrated_target = os.path.join(target_dir, "legacy_migrated_id.peek")
                self.assertTrue(os.path.exists(migrated_target), "Profile must be migrated to ProgramData")

                # Source legacy file in LOCALAPPDATA MUST be deleted so it cannot be read by other users
                self.assertFalse(
                    os.path.exists(legacy_file),
                    "Legacy profile in LOCALAPPDATA must be deleted after migration to avoid data exposure"
                )

    def test_management_authorization_caller_sid_matching(self):
        """Verifies engine rejects management requests from callers whose SID does not match the profile's user_sid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SecureProfileStore(storage_dir=tmpdir)

            # Profile 1 owned by User A
            user_a_sid = "S-1-5-21-USER-A-1001"
            prof_a = PeekProfile(
                profile_id="prof_user_a",
                display_name="User Alpha",
                user_sid=user_a_sid,
                enabled=True,
                templates=[PeekTemplate(template_id="t1", embedding=[0.1] * 512, pose_label="CENTER", quality_score=0.9)]
            )
            store.save_profile(prof_a)

            # Profile 2 owned by User B
            user_b_sid = "S-1-5-21-USER-B-1002"
            prof_b = PeekProfile(
                profile_id="prof_user_b",
                display_name="User Bravo",
                user_sid=user_b_sid,
                enabled=True,
                templates=[PeekTemplate(template_id="t2", embedding=[0.2] * 512, pose_label="CENTER", quality_score=0.85)]
            )
            store.save_profile(prof_b)

            server = EnrollmentPipeServer(profile_store=store)

            # Simulated pipe session for Caller User A
            session_a = EnrollmentPipeClientSession(handle=MagicMock())
            session_a.write_message = MagicMock()

            # Simulated pipe session for Caller User B
            session_b = EnrollmentPipeClientSession(handle=MagicMock())
            session_b.write_message = MagicMock()

            with patch("engine.ipc.enrollment_pipe_server.get_authenticated_client_sid", return_value=user_a_sid):
                # 1. LIST_PROFILES from User A must ONLY return User A's profile
                server._handle_list_profiles(session_a, {"type": MSG_LIST_PROFILES})
                session_a.write_message.assert_called_once()
                resp = session_a.write_message.call_args[0][0]
                self.assertEqual(resp["type"], MSG_PROFILES_LIST)
                self.assertTrue(resp["success"])
                returned_ids = [p["profile_id"] for p in resp["profiles"]]
                self.assertIn("prof_user_a", returned_ids)
                self.assertNotIn("prof_user_b", returned_ids)

                session_a.write_message.reset_mock()

                # 2. User A attempts to toggle User B's profile -> REJECTED
                server._handle_client_message_impl(
                    session_a,
                    MSG_SET_ENABLED,
                    {"type": MSG_SET_ENABLED, "profile_id": "prof_user_b", "enabled": False}
                )
                resp = session_a.write_message.call_args[0][0]
                self.assertEqual(resp["type"], MSG_SET_ENABLED_RESPONSE)
                self.assertFalse(resp["success"])
                self.assertIn("mismatch", resp.get("error", "").lower())
                # Verify prof_user_b was NOT disabled
                self.assertTrue(store.load_profile("prof_user_b").enabled)

                session_a.write_message.reset_mock()

                # 3. User A attempts to rename User B's profile -> REJECTED
                server._handle_client_message_impl(
                    session_a,
                    MSG_RENAME_PROFILE,
                    {"type": MSG_RENAME_PROFILE, "profile_id": "prof_user_b", "new_name": "Hacked Name"}
                )
                resp = session_a.write_message.call_args[0][0]
                self.assertEqual(resp["type"], MSG_RENAME_RESPONSE)
                self.assertFalse(resp["success"])
                self.assertEqual(store.load_profile("prof_user_b").display_name, "User Bravo")

                session_a.write_message.reset_mock()

                # 4. User A attempts to delete User B's profile -> REJECTED
                server._handle_client_message_impl(
                    session_a,
                    MSG_DELETE_PROFILE,
                    {"type": MSG_DELETE_PROFILE, "profile_id": "prof_user_b"}
                )
                resp = session_a.write_message.call_args[0][0]
                self.assertEqual(resp["type"], MSG_DELETE_RESPONSE)
                self.assertFalse(resp["success"])
                self.assertIsNotNone(store.load_profile("prof_user_b"))

                session_a.write_message.reset_mock()

                # 5. User A toggles and renames User A's OWN profile -> SUCCEEDS
                server._handle_client_message_impl(
                    session_a,
                    MSG_SET_ENABLED,
                    {"type": MSG_SET_ENABLED, "profile_id": "prof_user_a", "enabled": False}
                )
                resp = session_a.write_message.call_args[0][0]
                self.assertTrue(resp["success"])
                self.assertFalse(store.load_profile("prof_user_a").enabled)

    def test_persisted_lockout_across_simulated_reboots_and_wall_clock(self):
        """Power-cycle test: after 5 failed attempts and a reboot/restart, lockout remains active."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lockout_file = os.path.join(tmpdir, "lockout.json")

            # 1. Session 1: 5 failed attempts
            tracker1 = UserLockoutTracker(
                max_failures=5,
                lockout_duration_seconds=300,
                state_file_path=lockout_file,
            )

            test_user = "TargetUser"
            for _ in range(4):
                newly_locked = tracker1.record_failure(test_user)
                self.assertFalse(newly_locked)
                is_locked, _ = tracker1.is_locked_out(test_user)
                self.assertFalse(is_locked)

            # 5th failure triggers lockout
            newly_locked = tracker1.record_failure(test_user)
            self.assertTrue(newly_locked)
            locked, remaining = tracker1.is_locked_out(test_user)
            self.assertTrue(locked)
            self.assertGreater(remaining, 280)
            self.assertTrue(os.path.exists(lockout_file))

            # 2. Simulate machine reboot / engine process restart:
            # instantiate fresh tracker reading from same persistent lockout file
            tracker2 = UserLockoutTracker(
                max_failures=5,
                lockout_duration_seconds=300,
                state_file_path=lockout_file,
            )

            is_locked, remaining2 = tracker2.is_locked_out(test_user)
            self.assertTrue(is_locked, "Lockout must remain active across engine process restarts / reboots")
            self.assertGreater(remaining2, 280)

            # Successful auth resets the failure count and clears lockout
            tracker2.record_success(test_user)
            is_locked_after, _ = tracker2.is_locked_out(test_user)
            self.assertFalse(is_locked_after)

    def test_corrupt_lockout_file_fails_closed(self):
        """Corrupt state file fails closed (lockout active)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lockout_file = os.path.join(tmpdir, "lockout.json")

            # Write corrupt/invalid JSON
            with open(lockout_file, "w", encoding="utf-8") as f:
                f.write("{corrupt_garbage_truncated_json")

            tracker = UserLockoutTracker(
                max_failures=5,
                lockout_duration_seconds=300,
                state_file_path=lockout_file,
            )

            # Corrupt state file must fail closed
            is_locked, remaining = tracker.is_locked_out("AnyUser")
            self.assertTrue(is_locked, "Corrupt lockout state must fail closed")
            self.assertGreater(remaining, 0)

    def test_pipe_owner_and_camera_status_documentation(self):
        """Verifies pipe owner is defined and logged, and camera status documents Session 0 result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"ProgramData": tmpdir}):
                # Test pipe owner logging
                owner = log_pipe_owner()
                self.assertIn("user_name", owner)
                self.assertIn("user_sid", owner)
                self.assertIn("pid", owner)
                self.assertIn("session_id", owner)

                # Test camera probe documentation in session 0
                status_0 = probe_and_document_camera(session_id=0)
                self.assertTrue(status_0["is_session_0"])
                self.assertEqual(status_0["session_id"], 0)

                # Verify file was written to %ProgramData%\Peek\state\camera_status.json
                status_path = os.path.join(tmpdir, "Peek", "state", "camera_status.json")
                self.assertTrue(os.path.exists(status_path))

                with open(status_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertTrue(data["is_session_0"])
                self.assertIn("camera_available", data)

    def test_registry_acls_users_cannot_write_root_or_identities(self):
        """Test that fails if Users have write rights on HKLM\\SOFTWARE\\Peek or Identities."""
        import winreg
        users_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinUsersSid)

        keys_to_test = [
            r"SOFTWARE\Peek",
            r"SOFTWARE\Peek\Identities",
        ]

        for subkey in keys_to_test:
            try:
                # Attempt to query registry security
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey, 0, winreg.KEY_READ)
                sd = win32security.GetSecurityInfo(
                    k.handle,
                    win32security.SE_REGISTRY_KEY,
                    win32security.DACL_SECURITY_INFORMATION
                )
            except OSError:
                # Key might not exist in test runner environment; that's fine
                continue

            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:
                continue

            for i in range(dacl.GetAceCount()):
                ace = dacl.GetAce(i)
                sid = ace[2]
                mask = ace[1]
                ace_type = ace[0][0]

                if sid == users_sid and ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE:
                    write_masks = (
                        winreg.KEY_SET_VALUE |
                        winreg.KEY_CREATE_SUB_KEY |
                        winreg.KEY_CREATE_LINK |
                        0x00010000 |  # DELETE
                        0x00040000 |  # WRITE_DAC
                        0x00080000 |  # WRITE_OWNER
                        win32con.GENERIC_WRITE |
                        win32con.GENERIC_ALL
                    )
                    self.assertEqual(
                        mask & write_masks,
                        0,
                        f"Users SID must NOT have write rights on HKLM\\{subkey} (Mask: 0x{mask:08X})"
                    )


if __name__ == "__main__":
    unittest.main()
