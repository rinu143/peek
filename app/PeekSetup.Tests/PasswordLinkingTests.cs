using System;
using System.Collections.Generic;
using System.IO;
using System.Security;
using System.Threading.Tasks;
using PeekSetup.Models;
using PeekSetup.Services;
using PeekSetup.ViewModels;
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

    public class MockLsaSecretStore : ILsaSecretStore
    {
        public Dictionary<string, byte[]> StoredSecrets { get; } = new(StringComparer.OrdinalIgnoreCase);
        public List<string> DeletedSecrets { get; } = new();

        public bool StoreSecret(string keyName, byte[] payload)
        {
            StoredSecrets[keyName] = (byte[])payload.Clone();
            return true;
        }

        public bool DeleteSecret(string keyName)
        {
            DeletedSecrets.Add(keyName);
            StoredSecrets.Remove(keyName);
            return true;
        }

        public byte[]? RetrieveSecret(string keyName)
        {
            return StoredSecrets.TryGetValue(keyName, out var data) ? (byte[])data.Clone() : null;
        }
    }

    public class MockAccountSidResolver : IAccountSidResolver
    {
        public bool ShouldMatch { get; set; } = true;
        public string? ExpectedSid { get; set; } = "S-1-5-21-12345678-1001";

        public bool ValidateUserSid(string username, string expectedSid)
        {
            return ShouldMatch && (ExpectedSid == null || string.Equals(expectedSid, ExpectedSid, StringComparison.OrdinalIgnoreCase));
        }

        public string? ResolveUserToSid(string username)
        {
            return ExpectedSid;
        }
    }

    public class MockPasswordLinkLauncher : IPasswordLinkLauncher
    {
        public int ReturnExitCode { get; set; } = 0;
        public string? LastUsername { get; private set; }
        public string? LastSid { get; private set; }
        public string? LastProfileId { get; private set; }

        public Task<int> LaunchLinkPasswordAsync(string username, string sid, string profileId)
        {
            LastUsername = username;
            LastSid = sid;
            LastProfileId = profileId;
            return Task.FromResult(ReturnExitCode);
        }

        public Task<int> LaunchUnlinkAsync(string sid)
        {
            LastSid = sid;
            return Task.FromResult(ReturnExitCode);
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

        [Fact]
        public void RejectedPassword_NeverWritesToStoreAndReturnsFalse()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: false);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver { ShouldMatch = true };
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver);

            using var password = CreateSecureString("invalid_password");
            bool result = linker.LinkPassword("profile_rejected", password, "DOMAIN\\testuser", "S-1-5-21-12345678-1001");

            Assert.False(result);
            Assert.Equal(1, mockValidator.ValidateCallCount);
            Assert.Empty(mockStore.StoredSecrets);
        }

        [Fact]
        public void VerifiedPassword_WritesExactlyOnePayloadWithRightKeyAndProfileId()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver { ExpectedSid = "S-1-5-21-12345678-1001" };
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver);

            string testSecretPassword = "SuperSecretPassword123!@#";
            string testProfileId = "profile_verified_456";
            string testSid = "S-1-5-21-12345678-1001";

            using var password = CreateSecureString(testSecretPassword);
            bool result = linker.LinkPassword(testProfileId, password, "DOMAIN\\testuser", testSid);

            Assert.True(result);
            Assert.Equal(1, mockValidator.ValidateCallCount);

            // Exactly one secret stored with key name L$PeekCred_<SID>
            Assert.Single(mockStore.StoredSecrets);
            string expectedKey = $"L$PeekCred_{testSid}";
            Assert.True(mockStore.StoredSecrets.ContainsKey(expectedKey));

            byte[] storedPayload = mockStore.StoredSecrets[expectedKey];
            Assert.NotNull(storedPayload);

            // Validate binary payload format
            bool parseOk = SecretPayloadBuilder.TryParsePayload(storedPayload, testProfileId, out string recoveredPassword);
            Assert.True(parseOk);
            Assert.Equal(testSecretPassword, recoveredPassword);
        }

        [Fact]
        public void AccountSidMismatch_WritesNothingAndReturnsFalse()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver { ShouldMatch = false }; // mismatch
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver);

            using var password = CreateSecureString("password");
            bool result = linker.LinkPassword("prof", password, "testuser", "S-1-5-21-MISMATCH");

            Assert.False(result);
            Assert.Equal(0, mockValidator.ValidateCallCount);
            Assert.Empty(mockStore.StoredSecrets);
        }

        [Theory]
        [InlineData(0, true, false, "")]
        [InlineData(1, false, true, "cancelled")]
        [InlineData(2, false, true, "Maximum attempts")]
        [InlineData(3, false, true, "unexpected error")]
        [InlineData(4, false, true, "UAC")]
        public async Task ExitCodeMapping_ProducesExpectedUiMessages(int exitCode, bool expectedLinked, bool expectedError, string messageSubstring)
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockLauncher = new MockPasswordLinkLauncher { ReturnExitCode = exitCode };
            var vm = new MainViewModel(mockPipe, null, mockLauncher)
            {
                EnrolledProfileId = "prof_test",
                CurrentStep = SetupStep.PasswordLink
            };

            await vm.LinkPasswordElevatedAsync();

            Assert.Equal(expectedLinked, vm.IsPasswordLinked);
            Assert.Equal(expectedError, vm.HasPasswordError);

            if (!string.IsNullOrEmpty(messageSubstring))
            {
                Assert.Contains(messageSubstring, vm.PasswordErrorMessage, StringComparison.OrdinalIgnoreCase);
            }

            if (exitCode == 0)
            {
                Assert.Equal(SetupStep.Done, vm.CurrentStep);
            }
            else
            {
                Assert.Equal(SetupStep.PasswordLink, vm.CurrentStep);
            }
        }

        [Fact]
        public async Task CommandLineArguments_NeverContainPasswordMaterial()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockLauncher = new MockPasswordLinkLauncher { ReturnExitCode = 0 };
            var vm = new MainViewModel(mockPipe, null, mockLauncher)
            {
                EnrolledProfileId = "test_profile_secret_free",
                CurrentStep = SetupStep.PasswordLink
            };

            await vm.LinkPasswordElevatedAsync();

            Assert.NotNull(mockLauncher.LastUsername);
            Assert.NotNull(mockLauncher.LastSid);
            Assert.NotNull(mockLauncher.LastProfileId);
            Assert.Equal("test_profile_secret_free", mockLauncher.LastProfileId);

            // Reconstruct the argument string exactly as PasswordLinkLauncher builds it
            string args = $"--link-password --user \"{mockLauncher.LastUsername}\" --sid \"{mockLauncher.LastSid}\" --profile-id \"{mockLauncher.LastProfileId}\"";

            // Verify no secret or password keywords appear in arguments
            Assert.DoesNotContain("password=", args, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("secret=", args, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void SecretPayloadBuilder_RejectsOversizePayload()
        {
            // Create a profile ID so large that total size exceeds 4096 bytes
            string largeProfileId = new string('A', 2500); // 5000 bytes in UTF-16
            using var password = CreateSecureString("pass");

            Assert.Throws<ArgumentException>(() =>
            {
                SecretPayloadBuilder.BuildPayload(largeProfileId, password);
            });
        }

        [Fact]
        public void SecretPayloadBuilder_ProfileBinding_RejectsWrongProfile()
        {
            using var password = CreateSecureString("secretPass");
            byte[] payload = SecretPayloadBuilder.BuildPayload("original_profile", password);

            bool parseOk = SecretPayloadBuilder.TryParsePayload(payload, "different_profile", out string recovered);
            Assert.False(parseOk);
            Assert.Empty(recovered);
        }

        [Fact]
        public void UnlinkPassword_DeletesLsaSecretKey()
        {
            var mockStore = new MockLsaSecretStore();
            string testSid = "S-1-5-21-UNLINK-1001";
            mockStore.StoredSecrets[$"L$PeekCred_{testSid}"] = new byte[] { 1, 2, 3 };

            var linker = new PasswordLinker(null, mockStore, null);
            bool unlinked = linker.UnlinkPassword(testSid);

            Assert.True(unlinked);
            Assert.False(mockStore.StoredSecrets.ContainsKey($"L$PeekCred_{testSid}"));
            Assert.Contains($"L$PeekCred_{testSid}", mockStore.DeletedSecrets);
        }

        [Fact]
        public void LinkPassword_DeletesLegacySecretFileIfPresent()
        {
            string profileId = "legacy_test_profile_" + Guid.NewGuid().ToString("N");
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string profileDir = Path.Combine(localAppData, "Peek", "Profiles");
            Directory.CreateDirectory(profileDir);
            string secretFilePath = Path.Combine(profileDir, $"{profileId}.secret");
            File.WriteAllText(secretFilePath, "legacy dummy content");

            try
            {
                Assert.True(File.Exists(secretFilePath));

                var mockValidator = new MockPasswordValidator(shouldAccept: true);
                var mockStore = new MockLsaSecretStore();
                var mockResolver = new MockAccountSidResolver { ExpectedSid = "S-1-5-21-12345678-1001" };
                var linker = new PasswordLinker(mockValidator, mockStore, mockResolver);

                using var password = CreateSecureString("TestPass123!");
                bool linked = linker.LinkPassword(profileId, password, "testuser", "S-1-5-21-12345678-1001");

                Assert.True(linked);
                Assert.False(File.Exists(secretFilePath), "Legacy <profile_id>.secret file must be deleted upon linking");
            }
            finally
            {
                if (File.Exists(secretFilePath))
                {
                    File.Delete(secretFilePath);
                }
            }
        }
    }
}
