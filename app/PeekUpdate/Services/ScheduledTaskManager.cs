using System;
using System.Diagnostics;
using System.IO;

namespace PeekUpdate.Services
{
    public static class ScheduledTaskManager
    {
        public const string TaskName = "PeekUpdate";

        public static bool RegisterUserUpdateTask(string? updateExePath = null)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(updateExePath))
                {
                    updateExePath = Process.GetCurrentProcess().MainModule?.FileName ?? "PeekUpdate.exe";
                }

                string workingDir = Path.GetDirectoryName(updateExePath) ?? AppContext.BaseDirectory;

                // User-level scheduled task: starts at logon and repeats every 12 hours with random delay up to 30 minutes
                string psScript = $@"
$ErrorActionPreference = 'Stop'
$taskName = '{TaskName}'
$exe = '{updateExePath}'
$wDir = '{workingDir}'

$action = New-ScheduledTaskAction -Execute $exe -Argument '--check' -WorkingDirectory $wDir
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -RandomDelay (New-TimeSpan -Minutes 30)
$triggerRep = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 12) -RepetitionDuration ([TimeSpan]::MaxValue) -RandomDelay (New-TimeSpan -Minutes 30)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$null = Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($triggerLogon, $triggerRep) -Settings $settings -Force
";

                var psi = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = $"-NoProfile -ExecutionPolicy Bypass -Command \"{psScript.Replace("\r", " ").Replace("\n", " ")}\"",
                    WindowStyle = ProcessWindowStyle.Hidden,
                    CreateNoWindow = true,
                    UseShellExecute = false
                };

                using var proc = Process.Start(psi);
                if (proc != null)
                {
                    proc.WaitForExit(10000);
                    return proc.ExitCode == 0;
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[!] Failed to register scheduled task: {ex.Message}");
            }

            return false;
        }

        public static bool RemoveUserUpdateTask()
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = "schtasks.exe",
                    Arguments = $"/Delete /TN \"{TaskName}\" /F",
                    WindowStyle = ProcessWindowStyle.Hidden,
                    CreateNoWindow = true,
                    UseShellExecute = false
                };

                using var proc = Process.Start(psi);
                if (proc != null)
                {
                    proc.WaitForExit(5000);
                    return proc.ExitCode == 0;
                }
            }
            catch { }

            return false;
        }
    }
}
