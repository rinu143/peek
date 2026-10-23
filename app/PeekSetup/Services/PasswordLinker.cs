using System;
using System.IO;
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

        public PasswordLinker(
            IPasswordValidator? validator = null,
            ILsaSecretStore? secretStore = null,
            IAccountSidResolver? sidResolver = null)
        {
            _validator = validator ?? new WindowsPasswordValidator();
            _secretStore = secretStore ?? new LsaSecretStore();
            _sidResolver = sidResolver ?? new AccountSidResolver();
        }

        public bool LinkPassword(string profileId, SecureString password, string? username = null, string? sid = null)
        {
            if (string.IsNullOrWhiteSpace(profileId) || password == null || password.Length == 0)
            {
                return false;
            }

            string user = string.IsNullOrWhiteSpace(username) ? Environment.UserName : username;
            string targetSid = sid ?? WindowsIdentity.GetCurrent().User?.Value ?? string.Empty;

            if (string.IsNullOrEmpty(targetSid))
            {
                return false;
            }

            // 1. Resolve and validate that username maps to targetSid
            if (!_sidResolver.ValidateUserSid(user, targetSid))
            {
                return false;
            }

            // 2. Verify password for TARGET user with LogonUserW before accepting
            if (!_validator.Validate(user, password))
            {
                return false;
            }

            // 3. Build binary payload:
            // [DWORD version (=1)]
            // [DWORD profileIdBytes] + [UTF-16LE profile_id]
            // [DWORD passwordBytes] + [UTF-16LE password]
            byte[] payload = SecretPayloadBuilder.BuildPayload(profileId, password);

            try
            {
                // 4. Store in Windows LSA under L$PeekCred_<targetSid>
                string keyName = SecretPayloadBuilder.GetSecretKeyName(targetSid);
                bool success = _secretStore.StoreSecret(keyName, payload);

                if (success)
                {
                    // Clean up obsolete legacy DPAPI .secret file if present
                    DeleteLegacySecretFile(profileId);
                }

                return success;
            }
            finally
            {
                // CRITICAL SECURITY REQUIREMENT:
                // Zero plaintext payload memory immediately after use
                CryptographicOperations.ZeroMemory(payload);
            }
        }

        public bool UnlinkPassword(string sid)
        {
            if (string.IsNullOrWhiteSpace(sid))
            {
                return false;
            }

            string keyName = SecretPayloadBuilder.GetSecretKeyName(sid);
            return _secretStore.DeleteSecret(keyName);
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
                // Non-fatal cleanup
            }
        }
    }
}
