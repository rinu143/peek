using System;
using System.Buffers.Binary;
using System.IO;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.Cryptography;

namespace PeekSetup.Services
{
    public class PasswordLinker : IPasswordLinker
    {
        private readonly IPasswordValidator _validator;
        private readonly Func<byte[], byte[]>? _customEncrypt;

        public PasswordLinker(IPasswordValidator? validator = null, Func<byte[], byte[]>? customEncrypt = null)
        {
            _validator = validator ?? new WindowsPasswordValidator();
            _customEncrypt = customEncrypt;
        }

        public static string GetDefaultProfilesDirectory()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(localAppData, "Peek", "Profiles");
        }

        public bool LinkPassword(string profileId, SecureString password, string? username = null, string? targetDirectory = null)
        {
            if (string.IsNullOrWhiteSpace(profileId) || password == null || password.Length == 0)
            {
                return false;
            }

            string user = string.IsNullOrWhiteSpace(username) ? Environment.UserName : username;

            // 1. Verify password via IPasswordValidator before accepting
            if (!_validator.Validate(user, password))
            {
                return false;
            }

            string directory = string.IsNullOrWhiteSpace(targetDirectory)
                ? GetDefaultProfilesDirectory()
                : targetDirectory;

            Directory.CreateDirectory(directory);
            string secretFilePath = Path.Combine(directory, $"{profileId}.secret");

            // 2. Build length-prefixed UTF-16LE plaintext buffer:
            // Format: [DWORD byte length (little-endian)][UTF-16LE password bytes]
            int charCount = password.Length;
            int passwordByteLen = charCount * sizeof(char);
            byte[] plaintext = new byte[sizeof(uint) + passwordByteLen];

            BinaryPrimitives.WriteUInt32LittleEndian(plaintext.AsSpan(0, sizeof(uint)), (uint)passwordByteLen);

            IntPtr pPassword = IntPtr.Zero;
            try
            {
                pPassword = Marshal.SecureStringToGlobalAllocUnicode(password);
                Marshal.Copy(pPassword, plaintext, sizeof(uint), passwordByteLen);

                // 3. Encrypt with DPAPI ProtectedData (CurrentUser scope, null entropy)
                byte[] encrypted = _customEncrypt != null
                    ? _customEncrypt(plaintext)
                    : ProtectedData.Protect(plaintext, null, DataProtectionScope.CurrentUser);

                // 4. Write to %LOCALAPPDATA%\Peek\Profiles\<profile_id>.secret
                File.WriteAllBytes(secretFilePath, encrypted);
                return true;
            }
            finally
            {
                // CRITICAL SECURITY REQUIREMENT:
                // Zero plaintext memory immediately after use and free unmanaged buffer
                CryptographicOperations.ZeroMemory(plaintext);
                if (pPassword != IntPtr.Zero)
                {
                    Marshal.ZeroFreeGlobalAllocUnicode(pPassword);
                    pPassword = IntPtr.Zero;
                }
            }
        }
    }
}
