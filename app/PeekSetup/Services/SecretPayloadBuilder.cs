using System;
using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Security;
using System.Text;

namespace PeekSetup.Services
{
    public static class SecretPayloadBuilder
    {
        public const uint CurrentVersion = 2;
        public const uint LegacyVersion = 1;
        public const int MaxPayloadSize = 4096;

        public static string GetSecretKeyName(string userSid)
        {
            return $"L$PeekCred_{userSid}";
        }

        public static byte[] BuildPayload(SecureString password)
        {
            if (password == null || password.Length == 0)
                throw new ArgumentException("password cannot be null or empty", nameof(password));

            int passwordBytes = password.Length * sizeof(char);
            int totalSize = sizeof(uint) * 2 + passwordBytes; // [version=2][passwordBytes][password]

            if (totalSize > MaxPayloadSize)
            {
                throw new ArgumentException($"Payload size {totalSize} exceeds maximum cap of {MaxPayloadSize} bytes");
            }

            byte[] payload = new byte[totalSize];
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(0, 4), CurrentVersion);
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(4, 4), (uint)passwordBytes);

            IntPtr pPassword = IntPtr.Zero;
            try
            {
                pPassword = Marshal.SecureStringToGlobalAllocUnicode(password);
                Marshal.Copy(pPassword, payload, 8, passwordBytes);
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

        public static byte[] BuildPayload(string profileId, SecureString password)
        {
            // Writers always write v2
            return BuildPayload(password);
        }

        public static byte[] BuildPayloadV1(string profileId, SecureString password)
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
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(0, 4), LegacyVersion);
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

        public static bool TryParsePayload(byte[] payload, out string password)
        {
            password = string.Empty;
            if (payload == null || payload.Length < 8 || payload.Length > MaxPayloadSize)
                return false;

            uint version = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(0, 4));
            if (version == CurrentVersion)
            {
                uint passwordBytes = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(4, 4));
                if (passwordBytes == 0 || (passwordBytes % sizeof(char)) != 0)
                    return false;

                if (payload.Length != 8 + (int)passwordBytes)
                    return false;

                password = Encoding.Unicode.GetString(payload, 8, (int)passwordBytes);
                return true;
            }
            else if (version == LegacyVersion)
            {
                if (payload.Length < 12)
                    return false;

                uint profileIdBytes = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(4, 4));
                if (profileIdBytes == 0 || (profileIdBytes % sizeof(char)) != 0)
                    return false;

                int pwOffset = 8 + (int)profileIdBytes;
                if (payload.Length < pwOffset + 4)
                    return false;

                uint passwordBytes = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(pwOffset, 4));
                if (passwordBytes == 0 || (passwordBytes % sizeof(char)) != 0)
                    return false;

                if (payload.Length != pwOffset + 4 + (int)passwordBytes)
                    return false;

                password = Encoding.Unicode.GetString(payload, pwOffset + 4, (int)passwordBytes);
                return true;
            }

            return false;
        }

        public static bool TryParsePayload(byte[] payload, string expectedProfileId, out string password)
        {
            password = string.Empty;
            if (payload == null || payload.Length < 8 || payload.Length > MaxPayloadSize)
                return false;

            uint version = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(0, 4));
            if (version == CurrentVersion)
            {
                // For v2, profile ID is not stored in payload; callers verify authorization against registry
                return TryParsePayload(payload, out password);
            }
            else if (version == LegacyVersion)
            {
                if (payload.Length < 12)
                    return false;

                uint profileIdBytes = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(4, 4));
                if (profileIdBytes == 0 || (profileIdBytes % sizeof(char)) != 0)
                    return false;

                int pwOffset = 8 + (int)profileIdBytes;
                if (payload.Length < pwOffset + 4)
                    return false;

                string storedProfileId = Encoding.Unicode.GetString(payload, 8, (int)profileIdBytes);
                if (!string.Equals(storedProfileId, expectedProfileId, StringComparison.Ordinal))
                    return false;

                uint passwordBytes = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(pwOffset, 4));
                if (passwordBytes == 0 || (passwordBytes % sizeof(char)) != 0)
                    return false;

                if (payload.Length != pwOffset + 4 + (int)passwordBytes)
                    return false;

                password = Encoding.Unicode.GetString(payload, pwOffset + 4, (int)passwordBytes);
                return true;
            }

            return false;
        }
    }
}
