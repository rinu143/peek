"""Password-linking and secret storage policy tests."""
import os
import io
import tempfile
import unittest
from unittest.mock import patch

from apps.PeekEnrollment.main import prompt_optional_password_link
from storage.template_store import (
    SecureProfileStore,
    dpapi_encrypt,
    dpapi_decrypt,
    get_machine_entropy,
)


class PasswordLinkingTests(unittest.TestCase):
    def test_enrollment_prompt_never_writes_secret_files(self):
        """Verify prompt_optional_password_link prints pointer to PeekSetup and writes zero .secret files."""
        with tempfile.TemporaryDirectory() as directory:
            store = SecureProfileStore(directory)
            profile_id = "test_profile_no_secret"

            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                prompt_optional_password_link(profile_id, store)
                output = mock_stdout.getvalue()

            self.assertIn("PeekSetup", output)
            # Assert absolutely no .secret file was created in the profile directory
            secret_path = os.path.join(directory, f"{profile_id}.secret")
            self.assertFalse(os.path.exists(secret_path))
            files = [f for f in os.listdir(directory) if f.endswith(".secret")]
            self.assertEqual(len(files), 0, "Python enrollment tool must not write any .secret files")

    def test_python_app_does_not_contain_secret_writing_functions(self):
        """Assert link_windows_password and legacy migration have been removed from Python enrollment."""
        import apps.PeekEnrollment.main as main_mod
        self.assertFalse(hasattr(main_mod, "link_windows_password"))
        self.assertFalse(hasattr(main_mod, "_verify_windows_password"))
        self.assertFalse(hasattr(main_mod, "migrate_legacy_secret"))

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_dpapi_roundtrip_with_entropy_for_templates(self):
        """Verify Windows DPAPI used for biometric templates remains fully functional."""
        test_payload = b"Biometric template data for testing"
        test_entropy = b"machine-bound-entropy-token"

        ciphertext = dpapi_encrypt(test_payload, description="TestTemplate", entropy=test_entropy)
        self.assertNotEqual(ciphertext, test_payload)

        # Correct entropy decrypts
        decrypted = dpapi_decrypt(ciphertext, entropy=test_entropy)
        self.assertEqual(decrypted, test_payload)

        # Wrong entropy fails decryption
        with self.assertRaises(Exception):
            dpapi_decrypt(ciphertext, entropy=b"wrong-entropy")

        # Missing entropy (None) fails decryption
        with self.assertRaises(Exception):
            dpapi_decrypt(ciphertext, entropy=None)

    def test_get_machine_entropy_environment_override(self):
        """Verify PEEK_MACHINE_ENTROPY environment variable overrides disk lookups."""
        with patch.dict(os.environ, {"PEEK_MACHINE_ENTROPY": "  override-entropy-guid  "}):
            self.assertEqual(get_machine_entropy(), b"override-entropy-guid")

    def test_get_machine_entropy_reads_existing_file(self):
        """Verify get_machine_entropy reads and strips existing machine_entropy.bin."""
        with tempfile.TemporaryDirectory() as tmp_program_data:
            peek_dir = os.path.join(tmp_program_data, "Peek")
            os.makedirs(peek_dir, exist_ok=True)
            entropy_file = os.path.join(peek_dir, "machine_entropy.bin")
            with open(entropy_file, "wb") as f:
                f.write(b"  existing-guid-entropy-value  \n")

            env = {"ProgramData": tmp_program_data}
            with patch.dict(os.environ, env, clear=False):
                if "PEEK_MACHINE_ENTROPY" in os.environ:
                    del os.environ["PEEK_MACHINE_ENTROPY"]
                val = get_machine_entropy()
                self.assertEqual(val, b"existing-guid-entropy-value")

    def test_get_machine_entropy_creates_file_if_missing_and_writable(self):
        """Verify get_machine_entropy creates machine_entropy.bin if missing and directory is writable."""
        with tempfile.TemporaryDirectory() as tmp_program_data:
            env = {"ProgramData": tmp_program_data}
            with patch.dict(os.environ, env, clear=False):
                if "PEEK_MACHINE_ENTROPY" in os.environ:
                    del os.environ["PEEK_MACHINE_ENTROPY"]
                val = get_machine_entropy()
                self.assertTrue(len(val) > 0)
                entropy_file = os.path.join(tmp_program_data, "Peek", "machine_entropy.bin")
                self.assertTrue(os.path.isfile(entropy_file))
                with open(entropy_file, "rb") as f:
                    content = f.read()
                self.assertEqual(content, val)

    def test_get_machine_entropy_raises_error_if_unreadable_and_unwritable(self):
        """Verify get_machine_entropy raises RuntimeError and logs error instead of fallback to MachineGuid or uuid."""
        with tempfile.TemporaryDirectory() as tmp_program_data:
            peek_dir = os.path.join(tmp_program_data, "Peek")
            os.makedirs(peek_dir, exist_ok=True)
            entropy_file = os.path.join(peek_dir, "machine_entropy.bin")
            with open(entropy_file, "w") as f:
                f.write("")

            env = {"ProgramData": tmp_program_data}
            with patch.dict(os.environ, env, clear=False):
                if "PEEK_MACHINE_ENTROPY" in os.environ:
                    del os.environ["PEEK_MACHINE_ENTROPY"]
                def mock_open_err(*args, **kwargs):
                    raise PermissionError("Access denied")

                with patch("builtins.open", side_effect=mock_open_err), \
                     patch("storage.template_store.logger.error") as mock_log_err:
                    with self.assertRaises(RuntimeError) as ctx:
                        get_machine_entropy()
                    self.assertIn("could not be read or created", str(ctx.exception))
                    mock_log_err.assert_called_once()
                    self.assertIn("could not be read or created", mock_log_err.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
