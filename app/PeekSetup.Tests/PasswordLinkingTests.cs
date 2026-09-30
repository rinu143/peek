using System;
using System.Buffers.Binary;
using System.IO;
using System.Security;
using System.Security.Cryptography;
using System.Text;
using PeekSetup.Services;
using Xunit;

namespace PeekSetup.Tests
{
    public class MockPasswordValidator : IPasswordValidator
    {
        public bool ShouldAccept { get; set; }
        public string? LastValidatedUsername { get; private set; }
        public int ValidateCallCount { get; private set; }

        public MockPasswordValidator(bool shouldAccept = true)
        {
            ShouldAccept = shouldAccept;
        }

        public bool Validate(string username, SecureString password, string? domain = null)
        {
            ValidateCallCount++;
            LastValidatedUsername = username;
            return ShouldAccept;
        }
    }

    public class PasswordLinkingTests
    {
        private static SecureString CreateSecureString(string plain)
        {
            var ss = new SecureString();
            foreach (char c in plain)
            {
                ss.AppendChar(c);
            }
            ss.MakeReadOnly();
            return ss;
        }

        private static bool ContainsSubSequence(byte[] haystack, byte[] needle)
        {
            if (needle.Length == 0 || haystack.Length < needle.Length) return false;
            for (int i = 0; i <= haystack.Length - needle.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length; j++)
                {
                    if (haystack[i + j] != needle[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match) return true;
            }
            return false;
        }

        [Fact]
        public void RejectedPassword_NeverPersistsAndReturnsFalse()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTest_" + Guid.NewGuid().ToString("N"));
            try
            {
                var mockValidator = new MockPasswordValidator(shouldAccept: false);
                var linker = new PasswordLinker(mockValidator);

                using var password = CreateSecureString("invalid_password");
                bool result = linker.LinkPassword("profile_rejected", password, "testuser", tempDir);

                Assert.False(result);
                Assert.Equal(1, mockValidator.ValidateCallCount);
                Assert.Equal("testuser", mockValidator.LastValidatedUsername);

                // Verify nothing was written to disk
                string expectedSecretPath = Path.Combine(tempDir, "profile_rejected.secret");
                Assert.False(File.Exists(expectedSecretPath));
                if (Directory.Exists(tempDir))
                {
                    Assert.Empty(Directory.GetFiles(tempDir));
                }
            }
            finally
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
        }

        [Fact]
        public void VerifiedPassword_WritesExactlyOneCorrectlyFormattedEncryptedFile()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTest_" + Guid.NewGuid().ToString("N"));
            try
            {
                var mockValidator = new MockPasswordValidator(shouldAccept: true);
                var linker = new PasswordLinker(mockValidator);

                string testSecretPassword = "SuperSecretPassword123!@#";
                using var password = CreateSecureString(testSecretPassword);
                bool result = linker.LinkPassword("profile_verified", password, "testuser", tempDir);

                Assert.True(result);
                Assert.Equal(1, mockValidator.ValidateCallCount);

                // Assert exactly one .secret file was written
                string[] files = Directory.GetFiles(tempDir);
                Assert.Single(files);
                Assert.Equal("profile_verified.secret", Path.GetFileName(files[0]));

                byte[] encryptedBytes = File.ReadAllBytes(files[0]);
                Assert.NotEmpty(encryptedBytes);

                // Security check: Plaintext password must NOT appear in ciphertext
                byte[] rawUtf8 = Encoding.UTF8.GetBytes(testSecretPassword);
                byte[] rawUtf16 = Encoding.Unicode.GetBytes(testSecretPassword);
                Assert.False(ContainsSubSequence(encryptedBytes, rawUtf8));
                Assert.False(ContainsSubSequence(encryptedBytes, rawUtf16));

                // Decrypt using Windows DPAPI to verify exact format matching SecretVault.cpp:
                // Format: [DWORD length (UTF-16LE byte length)][UTF-16LE password bytes]
                byte[] decrypted = ProtectedData.Unprotect(encryptedBytes, null, DataProtectionScope.CurrentUser);
                Assert.True(decrypted.Length >= 4);

                uint passwordByteLen = BinaryPrimitives.ReadUInt32LittleEndian(decrypted.AsSpan(0, 4));
                int expectedByteLen = testSecretPassword.Length * sizeof(char);
                Assert.Equal((uint)expectedByteLen, passwordByteLen);
                Assert.Equal(4 + expectedByteLen, decrypted.Length);

                string decryptedPassword = Encoding.Unicode.GetString(decrypted, 4, (int)passwordByteLen);
                Assert.Equal(testSecretPassword, decryptedPassword);
            }
            finally
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
        }

        [Fact]
        public void CustomEncryption_PassedExactLengthPrefixedPlaintext()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTest_" + Guid.NewGuid().ToString("N"));
            try
            {
                byte[]? capturedPlaintext = null;
                byte[] mockCipher = new byte[] { 0xDE, 0xAD, 0xBE, 0xEF };

                var mockValidator = new MockPasswordValidator(shouldAccept: true);
                var linker = new PasswordLinker(mockValidator, customEncrypt: plaintext =>
                {
                    // Capture a copy to verify exact framing
                    capturedPlaintext = (byte[])plaintext.Clone();
                    return mockCipher;
                });

                string testPassword = "HelloPeekSecurity";
                using var password = CreateSecureString(testPassword);
                bool result = linker.LinkPassword("profile_custom", password, "testuser", tempDir);

                Assert.True(result);
                Assert.NotNull(capturedPlaintext);

                // Verify captured buffer format: [DWORD length][UTF-16LE bytes]
                uint lengthPrefix = BinaryPrimitives.ReadUInt32LittleEndian(capturedPlaintext.AsSpan(0, 4));
                Assert.Equal((uint)(testPassword.Length * 2), lengthPrefix);
                string decoded = Encoding.Unicode.GetString(capturedPlaintext, 4, (int)lengthPrefix);
                Assert.Equal(testPassword, decoded);

                // Verify written file is the exact mock ciphertext
                string secretPath = Path.Combine(tempDir, "profile_custom.secret");
                byte[] written = File.ReadAllBytes(secretPath);
                Assert.Equal(mockCipher, written);
            }
            finally
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
        }

        [Theory]
        [InlineData("", "password123")]
        [InlineData("   ", "password123")]
        [InlineData(null, "password123")]
        public void InvalidProfileId_ReturnsFalseWithoutCallingValidator(string? profileId, string pass)
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTest_" + Guid.NewGuid().ToString("N"));
            try
            {
                var mockValidator = new MockPasswordValidator(shouldAccept: true);
                var linker = new PasswordLinker(mockValidator);

                using var password = CreateSecureString(pass);
                bool result = linker.LinkPassword(profileId!, password, "testuser", tempDir);

                Assert.False(result);
                Assert.Equal(0, mockValidator.ValidateCallCount);
                if (Directory.Exists(tempDir))
                {
                    Assert.Empty(Directory.GetFiles(tempDir));
                }
            }
            finally
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
        }

        [Fact]
        public void EmptyPassword_ReturnsFalseWithoutCallingValidator()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTest_" + Guid.NewGuid().ToString("N"));
            try
            {
                var mockValidator = new MockPasswordValidator(shouldAccept: true);
                var linker = new PasswordLinker(mockValidator);

                using var password = new SecureString();
                bool result = linker.LinkPassword("profile_1", password, "testuser", tempDir);

                Assert.False(result);
                Assert.Equal(0, mockValidator.ValidateCallCount);
            }
            finally
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
        }
    }
}
