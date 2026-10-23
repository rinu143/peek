using System;
using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Security;
using System.Text;

namespace PeekSetup.Services
{
    public static class SecretPayloadBuilder
    {
        public const uint CurrentVersion = 1;
        public const int MaxPayloadSize = 4096;

        public static string GetSecretKeyName(string userSid)
        {
            return $"L$PeekCred_{userSid}";
        }

        public static byte[] BuildPayload(string profileId, SecureString password)
        {
            if (string.IsNullOrEmpty(profileId))
                throw new ArgumentException("profileId cannot be null or empty", nameof(profileId));
            if (password == null || password.Length == 0)
                throw new ArgumentException("password cannot be null or empty", nameof(password));

            int profileIdBytes = profileId.Length * sizeof(char);
            int passwordBytes = password.Length * sizeof(char);
            int totalSize = sizeof(uint) * 3 + profileIdBytes + passwordBytes;

            if (totalSize > MaxPayloadSize)
            {
                throw new ArgumentException($"Payload size {totalSize} exceeds maximum cap of {MaxPayloadSize} bytes");
            }

            byte[] payload = new byte[totalSize];
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(0, 4), CurrentVersion);
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(4, 4), (uint)profileIdBytes);
            Encoding.Unicode.GetBytes(profileId, 0, profileId.Length, payload, 8);

            int pwOffset = 8 + profileIdBytes;
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(pwOffset, 4), (uint)passwordBytes);

            IntPtr pPassword = IntPtr.Zero;
            try
            {
                pPassword = Marshal.SecureStringToGlobalAllocUnicode(password);
                Marshal.Copy(pPassword, payload, pwOffset + 4, passwordBytes);
                return payload;
            }
            finally
            {
                if (pPassword != IntPtr.Zero)
                {
                    Marshal.ZeroFreeGlobalAllocUnicode(pPassword);
                }
            }
        }

        public static bool TryParsePayload(byte[] payload, string expectedProfileId, out string password)
        {
            password = string.Empty;
            if (payload == null || payload.Length < 12 || payload.Length > MaxPayloadSize)
                return false;

            uint version = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(0, 4));
            if (version != CurrentVersion)
                return false;

            uint profileIdBytes = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(4, 4));
            if (profileIdBytes == 0 || (profileIdBytes % sizeof(char)) != 0)
                return false;

            if (payload.Length < 8 + profileIdBytes + 4)
                return false;

            string storedProfileId = Encoding.Unicode.GetString(payload, 8, (int)profileIdBytes);
            if (!string.Equals(storedProfileId, expectedProfileId, StringComparison.Ordinal))
                return false;

            int pwOffset = 8 + (int)profileIdBytes;
            uint passwordBytes = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(pwOffset, 4));
            if (passwordBytes == 0 || (passwordBytes % sizeof(char)) != 0)
                return false;

            if (payload.Length != pwOffset + 4 + (int)passwordBytes)
                return false;

            password = Encoding.Unicode.GetString(payload, pwOffset + 4, (int)passwordBytes);
            return true;
        }
    }
}
