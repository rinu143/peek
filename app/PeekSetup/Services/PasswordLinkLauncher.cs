using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Threading.Tasks;

namespace PeekSetup.Services
{
    public class PasswordLinkLauncher : IPasswordLinkLauncher
    {
        public async Task<int> LaunchLinkPasswordAsync(string username, string sid, string profileId)
        {
            string? exePath = Environment.ProcessPath ?? Process.GetCurrentProcess().MainModule?.FileName;
            var startInfo = new ProcessStartInfo
            {
                FileName = exePath,
                Arguments = $"--link-password --user \"{username}\" --sid \"{sid}\" --profile-id \"{profileId}\"",
                UseShellExecute = true,
                Verb = "runas"
            };

            return await RunElevatedProcessAsync(startInfo);
        }

        public async Task<int> LaunchAuthorizeIdentityAsync(string sid, string profileId, string? username = null)
        {
            string? exePath = Environment.ProcessPath ?? Process.GetCurrentProcess().MainModule?.FileName;
            string args = string.IsNullOrWhiteSpace(username)
                ? $"--authorize-identity --sid \"{sid}\" --profile-id \"{profileId}\""
                : $"--authorize-identity --sid \"{sid}\" --profile-id \"{profileId}\" --user \"{username}\"";

            var startInfo = new ProcessStartInfo
            {
                FileName = exePath,
                Arguments = args,
                UseShellExecute = true,
                Verb = "runas"
            };

            return await RunElevatedProcessAsync(startInfo);
        }

        public async Task<int> LaunchRevokeIdentityAsync(string sid, string profileId)
        {
            string? exePath = Environment.ProcessPath ?? Process.GetCurrentProcess().MainModule?.FileName;
            var startInfo = new ProcessStartInfo
            {
                FileName = exePath,
                Arguments = $"--revoke-identity --sid \"{sid}\" --profile-id \"{profileId}\"",
                UseShellExecute = true,
                Verb = "runas"
            };

            return await RunElevatedProcessAsync(startInfo);
        }

        public async Task<int> LaunchUnlinkAsync(string sid)
        {
            string? exePath = Environment.ProcessPath ?? Process.GetCurrentProcess().MainModule?.FileName;
            var startInfo = new ProcessStartInfo
            {
                FileName = exePath,
                Arguments = $"--unlink --sid \"{sid}\"",
                UseShellExecute = true,
                Verb = "runas"
            };

            return await RunElevatedProcessAsync(startInfo);
        }

        private static async Task<int> RunElevatedProcessAsync(ProcessStartInfo startInfo)
        {
            try
            {
                using var proc = Process.Start(startInfo);
                if (proc == null) return PasswordLinker.ExitError; // 3 other failure
                await proc.WaitForExitAsync();
                return proc.ExitCode;
            }
            catch (Win32Exception ex) when (ex.NativeErrorCode == 1223) // ERROR_CANCELLED (UAC declined)
            {
                return PasswordLinker.ExitUacDeclined; // 4 UAC declined
            }
            catch
            {
                return PasswordLinker.ExitError; // 3 other failure
            }
        }
    }
}
