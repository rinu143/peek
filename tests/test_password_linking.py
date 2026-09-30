"""Password-linking tests that never require a real Windows account."""
import os
import tempfile
import unittest
from unittest.mock import patch

from apps.PeekEnrollment.main import link_windows_password
from storage.template_store import SecureProfileStore


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
