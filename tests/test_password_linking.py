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


if __name__ == "__main__":
    unittest.main()
