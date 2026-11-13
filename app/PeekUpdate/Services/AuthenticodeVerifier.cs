using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;

namespace PeekUpdate.Services
{
    public static class AuthenticodeVerifier
    {
        private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);
        private const int WTD_CHOICE_FILE = 1;
        private const int WTD_REVOKE_NONE = 0;
        private const int WTD_UI_NONE = 2;
        private const int WTD_STATEACTION_IGNORE = 0;

        private static readonly Guid WINTRUST_ACTION_GENERIC_VERIFY_V2 = new("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WINTRUST_FILE_INFO
        {
            public uint cbStruct;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string pcwszFilePath;
            public IntPtr hFile;
            public IntPtr pgKnownSubject;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WINTRUST_DATA
        {
            public uint cbStruct;
            public IntPtr pPolicyCallbackData;
            public IntPtr pSIPClientData;
            public uint dwUIChoice;
            public uint fdwRevocationChecks;
            public uint dwUnionChoice;
            public IntPtr pFile;
            public uint dwStateAction;
            public IntPtr hWVTStateData;
            public IntPtr pwszURLReference;
            public uint dwProvFlags;
            public uint dwUIContext;
        }

        [DllImport("wintrust.dll", ExactSpelling = true, SetLastError = false, CharSet = CharSet.Unicode)]
        private static extern int WinVerifyTrust(
            [In] IntPtr hwnd,
            [In] [MarshalAs(UnmanagedType.LPStruct)] Guid pgActionID,
            [In] IntPtr pWVTData);

        public static bool VerifyAuthenticode(string filePath, string? expectedThumbprint, out string errorMessage, bool allowTestOverride = false)
        {
            errorMessage = string.Empty;

            if (!File.Exists(filePath))
            {
                errorMessage = $"Target file does not exist: {filePath}";
                return false;
            }

#if PEEK_TEST_BUILD
            if (allowTestOverride)
            {
                string? testAllowUnsigned = Environment.GetEnvironmentVariable("PEEK_TEST_ALLOW_UNSIGNED");
                if (string.Equals(testAllowUnsigned, "1", StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
#endif

            var fileInfo = new WINTRUST_FILE_INFO
            {
                cbStruct = (uint)Marshal.SizeOf(typeof(WINTRUST_FILE_INFO)),
                pcwszFilePath = filePath,
                hFile = IntPtr.Zero,
                pgKnownSubject = IntPtr.Zero
            };

            IntPtr pFileInfo = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(WINTRUST_FILE_INFO)));
            IntPtr pWVTData = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(WINTRUST_DATA)));

            try
            {
                Marshal.StructureToPtr(fileInfo, pFileInfo, false);

                var wvtData = new WINTRUST_DATA
                {
                    cbStruct = (uint)Marshal.SizeOf(typeof(WINTRUST_DATA)),
                    pPolicyCallbackData = IntPtr.Zero,
                    pSIPClientData = IntPtr.Zero,
                    dwUIChoice = WTD_UI_NONE,
                    fdwRevocationChecks = WTD_REVOKE_NONE,
                    dwUnionChoice = WTD_CHOICE_FILE,
                    pFile = pFileInfo,
                    dwStateAction = WTD_STATEACTION_IGNORE,
                    hWVTStateData = IntPtr.Zero,
                    pwszURLReference = IntPtr.Zero,
                    dwProvFlags = 0x00000040, // WTD_CACHE_ONLY_URL_RETRIEVAL
                    dwUIContext = 0
                };

                Marshal.StructureToPtr(wvtData, pWVTData, false);

                int result = WinVerifyTrust(INVALID_HANDLE_VALUE, WINTRUST_ACTION_GENERIC_VERIFY_V2, pWVTData);
                if (result != 0)
                {
                    errorMessage = $"WinVerifyTrust failed with error 0x{result:X8}. Digital signature is invalid or not trusted.";
                    return false;
                }
            }
            finally
            {
                Marshal.FreeHGlobal(pFileInfo);
                Marshal.FreeHGlobal(pWVTData);
            }

            // Check publisher certificate thumbprint if specified in manifest
            if (!string.IsNullOrWhiteSpace(expectedThumbprint))
            {
                try
                {
                    using var cert = new X509Certificate2(X509Certificate.CreateFromSignedFile(filePath));
                    string actualThumbprint = cert.Thumbprint.Replace(":", "").Replace(" ", "").Trim();
                    string cleanExpected = expectedThumbprint.Replace(":", "").Replace(" ", "").Trim();

                    if (!string.Equals(actualThumbprint, cleanExpected, StringComparison.OrdinalIgnoreCase))
                    {
                        errorMessage = $"Publisher certificate thumbprint mismatch. Expected: {cleanExpected}, Actual: {actualThumbprint}";
                        return false;
                    }
                }
                catch (Exception ex)
                {
                    errorMessage = $"Failed to extract publisher certificate from {filePath}: {ex.Message}";
                    return false;
                }
            }

            return true;
        }
    }
}
