using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using PeekUpdate.Models;
using PeekUpdate.Services;

namespace PeekUpdate
{
    public static class Program
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AttachConsole(int dwProcessId);

        private const int ATTACH_PARENT_PROCESS = -1;

        public static async Task<int> Main(string[] args)
        {
            if (AttachConsole(ATTACH_PARENT_PROCESS))
            {
                var stdout = Console.OpenStandardOutput();
                if (stdout != Stream.Null)
                {
                    var writer = new StreamWriter(stdout, Console.OutputEncoding) { AutoFlush = true };
                    Console.SetOut(writer);
                    Console.SetError(writer);
                }
            }

            var policyManager = new PolicyManager();
            var signatureVerifier = new SignatureVerifier();
            var updateChecker = new UpdateChecker(signatureVerifier, policyManager);

            if (args.Length == 0)
            {
                PrintUsage();
                return 0;
            }

            string mode = args[0].ToLowerInvariant();

            // --version
            if (mode == "--version" || mode == "-v")
            {
                Console.WriteLine($"PeekUpdate {UpdateChecker.GetAppVersion()}");
                return 0;
            }

            // --status
            if (mode == "--status")
            {
                var state = policyManager.LoadUserState();
                var settings = policyManager.LoadUserSettings();
                var statusInfo = new
                {
                    installed_version = UpdateChecker.GetAppVersion(),
                    channel = policyManager.GetEffectiveChannel(),
                    auto_check_enabled = policyManager.GetEffectiveAutoCheck(),
                    hklm_auto_check = policyManager.GetHklmAutoCheck(),
                    feed_url = policyManager.GetEffectiveFeedUrl(),
                    is_snoozed = policyManager.IsSnoozed(),
                    snooze_until = settings.SnoozeUntil,
                    skipped_version = settings.SkippedVersion,
                    last_checked = state.LastChecked,
                    update_status = state.Status,
                    available_version = state.AvailableVersion,
                    critical = state.Critical,
                    notes_url = state.NotesUrl,
                    download_url = state.DownloadUrl
                };

                Console.WriteLine(JsonSerializer.Serialize(statusInfo, new JsonSerializerOptions { WriteIndented = true }));
                return 0;
            }

            // --check [--now]
            if (mode == "--check")
            {
                bool isNow = false;
                for (int i = 1; i < args.Length; i++)
                {
                    if (args[i].Equals("--now", StringComparison.OrdinalIgnoreCase))
                    {
                        isNow = true;
                    }
                }

                Console.WriteLine($"[*] Checking for updates (Channel: {policyManager.GetEffectiveChannel()}, Interactive: {isNow})...");
                var result = await updateChecker.CheckForUpdatesAsync(isInteractive: isNow);

                var state = policyManager.LoadUserState();
                state.LastChecked = DateTime.UtcNow.ToString("o");
                state.InstalledVersion = result.InstalledVersion;
                state.Channel = result.Channel;

                if (!result.Success)
                {
                    state.Status = "error";
                    state.ErrorMessage = result.ErrorMessage;
                    policyManager.SaveUserState(state);

                    if (result.IsOffline)
                    {
                        // Silent when offline
                        Console.WriteLine($"[-] System is offline or update server unreachable: {result.ErrorMessage}");
                        return 0;
                    }

                    Console.WriteLine($"[!] Update check failed: {result.ErrorMessage}");
                    return 1;
                }

                if (result.NotModified)
                {
                    state.Status = "up_to_date";
                    state.ErrorMessage = null;
                    policyManager.SaveUserState(state);
                    Console.WriteLine("[OK] Peek is up to date (feed not modified).");
                    return 0;
                }

                if (result.UpdateAvailable && result.Manifest != null)
                {
                    var m = result.Manifest;
                    state.Status = "update_available";
                    state.AvailableVersion = m.Version;
                    state.Critical = m.Critical;
                    state.ReleaseDate = m.Released;
                    state.NotesUrl = m.NotesUrl;
                    state.DownloadUrl = m.Url;
                    state.Sha256 = m.Sha256;
                    state.Size = m.Size;
                    state.PublisherThumbprint = m.PublisherThumbprint;
                    state.ErrorMessage = null;
                    policyManager.SaveUserState(state);

                    try
                    {
                        string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                        string updateDir = Path.Combine(localAppData, "Peek", "updates", m.Version);
                        if (!Directory.Exists(updateDir)) Directory.CreateDirectory(updateDir);
                        string sidecarPath = Path.Combine(updateDir, $"Peek-Setup-{m.Version}-x64.json");
                        File.WriteAllText(sidecarPath, JsonSerializer.Serialize(m, new JsonSerializerOptions { WriteIndented = true }));
                    }
                    catch { }

                    Console.WriteLine($"[+] New version available: Peek {m.Version} (Critical: {m.Critical})");
                    Console.WriteLine($"    Notes: {m.NotesUrl}");
                    Console.WriteLine($"    Download: {m.Url}");

                    // Fire notification toast / balloon
                    NotificationService.ShowUpdateNotification(m);
                    return 0;
                }

                state.Status = "up_to_date";
                state.AvailableVersion = null;
                state.ErrorMessage = null;
                policyManager.SaveUserState(state);
                Console.WriteLine($"[OK] Peek is up to date (Version {result.InstalledVersion}).");
                return 0;
            }

            // --apply [--version <ver>]
            if (mode == "--apply")
            {
                string? version = null;

                for (int i = 1; i < args.Length; i++)
                {
                    if (args[i].Equals("--version", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                    {
                        version = args[++i];
                    }
                }

                if (string.IsNullOrWhiteSpace(version))
                {
                    var state = policyManager.LoadUserState();
                    version = state.AvailableVersion;
                }

                if (string.IsNullOrWhiteSpace(version) || !ElevatedInstaller.VersionRegex.IsMatch(version.Trim()))
                {
                    Console.WriteLine("[!] ERROR: Target version is missing or invalid for --apply. Must match format ^\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.]+)?$");
                    return 1;
                }
                version = version.Trim();

                // If not elevated, relaunch elevated
                if (!ElevatedInstaller.IsAdministrator())
                {
                    Console.WriteLine("[*] Requesting UAC administrator elevation for update installation...");
                    bool launched = ElevatedInstaller.RelaunchElevated(version);
                    return launched ? 0 : 1;
                }

                // If elevated but not running from temp runner, launch from runner copy to survive upgrade
                if (!ElevatedInstaller.IsRunningFromTempRunner())
                {
                    return ElevatedInstaller.LaunchFromTempRunner(version);
                }

                var elevatedInstaller = new ElevatedInstaller(signatureVerifier, policyManager);
                return elevatedInstaller.ExecuteApply(version);
            }

            // --register-task
            if (mode == "--register-task")
            {
                bool registered = ScheduledTaskManager.RegisterUserUpdateTask();
                Console.WriteLine(registered
                    ? "[OK] Registered user update scheduled task 'PeekUpdate'."
                    : "[!] Failed to register scheduled task.");
                return registered ? 0 : 1;
            }

            // --download
            if (mode == "--download")
            {
                var state = policyManager.LoadUserState();
                if (string.IsNullOrWhiteSpace(state.AvailableVersion) || string.IsNullOrWhiteSpace(state.DownloadUrl))
                {
                    Console.WriteLine("[!] No update currently available to download. Run --check --now first.");
                    return 1;
                }

                var manifest = new UpdateManifest
                {
                    Version = state.AvailableVersion,
                    Url = state.DownloadUrl,
                    Sha256 = state.Sha256 ?? string.Empty,
                    Size = state.Size,
                    PublisherThumbprint = state.PublisherThumbprint ?? string.Empty,
                    Critical = state.Critical
                };

                Console.WriteLine($"[*] Downloading Peek {manifest.Version} from {manifest.Url}...");
                var downloader = new UpdateDownloader();
                try
                {
                    string localPath = await downloader.DownloadAndVerifyAsync(manifest, (read, total) =>
                    {
                        if (total > 0)
                        {
                            double pct = (double)read / total * 100.0;
                            Console.Write($"\rDownloading: {pct:F1}% ({read / 1024 / 1024} MB / {total / 1024 / 1024} MB)   ");
                        }
                    });

                    Console.WriteLine();
                    Console.WriteLine($"[OK] Downloaded and verified installer: {localPath}");
                    state.Status = "downloaded";
                    state.LocalInstallerPath = localPath;
                    policyManager.SaveUserState(state);
                    return 0;
                }
                catch (Exception ex)
                {
                    Console.WriteLine();
                    Console.WriteLine($"[!] Download failed: {ex.Message}");
                    state.Status = "error";
                    state.ErrorMessage = ex.Message;
                    policyManager.SaveUserState(state);
                    return 2;
                }
            }

            PrintUsage();
            return 1;
        }

        private static void PrintUsage()
        {
            Console.WriteLine("PeekUpdate - Automated Update Checking & Verification Service for Peek");
            Console.WriteLine("Usage: PeekUpdate.exe [command]");
            Console.WriteLine();
            Console.WriteLine("Commands:");
            Console.WriteLine("  --check              Run scheduled update check (respects policy, snooze, ETag)");
            Console.WriteLine("  --check --now        Run immediate interactive update check");
            Console.WriteLine("  --status             Display current update policy, feed, and check status");
            Console.WriteLine("  --download           Download available update to user cache");
            Console.WriteLine("  --apply              Elevated installer execution and verification (UAC prompt)");
            Console.WriteLine("  --register-task      Ensure user-level logon & 12-hour scheduled task is registered");
            Console.WriteLine("  --version            Display PeekUpdate version");
        }
    }
}
