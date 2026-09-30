"""Password-linking tests that never require a real Windows account."""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from apps.PeekEnrollment.main import link_windows_password, migrate_legacy_secret
from storage.template_store import (
    SecureProfileStore,
    dpapi_encrypt,
    dpapi_decrypt,
    get_machine_entropy,
)


class PasswordLinkingTests(unittest.TestCase):
    def test_rejected_password_never_persists(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("apps.PeekEnrollment.main.os.name", "nt"), \
             patch("apps.PeekEnrollment.main._current_windows_username", return_value="testuser"), \
             patch("storage.template_store.dpapi_encrypt") as encrypt:
            result = link_windows_password(
                "p1", "secret", SecureProfileStore(directory), verify_password=lambda _u, _p: False
            )
            self.assertFalse(result)
            encrypt.assert_not_called()
            self.assertFalse(os.path.exists(os.path.join(directory, "p1.secret")))
            self.assertEqual(len(os.listdir(directory)), 0)

    def test_verified_password_writes_encrypted_secret(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("apps.PeekEnrollment.main.os.name", "nt"), \
             patch("apps.PeekEnrollment.main._current_windows_username", return_value="testuser"), \
             patch("storage.template_store.dpapi_encrypt", return_value=b"encrypted_ciphertext_data") as encrypt:
            path = os.path.join(directory, "p2.secret")
            result = link_windows_password(
                "p2", "supersecretpassword", SecureProfileStore(directory), verify_password=lambda _u, _p: True
            )
            self.assertTrue(result)
            encrypt.assert_called_once()
            # Assert exactly one <profile_id>.secret file was written
            files = os.listdir(directory)
            self.assertEqual(files, ["p2.secret"])
            with open(path, "rb") as f:
                content = f.read()
            self.assertEqual(content, b"encrypted_ciphertext_data")
            self.assertNotIn(b"supersecretpassword", content)

    def test_entropy_passed_to_dpapi_encrypt(self):
        """Verify that link_windows_password forwards explicit or machine entropy to dpapi_encrypt."""
        with tempfile.TemporaryDirectory() as directory, \
             patch("apps.PeekEnrollment.main.os.name", "nt"), \
             patch("apps.PeekEnrollment.main._current_windows_username", return_value="testuser"), \
             patch("storage.template_store.dpapi_encrypt", return_value=b"encrypted_with_entropy") as mock_encrypt:

            # 1. Explicit entropy passed
            custom_entropy = b"custom_entropy_12345"
            result = link_windows_password(
                "p_custom", "my_secret_pass", SecureProfileStore(directory),
                verify_password=lambda _u, _p: True, entropy=custom_entropy
            )
            self.assertTrue(result)
            mock_encrypt.assert_called_once()
            _, kwargs = mock_encrypt.call_args
            self.assertEqual(kwargs.get("entropy"), custom_entropy)

            # 2. Default machine entropy passed
            mock_encrypt.reset_mock()
            with patch("storage.template_store.get_machine_entropy", return_value=b"mock_machine_entropy_guid"):
                result_default = link_windows_password(
                    "p_default", "my_secret_pass", SecureProfileStore(directory),
                    verify_password=lambda _u, _p: True
                )
                self.assertTrue(result_default)
                mock_encrypt.assert_called_once()
                _, kwargs_default = mock_encrypt.call_args
                self.assertEqual(kwargs_default.get("entropy"), b"mock_machine_entropy_guid")

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_dpapi_roundtrip_with_entropy(self):
        """Verify real Windows DPAPI CryptProtectData/CryptUnprotectData with entropy."""
        test_payload = b"Hello, Peek Secure Vault!"
        test_entropy = b"machine-bound-entropy-token"

        ciphertext = dpapi_encrypt(test_payload, description="TestSecret", entropy=test_entropy)
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

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_legacy_no_entropy_secret_migrates_correctly(self):
        """Verify backward compatibility: legacy secret with NULL entropy migrates to machine entropy."""
        with tempfile.TemporaryDirectory() as directory:
            store = SecureProfileStore(directory)
            profile_id = "legacy_user"
            secret_path = os.path.join(directory, f"{profile_id}.secret")
            test_password = "LegacyPassword123!"

            # 1. Write legacy secret encrypted with NULL entropy (pre-hardening state)
            import struct
            raw_payload = bytearray(struct.pack("<I", len(test_password.encode("utf-16-le"))))
            raw_payload.extend(test_password.encode("utf-16-le"))
            legacy_ciphertext = dpapi_encrypt(bytes(raw_payload), description="PeekCredentialSecret", entropy=None)

            with open(secret_path, "wb") as f:
                f.write(legacy_ciphertext)

            # 2. Confirm it decrypts with None but fails with machine entropy before migration
            machine_entropy = b"peek_install_machine_entropy_test"
            self.assertEqual(dpapi_decrypt(legacy_ciphertext, entropy=None), bytes(raw_payload))
            with self.assertRaises(Exception):
                dpapi_decrypt(legacy_ciphertext, entropy=machine_entropy)

            # 3. Perform migration
            migrated = migrate_legacy_secret(profile_id, store=store, entropy=machine_entropy)
            self.assertTrue(migrated)

            # 4. Confirm the file on disk is updated: now requires machine_entropy and fails with None
            with open(secret_path, "rb") as f:
                new_ciphertext = f.read()

            self.assertNotEqual(new_ciphertext, legacy_ciphertext)
            decrypted_migrated = dpapi_decrypt(new_ciphertext, entropy=machine_entropy)
            self.assertEqual(decrypted_migrated, bytes(raw_payload))

            # Decrypting with None must now fail
            with self.assertRaises(Exception):
                dpapi_decrypt(new_ciphertext, entropy=None)


if __name__ == "__main__":
    unittest.main()
