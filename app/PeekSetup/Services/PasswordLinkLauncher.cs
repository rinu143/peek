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
            string? exePath = Environment.ProcessPath;
            if (string.IsNullOrEmpty(exePath))
            {
                exePath = Process.GetCurrentProcess().MainModule?.FileName;
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = exePath,
                Arguments = $"--link-password --user \"{username}\" --sid \"{sid}\" --profile-id \"{profileId}\"",
                UseShellExecute = true,
                Verb = "runas"
            };

            try
            {
                using var proc = Process.Start(startInfo);
                if (proc == null) return 3; // other failure
                await proc.WaitForExitAsync();
                return proc.ExitCode;
            }
            catch (Win32Exception ex) when (ex.NativeErrorCode == 1223) // ERROR_CANCELLED (UAC declined)
            {
                return 4; // UAC declined
            }
            catch
            {
                return 3; // other failure
            }
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

            try
            {
                using var proc = Process.Start(startInfo);
                if (proc == null) return 3;
                await proc.WaitForExitAsync();
                return proc.ExitCode;
            }
            catch (Win32Exception ex) when (ex.NativeErrorCode == 1223)
            {
                return 4;
            }
            catch
            {
                return 3;
            }
        }
    }
}
