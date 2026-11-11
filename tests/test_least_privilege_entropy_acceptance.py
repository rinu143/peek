"""Acceptance tests for least-privilege installation, machine entropy handling, and documentation links."""
import os
import re
import tempfile
import uuid
import unittest
from unittest.mock import patch

from storage.template_store import (
    SecureProfileStore,
    PeekProfile,
    PeekTemplate,
    get_machine_entropy,
)


class LeastPrivilegeEntropyAcceptanceTests(unittest.TestCase):
    def test_fresh_install_non_elevated_user_lifecycle(self):
        """
        Acceptance Criteria 1:
        Fresh install as a non-elevated user: enrollment succeeds, profile decrypts after an
        engine restart, and no fallback entropy source is used.
        """
        with tempfile.TemporaryDirectory() as tmp_program_data, \
             tempfile.TemporaryDirectory() as tmp_user_profiles:

            # 1. Simulate install.ps1: elevated installer created machine_entropy.bin
            peek_data_dir = os.path.join(tmp_program_data, "Peek")
            os.makedirs(peek_data_dir, exist_ok=True)
            entropy_file = os.path.join(peek_data_dir, "machine_entropy.bin")

            installed_guid = str(uuid.uuid4())
            with open(entropy_file, "wb") as f:
                f.write(installed_guid.encode("utf-8"))

            # Simulate non-elevated user: PEEK_MACHINE_ENTROPY unset, ProgramData points to installed directory
            env = {"ProgramData": tmp_program_data}
            with patch.dict(os.environ, env, clear=False):
                if "PEEK_MACHINE_ENTROPY" in os.environ:
                    del os.environ["PEEK_MACHINE_ENTROPY"]

                # Ensure get_machine_entropy reads installed machine_entropy.bin directly
                read_entropy = get_machine_entropy()
                self.assertEqual(read_entropy, installed_guid.encode("utf-8"))

                # 2. Non-elevated user enrollment session
                store1 = SecureProfileStore(storage_dir=tmp_user_profiles)
                profile = PeekProfile(
                    profile_id=str(uuid.uuid4()),
                    display_name="TestUser",
                    templates=[
                        PeekTemplate(
                            template_id="t1",
                            embedding=[0.123] * 512,
                            pose_label="CENTER",
                            quality_score=0.95,
                        )
                    ]
                )
                saved_path = store1.save_profile(profile)
                self.assertTrue(os.path.isfile(saved_path))

                # 3. Simulate engine restart: fresh store instance
                store2 = SecureProfileStore(storage_dir=tmp_user_profiles)
                loaded_profile = store2.load_profile(profile.profile_id)

                self.assertIsNotNone(loaded_profile, "Profile must successfully decrypt after engine restart")
                self.assertEqual(loaded_profile.profile_id, profile.profile_id)
                self.assertEqual(loaded_profile.display_name, "TestUser")
                self.assertEqual(len(loaded_profile.templates), 1)
                self.assertAlmostEqual(loaded_profile.templates[0].embedding[0], 0.123, places=3)

    def test_missing_unreadable_entropy_file_produces_error_not_different_key(self):
        """
        Acceptance Criteria 2:
        A missing/unreadable entropy file produces an error, not a different key.
        """
        with tempfile.TemporaryDirectory() as tmp_program_data:
            peek_data_dir = os.path.join(tmp_program_data, "Peek")
            os.makedirs(peek_data_dir, exist_ok=True)
            entropy_file = os.path.join(peek_data_dir, "machine_entropy.bin")

            env = {"ProgramData": tmp_program_data}
            with patch.dict(os.environ, env, clear=False):
                if "PEEK_MACHINE_ENTROPY" in os.environ:
                    del os.environ["PEEK_MACHINE_ENTROPY"]

                # Case A: Missing file and unwritable directory (simulating non-elevated user)
                def mock_open_perm_denied(path, mode="r", *args, **kwargs):
                    if "w" in mode or "a" in mode:
                        raise PermissionError(f"Access denied to {path}")
                    raise FileNotFoundError(f"No such file: {path}")

                with patch("builtins.open", side_effect=mock_open_perm_denied), \
                     patch("storage.template_store.logger.error") as mock_log:
                    with self.assertRaises(RuntimeError) as ctx:
                        get_machine_entropy()

                    self.assertIn("could not be read or created", str(ctx.exception))
                    mock_log.assert_called_once()

                # Case B: File exists but cannot be read
                with open(entropy_file, "wb") as f:
                    f.write(b"entropy-secret")

                def mock_read_error(path, mode="r", *args, **kwargs):
                    raise PermissionError(f"Permission denied reading {path}")

                with patch("builtins.open", side_effect=mock_read_error), \
                     patch("storage.template_store.logger.error") as mock_log_read:
                    with self.assertRaises(RuntimeError) as ctx:
                        get_machine_entropy()

                    self.assertIn("could not be read or created", str(ctx.exception))
                    mock_log_read.assert_called_once()

    def test_docs_contain_no_file_uri_links(self):
        """
        Acceptance Criteria 3:
        Docs contain no file:/// links.
        """
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        file_uri_pattern = re.compile(r"file:///", re.IGNORECASE)

        violating_files = []
        for root, _, files in os.walk(repo_root):
            # Skip .git, .pytest_cache, bin, obj, dist
            if any(part in root for part in [".git", ".pytest_cache", "bin", "obj", "dist"]):
                continue
            for file in files:
                if file.endswith(".md"):
                    full_path = os.path.join(root, file)
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        if file_uri_pattern.search(content):
                            violating_files.append(full_path)

        self.assertEqual(
            violating_files,
            [],
            f"Expected no markdown files with file:/// links, but found: {violating_files}"
        )


if __name__ == "__main__":
    unittest.main()
