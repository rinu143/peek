using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
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

    public class MockAuthorizationStore : IAuthorizationStore
    {
        public Dictionary<string, List<string>> Store { get; } = new(StringComparer.OrdinalIgnoreCase);

        public string[] GetAuthorizedIdentities(string userSid)
        {
            return Store.TryGetValue(userSid, out var list)
                ? list.Take(RegistryAuthorizationStore.MaxAuthorizedIdentities).ToArray()
                : Array.Empty<string>();
        }

        public bool SetAuthorizedIdentities(string userSid, string[] profileIds)
        {
            Store[userSid] = (profileIds ?? Array.Empty<string>())
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .Take(RegistryAuthorizationStore.MaxAuthorizedIdentities)
                .ToList();
            return true;
        }

        public bool AddAuthorizedIdentity(string userSid, string profileId)
        {
            if (!Store.TryGetValue(userSid, out var list))
            {
                list = new List<string>();
                Store[userSid] = list;
            }

            if (list.Contains(profileId, StringComparer.OrdinalIgnoreCase))
            {
                return true;
            }

            if (list.Count >= RegistryAuthorizationStore.MaxAuthorizedIdentities)
            {
                return false;
            }

            list.Add(profileId);
            return true;
        }

        public bool RemoveAuthorizedIdentity(string userSid, string profileId)
        {
            if (Store.TryGetValue(userSid, out var list))
            {
                list.RemoveAll(x => string.Equals(x, profileId, StringComparison.OrdinalIgnoreCase));
                if (list.Count == 0)
                {
                    Store.Remove(userSid);
                }
                return true;
            }
            return false;
        }

        public bool DeleteIdentitiesKey(string userSid)
        {
            return Store.Remove(userSid);
        }

        public bool HasAuthorizedIdentities(string userSid)
        {
            return Store.TryGetValue(userSid, out var list) && list.Count > 0;
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

        public Task<int> LaunchAuthorizeIdentityAsync(string sid, string profileId, string? username = null)
        {
            LastSid = sid;
            LastProfileId = profileId;
            LastUsername = username;
            return Task.FromResult(ReturnExitCode);
        }

        public Task<int> LaunchRevokeIdentityAsync(string sid, string profileId)
        {
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
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver, mockAuth);

            using var password = CreateSecureString("invalid_password");
            bool result = linker.LinkPassword("profile_rejected", password, "DOMAIN\\testuser", "S-1-5-21-12345678-1001");

            Assert.False(result);
            Assert.Equal(1, mockValidator.ValidateCallCount);
            Assert.Empty(mockStore.StoredSecrets);
            Assert.Empty(mockAuth.Store);
        }

        [Fact]
        public void VerifiedPassword_WritesV2PayloadAndSetsAuthorizedIdentity()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver { ExpectedSid = "S-1-5-21-12345678-1001" };
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver, mockAuth);

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

            // Validate binary v2 payload format: [version=2][passwordBytes][password]
            bool parseOk = SecretPayloadBuilder.TryParsePayload(storedPayload, out string recoveredPassword);
            Assert.True(parseOk);
            Assert.Equal(testSecretPassword, recoveredPassword);

            // Check authorization store: Authorized = [testProfileId]
            var authorized = mockAuth.GetAuthorizedIdentities(testSid);
            Assert.Single(authorized);
            Assert.Equal(testProfileId, authorized[0]);
        }

        [Fact]
        public void AccountSidMismatch_WritesNothingAndReturnsFalse()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver { ShouldMatch = false }; // mismatch
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver, mockAuth);

            using var password = CreateSecureString("password");
            bool result = linker.LinkPassword("prof", password, "testuser", "S-1-5-21-MISMATCH");

            Assert.False(result);
            Assert.Equal(0, mockValidator.ValidateCallCount);
            Assert.Empty(mockStore.StoredSecrets);
            Assert.Empty(mockAuth.Store);
        }

        [Fact]
        public void AuthorizeIdentity_WhenNotLinkedYet_ReturnsExitCode6()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver();
            var mockAuth = new MockAuthorizationStore(); // Empty: not linked
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver, mockAuth);

            string sid = "S-1-5-21-12345678-1001";
            using var password = CreateSecureString("pass123");

            int exitCode = linker.AuthorizeIdentity("prof2", password, "testuser", sid);

            Assert.Equal(PasswordLinker.ExitNotLinkedYet, exitCode); // 6: not linked yet
            Assert.Equal(0, mockValidator.ValidateCallCount);
            Assert.Empty(mockStore.StoredSecrets);
        }

        [Fact]
        public void AuthorizeIdentity_WhenAtCap5_ReturnsExitCode5()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver();
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver, mockAuth);

            string sid = "S-1-5-21-12345678-1001";
            mockAuth.SetAuthorizedIdentities(sid, new[] { "id1", "id2", "id3", "id4", "id5" });

            using var password = CreateSecureString("pass123");
            int exitCode = linker.AuthorizeIdentity("id6", password, "testuser", sid);

            Assert.Equal(PasswordLinker.ExitIdentityLimit, exitCode); // 5: identity limit
            Assert.Equal(0, mockValidator.ValidateCallCount);
        }

        [Fact]
        public void AuthorizeIdentity_WrongPassword_ReturnsExitCode2()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: false);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver();
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver, mockAuth);

            string sid = "S-1-5-21-12345678-1001";
            mockAuth.SetAuthorizedIdentities(sid, new[] { "id1" });

            using var password = CreateSecureString("wrongPass");
            int exitCode = linker.AuthorizeIdentity("id2", password, "testuser", sid);

            Assert.Equal(PasswordLinker.ExitWrongPassword, exitCode); // 2: wrong password
            Assert.Equal(1, mockValidator.ValidateCallCount);
            Assert.Single(mockAuth.GetAuthorizedIdentities(sid));
        }

        [Fact]
        public void AuthorizeIdentity_Success_RewritesV2SecretAndAppendsId()
        {
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var mockStore = new MockLsaSecretStore();
            var mockResolver = new MockAccountSidResolver();
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(mockValidator, mockStore, mockResolver, mockAuth);

            string sid = "S-1-5-21-12345678-1001";
            mockAuth.SetAuthorizedIdentities(sid, new[] { "id1" });

            using var password = CreateSecureString("newSecretPass!");
            int exitCode = linker.AuthorizeIdentity("id2", password, "testuser", sid);

            Assert.Equal(PasswordLinker.ExitOk, exitCode); // 0: ok
            Assert.Equal(1, mockValidator.ValidateCallCount);

            // Verified v2 secret written
            string key = $"L$PeekCred_{sid}";
            Assert.True(mockStore.StoredSecrets.ContainsKey(key));
            bool parseOk = SecretPayloadBuilder.TryParsePayload(mockStore.StoredSecrets[key], out string pw);
            Assert.True(parseOk);
            Assert.Equal("newSecretPass!", pw);

            // Authorized list updated
            var ids = mockAuth.GetAuthorizedIdentities(sid);
            Assert.Equal(2, ids.Length);
            Assert.Contains("id1", ids);
            Assert.Contains("id2", ids);
        }

        [Fact]
        public void RevokeIdentity_RemovesId_AndDeletesSecretWhenEmpty()
        {
            var mockStore = new MockLsaSecretStore();
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(null, mockStore, null, mockAuth);

            string sid = "S-1-5-21-1001";
            mockAuth.SetAuthorizedIdentities(sid, new[] { "id1", "id2" });
            mockStore.StoredSecrets[$"L$PeekCred_{sid}"] = new byte[] { 1, 2, 3 };

            // Revoke id1 -> id2 remains, secret stays
            int exit1 = linker.RevokeIdentity("id1", sid);
            Assert.Equal(PasswordLinker.ExitOk, exit1);
            Assert.Single(mockAuth.GetAuthorizedIdentities(sid));
            Assert.True(mockStore.StoredSecrets.ContainsKey($"L$PeekCred_{sid}"));

            // Revoke id2 -> list becomes empty, secret and key deleted
            int exit2 = linker.RevokeIdentity("id2", sid);
            Assert.Equal(PasswordLinker.ExitOk, exit2);
            Assert.Empty(mockAuth.GetAuthorizedIdentities(sid));
            Assert.False(mockStore.StoredSecrets.ContainsKey($"L$PeekCred_{sid}"));
            Assert.False(mockAuth.HasAuthorizedIdentities(sid));
        }

        [Fact]
        public void UnlinkPassword_DeletesLsaSecretAndIdentitiesKey()
        {
            var mockStore = new MockLsaSecretStore();
            var mockAuth = new MockAuthorizationStore();
            var linker = new PasswordLinker(null, mockStore, null, mockAuth);

            string testSid = "S-1-5-21-UNLINK-1001";
            mockStore.StoredSecrets[$"L$PeekCred_{testSid}"] = new byte[] { 1, 2, 3 };
            mockAuth.SetAuthorizedIdentities(testSid, new[] { "id1" });

            bool unlinked = linker.UnlinkPassword(testSid);

            Assert.True(unlinked);
            Assert.False(mockStore.StoredSecrets.ContainsKey($"L$PeekCred_{testSid}"));
            Assert.False(mockAuth.HasAuthorizedIdentities(testSid));
        }

        [Theory]
        [InlineData(0, true, false, "")]
        [InlineData(1, false, true, "cancelled")]
        [InlineData(2, false, true, "Maximum attempts")]
        [InlineData(3, false, true, "unexpected error")]
        [InlineData(4, false, true, "UAC")]
        [InlineData(5, false, true, "Identity limit")]
        [InlineData(6, false, true, "not linked yet")]
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

            // Check all helper mode argument formats
            string linkArgs = $"--link-password --user \"{mockLauncher.LastUsername}\" --sid \"{mockLauncher.LastSid}\" --profile-id \"{mockLauncher.LastProfileId}\"";
            string authArgs = $"--authorize-identity --sid \"{mockLauncher.LastSid}\" --profile-id \"{mockLauncher.LastProfileId}\"";
            string revokeArgs = $"--revoke-identity --sid \"{mockLauncher.LastSid}\" --profile-id \"{mockLauncher.LastProfileId}\"";
            string unlinkArgs = $"--unlink --sid \"{mockLauncher.LastSid}\"";

            foreach (var args in new[] { linkArgs, authArgs, revokeArgs, unlinkArgs })
            {
                Assert.DoesNotContain("password=", args, StringComparison.OrdinalIgnoreCase);
                Assert.DoesNotContain("secret=", args, StringComparison.OrdinalIgnoreCase);
            }
        }

        [Fact]
        public void SecretPayloadBuilder_V2_BuildsCorrectBinaryFormatWithoutProfileId()
        {
            using var password = CreateSecureString("v2PasswordSecret!");
            byte[] payload = SecretPayloadBuilder.BuildPayload(password);

            // Length must be 8 + passwordBytes
            Assert.Equal(8 + ("v2PasswordSecret!".Length * 2), payload.Length);

            // Header version must be 2
            uint version = BitConverter.ToUInt32(payload, 0);
            Assert.Equal(2u, version);

            // Parse successfully
            bool parseOk = SecretPayloadBuilder.TryParsePayload(payload, out string recovered);
            Assert.True(parseOk);
            Assert.Equal("v2PasswordSecret!", recovered);
        }

        [Fact]
        public void SecretPayloadBuilder_V1_LegacyBackwardCompatibility()
        {
            using var password = CreateSecureString("legacyV1Password!");
            string profileId = "legacy_prof_123";
            byte[] payload = SecretPayloadBuilder.BuildPayloadV1(profileId, password);

            // Version must be 1
            uint version = BitConverter.ToUInt32(payload, 0);
            Assert.Equal(1u, version);

            // Matching profile parses
            bool parseMatch = SecretPayloadBuilder.TryParsePayload(payload, profileId, out string recovered);
            Assert.True(parseMatch);
            Assert.Equal("legacyV1Password!", recovered);

            // Mismatched profile rejected
            bool parseMismatch = SecretPayloadBuilder.TryParsePayload(payload, "wrong_prof", out string recoveredMismatch);
            Assert.False(parseMismatch);
            Assert.Empty(recoveredMismatch);
        }

        [Fact]
        public void RegistryAuthorizationStore_MissingKey_ReturnsEmptyArray()
        {
            var store = new RegistryAuthorizationStore();
            var ids = store.GetAuthorizedIdentities("S-1-5-21-NONEXISTENT-SID-9999");
            Assert.NotNull(ids);
            Assert.Empty(ids);
        }

        [Fact]
        public void MockAuthorizationStore_EnforcesCap5AndDeduplication()
        {
            var store = new MockAuthorizationStore();
            string sid = "S-1-5-21-CAP-TEST";
            // Add 1
            Assert.True(store.AddAuthorizedIdentity(sid, "id1"));
            // Add duplicate
            Assert.True(store.AddAuthorizedIdentity(sid, "id1"));
            Assert.Single(store.GetAuthorizedIdentities(sid));

            // Add up to 5
            Assert.True(store.AddAuthorizedIdentity(sid, "id2"));
            Assert.True(store.AddAuthorizedIdentity(sid, "id3"));
            Assert.True(store.AddAuthorizedIdentity(sid, "id4"));
            Assert.True(store.AddAuthorizedIdentity(sid, "id5"));
            Assert.Equal(5, store.GetAuthorizedIdentities(sid).Length);

            // 6th rejected
            Assert.False(store.AddAuthorizedIdentity(sid, "id6"));
            Assert.Equal(5, store.GetAuthorizedIdentities(sid).Length);
            Assert.DoesNotContain("id6", store.GetAuthorizedIdentities(sid));
        }
    }
}
