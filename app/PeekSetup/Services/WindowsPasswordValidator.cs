using System;
using System.Runtime.InteropServices;
using System.Security;

namespace PeekSetup.Services
{
    public sealed class WindowsPasswordValidator : IPasswordValidator
    {
        private const int LOGON32_LOGON_INTERACTIVE = 2;
        private const int LOGON32_PROVIDER_DEFAULT = 0;

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool LogonUserW(
            string lpszUsername,
            string? lpszDomain,
            IntPtr pszPassword,
            int dwLogonType,
            int dwLogonProvider,
            out IntPtr phToken
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr hObject);

        public bool Validate(string username, SecureString password, string? domain = null)
        {
            if (string.IsNullOrWhiteSpace(username) || password == null || password.Length == 0)
            {
                return false;
            }

            // Split DOMAIN\user if username contains domain
            string userOnly = username;
            string? domainOnly = domain;

            if (username.Contains('\\'))
            {
                var parts = username.Split('\\', 2);
                domainOnly = parts[0];
                userOnly = parts[1];
            }

            IntPtr pPassword = IntPtr.Zero;
            IntPtr hToken = IntPtr.Zero;

            try
            {
                pPassword = Marshal.SecureStringToGlobalAllocUnicode(password);
                bool success = LogonUserW(
                    userOnly,
                    string.IsNullOrEmpty(domainOnly) ? null : domainOnly,
                    pPassword,
                    LOGON32_LOGON_INTERACTIVE,
                    LOGON32_PROVIDER_DEFAULT,
                    out hToken
                );

                return success;
            }
            catch
            {
                return false;
            }
            finally
            {
                // CRITICAL SECURITY REQUIREMENT:
                // Close the returned token handle immediately after verification
                if (hToken != IntPtr.Zero)
                {
                    CloseHandle(hToken);
                    hToken = IntPtr.Zero;
                }

                // Zero out and free unmanaged unicode password buffer
                if (pPassword != IntPtr.Zero)
                {
                    Marshal.ZeroFreeGlobalAllocUnicode(pPassword);
                    pPassword = IntPtr.Zero;
                }
            }
        }
    }
}
