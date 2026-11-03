using System;
using System.IO;
using System.Linq;
using System.Security;
using System.Security.Cryptography;
using System.Security.Principal;

namespace PeekSetup.Services
{
    public class PasswordLinker : IPasswordLinker
    {
        private readonly IPasswordValidator _validator;
        private readonly ILsaSecretStore _secretStore;
        private readonly IAccountSidResolver _sidResolver;
        private readonly IAuthorizationStore _authStore;

        public const int ExitOk = 0;
        public const int ExitCancelled = 1;
        public const int ExitWrongPassword = 2;
        public const int ExitError = 3;
        public const int ExitUacDeclined = 4;
        public const int ExitIdentityLimit = 5;
        public const int ExitNotLinkedYet = 6;

        public PasswordLinker(
            IPasswordValidator? validator = null,
            ILsaSecretStore? secretStore = null,
            IAccountSidResolver? sidResolver = null,
            IAuthorizationStore? authStore = null)
        {
            _validator = validator ?? new WindowsPasswordValidator();
            _secretStore = secretStore ?? new LsaSecretStore();
            _sidResolver = sidResolver ?? new AccountSidResolver();
            _authStore = authStore ?? new RegistryAuthorizationStore();
        }

        public int LinkPassword(string profileId, SecureString password, string? username = null, string? sid = null)
        {
            if (string.IsNullOrWhiteSpace(profileId) || password == null || password.Length == 0)
            {
                return ExitError;
            }

            string user = string.IsNullOrWhiteSpace(username) ? Environment.UserName : username;
            string targetSid = sid ?? WindowsIdentity.GetCurrent().User?.Value ?? string.Empty;

            if (string.IsNullOrEmpty(targetSid))
            {
                return ExitError;
            }

            // 1. Resolve and validate that username maps to targetSid
            if (!_sidResolver.ValidateUserSid(user, targetSid))
            {
                return ExitError;
            }

            // 2. Check identity limit: keep existing ids, add profileId (dedupe)
            var existing = _authStore.GetAuthorizedIdentities(targetSid);
            var merged = new System.Collections.Generic.List<string>();
            if (existing != null)
            {
                foreach (var id in existing)
                {
                    if (!string.IsNullOrWhiteSpace(id) && !merged.Contains(id, StringComparer.OrdinalIgnoreCase))
                    {
                        merged.Add(id);
                    }
                }
            }

            if (!merged.Contains(profileId, StringComparer.OrdinalIgnoreCase))
            {
                merged.Add(profileId);
            }

            if (merged.Count > RegistryAuthorizationStore.MaxAuthorizedIdentities)
            {
                return ExitIdentityLimit; // 5: identity limit reached instead of truncating
            }

            // 3. Verify password for TARGET user with LogonUserW before accepting
            if (!_validator.Validate(user, password))
            {
                return ExitWrongPassword; // 2
            }

            // 4. Build binary v2 payload: [DWORD version=2][DWORD passwordBytes][UTF-16LE password]
            byte[] payload = SecretPayloadBuilder.BuildPayload(password);

            try
            {
                // 5. Store in Windows LSA under L$PeekCred_<targetSid>
                string keyName = SecretPayloadBuilder.GetSecretKeyName(targetSid);
                bool success = _secretStore.StoreSecret(keyName, payload);

                if (!success)
                {
                    return ExitError; // 3
                }

                // 6. Save merged authorized identities
                bool authSaved = _authStore.SetAuthorizedIdentities(targetSid, merged.ToArray());
                if (!authSaved)
                {
                    return ExitError; // 3
                }

                // Clean up obsolete legacy DPAPI .secret file if present
                DeleteLegacySecretFile(profileId);

                return ExitOk; // 0
            }
            finally
            {
                // CRITICAL SECURITY REQUIREMENT:
                // Zero plaintext payload memory immediately after use
                CryptographicOperations.ZeroMemory(payload);
            }
        }

        public int AuthorizeIdentity(string profileId, SecureString password, string? username = null, string? sid = null)
        {
            if (string.IsNullOrWhiteSpace(profileId) || password == null || password.Length == 0)
            {
                return ExitError;
            }

            string targetSid = sid ?? WindowsIdentity.GetCurrent().User?.Value ?? string.Empty;
            if (string.IsNullOrEmpty(targetSid))
            {
                return ExitError;
            }

            string user = string.IsNullOrWhiteSpace(username) ? Environment.UserName : username;

            // 1. Resolve and validate that username maps to targetSid if username was supplied
            if (!string.IsNullOrWhiteSpace(username) && !_sidResolver.ValidateUserSid(user, targetSid))
            {
                return ExitError;
            }

            // 2. Require that a link already exists (registry list non-empty, otherwise exit 6)
            var authorized = _authStore.GetAuthorizedIdentities(targetSid);
            if (authorized == null || authorized.Length == 0)
            {
                return ExitNotLinkedYet; // 6: not linked yet
            }

            // 3. Check cap of 5: if not already in list and already 5 identities, return 5
            if (!authorized.Contains(profileId, StringComparer.OrdinalIgnoreCase) && authorized.Length >= RegistryAuthorizationStore.MaxAuthorizedIdentities)
            {
                return ExitIdentityLimit; // 5: identity limit
            }

            // 4. Verify password for target user with LogonUserW (3 attempts handled by caller)
            if (!_validator.Validate(user, password))
            {
                return ExitWrongPassword; // 2: wrong password
            }

            // 5. Rewrite v2 secret with verified password
            byte[] payload = SecretPayloadBuilder.BuildPayload(password);
            try
            {
                string keyName = SecretPayloadBuilder.GetSecretKeyName(targetSid);
                bool success = _secretStore.StoreSecret(keyName, payload);
                if (!success)
                {
                    return ExitError;
                }

                // 6. Append the id (dedupe, cap 5)
                var current = _authStore.GetAuthorizedIdentities(targetSid);
                if (!current.Contains(profileId, StringComparer.OrdinalIgnoreCase) && current.Length >= RegistryAuthorizationStore.MaxAuthorizedIdentities)
                {
                    return ExitIdentityLimit;
                }

                bool added = _authStore.AddAuthorizedIdentity(targetSid, profileId);
                if (!added)
                {
                    var checkAfter = _authStore.GetAuthorizedIdentities(targetSid);
                    if (!checkAfter.Contains(profileId, StringComparer.OrdinalIgnoreCase) && checkAfter.Length >= RegistryAuthorizationStore.MaxAuthorizedIdentities)
                    {
                        return ExitIdentityLimit;
                    }
                    return ExitError; // 3: registry write failure / other error
                }

                DeleteLegacySecretFile(profileId);
                return ExitOk; // 0: ok
            }
            catch
            {
                return ExitError;
            }
            finally
            {
                CryptographicOperations.ZeroMemory(payload);
            }
        }

        public int RevokeIdentity(string profileId, string sid)
        {
            if (string.IsNullOrWhiteSpace(profileId) || string.IsNullOrWhiteSpace(sid))
            {
                return ExitError;
            }

            try
            {
                _authStore.RemoveAuthorizedIdentity(sid, profileId);

                // If the list becomes empty remove the secret as well
                var remaining = _authStore.GetAuthorizedIdentities(sid);
                if (remaining == null || remaining.Length == 0)
                {
                    string keyName = SecretPayloadBuilder.GetSecretKeyName(sid);
                    _secretStore.DeleteSecret(keyName);
                    _authStore.DeleteIdentitiesKey(sid);
                }

                return ExitOk;
            }
            catch
            {
                return ExitError;
            }
        }

        public bool UnlinkPassword(string sid)
        {
            if (string.IsNullOrWhiteSpace(sid))
            {
                return false;
            }

            string keyName = SecretPayloadBuilder.GetSecretKeyName(sid);
            bool secretDeleted = _secretStore.DeleteSecret(keyName);
            bool storeDeleted = _authStore.DeleteIdentitiesKey(sid);

            return secretDeleted || storeDeleted;
        }

        public static void DeleteLegacySecretFile(string profileId)
        {
            try
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string secretFilePath = Path.Combine(localAppData, "Peek", "Profiles", $"{profileId}.secret");
                if (File.Exists(secretFilePath))
                {
                    File.Delete(secretFilePath);
                }
            }
            catch
            {
                // Best-effort cleanup
            }
        }
    }
}
