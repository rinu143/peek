using System;
using System.Runtime.InteropServices;

namespace PeekSetup.Services
{
    public class LsaSecretStore : ILsaSecretStore
    {
        private const int POLICY_GET_PRIVATE_INFORMATION = 0x00000004;
        private const int POLICY_CREATE_SECRET = 0x00000020;
        private const int STATUS_SUCCESS = 0;
        private const int STATUS_OBJECT_NAME_NOT_FOUND = unchecked((int)0xC0000034);

        [StructLayout(LayoutKind.Sequential)]
        private struct LSA_UNICODE_STRING
        {
            public ushort Length;
            public ushort MaximumLength;
            public IntPtr Buffer;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct LSA_OBJECT_ATTRIBUTES
        {
            public int Length;
            public IntPtr RootDirectory;
            public IntPtr ObjectName;
            public int Attributes;
            public IntPtr SecurityDescriptor;
            public IntPtr SecurityQualityOfService;
        }

        [DllImport("kernel32.dll", EntryPoint = "RtlZeroMemory")]
        private static extern void ZeroMemory(IntPtr dest, IntPtr size);

        [DllImport("advapi32.dll", SetLastError = true, PreserveSig = true)]
        private static extern int LsaOpenPolicy(
            IntPtr SystemName,
            ref LSA_OBJECT_ATTRIBUTES ObjectAttributes,
            int DesiredAccess,
            out IntPtr PolicyHandle
        );

        [DllImport("advapi32.dll", SetLastError = true, PreserveSig = true)]
        private static extern int LsaStorePrivateData(
            IntPtr PolicyHandle,
            ref LSA_UNICODE_STRING KeyName,
            ref LSA_UNICODE_STRING PrivateData
        );

        [DllImport("advapi32.dll", SetLastError = true, PreserveSig = true)]
        private static extern int LsaStorePrivateData(
            IntPtr PolicyHandle,
            ref LSA_UNICODE_STRING KeyName,
            IntPtr PrivateData
        );

        [DllImport("advapi32.dll", SetLastError = true, PreserveSig = true)]
        private static extern int LsaRetrievePrivateData(
            IntPtr PolicyHandle,
            ref LSA_UNICODE_STRING KeyName,
            out IntPtr PrivateData
        );

        [DllImport("advapi32.dll", SetLastError = true, PreserveSig = true)]
        private static extern int LsaFreeMemory(IntPtr Buffer);

        [DllImport("advapi32.dll", SetLastError = true, PreserveSig = true)]
        private static extern int LsaClose(IntPtr ObjectHandle);

        private static LSA_UNICODE_STRING CreateLsaUnicodeString(string value)
        {
            var lus = new LSA_UNICODE_STRING();
            if (value != null)
            {
                lus.Buffer = Marshal.StringToHGlobalUni(value);
                lus.Length = (ushort)(value.Length * sizeof(char));
                lus.MaximumLength = (ushort)((value.Length + 1) * sizeof(char));
            }
            return lus;
        }

        private static void FreeLsaUnicodeString(ref LSA_UNICODE_STRING lus)
        {
            if (lus.Buffer != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(lus.Buffer);
                lus.Buffer = IntPtr.Zero;
            }
            lus.Length = 0;
            lus.MaximumLength = 0;
        }

        public bool StoreSecret(string keyName, byte[] payload)
        {
            if (string.IsNullOrEmpty(keyName) || payload == null || payload.Length == 0)
            {
                return false;
            }

            var objAttr = new LSA_OBJECT_ATTRIBUTES();
            IntPtr hPolicy = IntPtr.Zero;

            int status = LsaOpenPolicy(IntPtr.Zero, ref objAttr, POLICY_CREATE_SECRET, out hPolicy);
            if (status != STATUS_SUCCESS)
            {
                return false;
            }

            var lusKey = CreateLsaUnicodeString(keyName);
            var lusData = new LSA_UNICODE_STRING
            {
                Length = (ushort)payload.Length,
                MaximumLength = (ushort)payload.Length,
                Buffer = Marshal.AllocHGlobal(payload.Length)
            };

            try
            {
                Marshal.Copy(payload, 0, lusData.Buffer, payload.Length);
                status = LsaStorePrivateData(hPolicy, ref lusKey, ref lusData);
                return status == STATUS_SUCCESS;
            }
            finally
            {
                // CRITICAL SECURITY REQUIREMENT: Zero unmanaged memory before freeing
                if (lusData.Buffer != IntPtr.Zero)
                {
                    ZeroMemory(lusData.Buffer, (IntPtr)payload.Length);
                    Marshal.FreeHGlobal(lusData.Buffer);
                    lusData.Buffer = IntPtr.Zero;
                }
                FreeLsaUnicodeString(ref lusKey);
                if (hPolicy != IntPtr.Zero)
                {
                    LsaClose(hPolicy);
                }
            }
        }

        public bool DeleteSecret(string keyName)
        {
            if (string.IsNullOrEmpty(keyName))
            {
                return false;
            }

            var objAttr = new LSA_OBJECT_ATTRIBUTES();
            IntPtr hPolicy = IntPtr.Zero;

            int status = LsaOpenPolicy(IntPtr.Zero, ref objAttr, POLICY_CREATE_SECRET, out hPolicy);
            if (status != STATUS_SUCCESS)
            {
                return false;
            }

            var lusKey = CreateLsaUnicodeString(keyName);

            try
            {
                // Passing IntPtr.Zero deletes the private data secret
                status = LsaStorePrivateData(hPolicy, ref lusKey, IntPtr.Zero);
                return status == STATUS_SUCCESS || status == STATUS_OBJECT_NAME_NOT_FOUND;
            }
            finally
            {
                FreeLsaUnicodeString(ref lusKey);
                if (hPolicy != IntPtr.Zero)
                {
                    LsaClose(hPolicy);
                }
            }
        }

        public byte[]? RetrieveSecret(string keyName)
        {
            if (string.IsNullOrEmpty(keyName))
            {
                return null;
            }

            var objAttr = new LSA_OBJECT_ATTRIBUTES();
            IntPtr hPolicy = IntPtr.Zero;

            int status = LsaOpenPolicy(IntPtr.Zero, ref objAttr, POLICY_GET_PRIVATE_INFORMATION, out hPolicy);
            if (status != STATUS_SUCCESS)
            {
                return null;
            }

            var lusKey = CreateLsaUnicodeString(keyName);
            IntPtr pPrivateData = IntPtr.Zero;

            try
            {
                status = LsaRetrievePrivateData(hPolicy, ref lusKey, out pPrivateData);
                if (status != STATUS_SUCCESS || pPrivateData == IntPtr.Zero)
                {
                    return null;
                }

                var privateDataStr = Marshal.PtrToStructure<LSA_UNICODE_STRING>(pPrivateData);
                if (privateDataStr.Buffer == IntPtr.Zero || privateDataStr.Length == 0)
                {
                    return null;
                }

                byte[] data = new byte[privateDataStr.Length];
                Marshal.Copy(privateDataStr.Buffer, data, 0, privateDataStr.Length);

                // Zero unmanaged LSA buffer before freeing
                ZeroMemory(privateDataStr.Buffer, (IntPtr)privateDataStr.Length);

                return data;
            }
            finally
            {
                if (pPrivateData != IntPtr.Zero)
                {
                    LsaFreeMemory(pPrivateData);
                }
                FreeLsaUnicodeString(ref lusKey);
                if (hPolicy != IntPtr.Zero)
                {
                    LsaClose(hPolicy);
                }
            }
        }
    }
}
