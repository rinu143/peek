using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Principal;
using Microsoft.Win32;

namespace PeekSetup.Services
{
    public static class SetupOperations
    {
        public const string CpGuid = "{4F64A0FC-9F09-4C0B-A5E7-537C44919903}";
        public const string CpRegistryKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\" + CpGuid;
        public const string ClsidRegistryKey = @"SOFTWARE\Classes\CLSID\" + CpGuid + @"\InprocServer32";
        public const string PeekRegistryKey = @"SOFTWARE\Peek";
        public const string PipeName = @"\\.\pipe\PeekEngine";

        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern bool WaitNamedPipe(string name, int timeout);

        public static string GetLogDirectory()
        {
            string programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            return Path.Combine(programData, "Peek", "logs");
        }

        public static string GetBackupRootDirectory()
        {
            string programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            return Path.Combine(programData, "Peek", "backup");
        }

        public static bool IsAuthPipeListening(int timeoutMs = 1000)
        {
            if (WaitNamedPipe(PipeName, timeoutMs))
            {
                return true;
            }

            int err = Marshal.GetLastWin32Error();
            // ERROR_ACCESS_DENIED (5) or ERROR_PIPE_BUSY (231) confirms pipe instance exists and is listening
            if (err == 5 || err == 231)
            {
                return true;
            }

            return false;
        }

        public static (bool Success, List<string> Messages) RunSelfTest()
        {
            var messages = new List<string>();
            bool allPassed = true;
            string logDir = GetLogDirectory();

            messages.Add("=== PEEK INSTALLATION HEALTH SELF-TEST ===");

            // 1. Check Credential Provider Registration
            bool cpRegOk = false;
            try
            {
                using var cpKey = Registry.LocalMachine.OpenSubKey(CpRegistryKey);
                using var clsidKey = Registry.LocalMachine.OpenSubKey(ClsidRegistryKey);
                if (cpKey != null && clsidKey != null)
                {
                    string? dllPath = clsidKey.GetValue("") as string;
                    if (!string.IsNullOrEmpty(dllPath) && File.Exists(Environment.ExpandEnvironmentVariables(dllPath)))
                    {
                        cpRegOk = true;
                    }
                }
            }
            catch (Exception ex)
            {
                messages.Add($"[FAIL] CP Registration Exception: {ex.Message}");
            }

            if (cpRegOk)
            {
                messages.Add("[PASS] Credential Provider Registration: Verified in HKLM LogonUI & COM");
            }
            else
            {
                messages.Add($"[FAIL] Credential Provider Registration: Missing or invalid DLL registration at {CpRegistryKey}");
                allPassed = false;
            }

            // 2. Check HKLM\SOFTWARE\Peek Keys
            bool hklmOk = false;
            string? installPathVal = null;
            try
            {
                using var peekKey = Registry.LocalMachine.OpenSubKey(PeekRegistryKey);
                if (peekKey != null)
                {
                    installPathVal = peekKey.GetValue("InstallPath") as string;
                    object? logLevel = peekKey.GetValue("LogLevel");
                    if (!string.IsNullOrEmpty(installPathVal) && logLevel != null)
                    {
                        hklmOk = true;
                    }
                }
            }
            catch (Exception ex)
            {
                messages.Add($"[FAIL] HKLM Registry Exception: {ex.Message}");
            }

            if (hklmOk)
            {
                messages.Add($"[PASS] Registry Configuration: HKLM\\SOFTWARE\\Peek verified (InstallPath: '{installPathVal}')");
            }
            else
            {
                messages.Add("[FAIL] Registry Configuration: Missing HKLM\\SOFTWARE\\Peek InstallPath or LogLevel");
                allPassed = false;
            }

            // 3 & 4. Check Engine Process Running & Auth Pipe Reachability (Poll for up to 15 seconds)
            Process[] engineProcesses = Array.Empty<Process>();
            bool pipeListening = false;
            var pollStart = DateTime.UtcNow;
            var pollTimeout = TimeSpan.FromSeconds(15);

            while (DateTime.UtcNow - pollStart < pollTimeout)
            {
                engineProcesses = Process.GetProcessesByName("PeekEngine");
                if (engineProcesses.Length > 0 && IsAuthPipeListening(500))
                {
                    pipeListening = true;
                    break;
                }
                Thread.Sleep(300);
            }

            if (engineProcesses.Length > 0)
            {
                messages.Add($"[PASS] Engine Service Process: Running (PID: {engineProcesses[0].Id})");
            }
            else
            {
                messages.Add("[FAIL] Engine Service Process: PeekEngine process is not running (timed out after 15s)");
                allPassed = false;
            }

            if (pipeListening || IsAuthPipeListening(1000))
            {
                messages.Add($"[PASS] Auth Named Pipe: {PipeName} is active and listening");
            }
            else
            {
                messages.Add($"[FAIL] Auth Named Pipe: {PipeName} could not be reached (engine offline or wrong DACL, timed out after 15s)");
                allPassed = false;
            }

            // 5. Check Engine Process Image Verification
            if (engineProcesses.Length > 0)
            {
                try
                {
                    string? procImagePath = engineProcesses[0].MainModule?.FileName;
                    if (!string.IsNullOrEmpty(procImagePath))
                    {
                        string expectedEngine = ResolveExpectedEnginePath(installPathVal);
                        string normActual = Path.GetFullPath(procImagePath);
                        string normExpected = Path.GetFullPath(expectedEngine);

                        if (string.Equals(normActual, normExpected, StringComparison.OrdinalIgnoreCase))
                        {
                            messages.Add($"[PASS] Engine Image Verification: Server binary matches '{normActual}'");
                        }
                        else
                        {
                            messages.Add($"[FAIL] Engine Image Verification: Mismatch!\n       Actual:   '{normActual}'\n       Expected: '{normExpected}'");
                            allPassed = false;
                        }
                    }
                }
                catch (Exception ex)
                {
                    messages.Add($"[WARN] Engine Image Query: {ex.Message} (elevated query may be required)");
                }
            }

            messages.Add($"[*] Diagnostics & Logs Directory: {logDir}");
            messages.Add(allPassed
                ? "=== SELF-TEST RESULT: ALL CHECKS PASSED ==="
                : "=== SELF-TEST RESULT: CHECKS FAILED ===");

            return (allPassed, messages);
        }

        public static string ResolveExpectedEnginePath(string? installPath)
        {
            if (string.IsNullOrEmpty(installPath))
            {
                string progFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
                return Path.Combine(progFiles, "Peek", "engine", "PeekEngine.exe");
            }

            string expanded = Environment.ExpandEnvironmentVariables(installPath);
            if (expanded.EndsWith("PeekEngine.exe", StringComparison.OrdinalIgnoreCase))
            {
                return expanded;
            }

            return Path.Combine(expanded, "engine", "PeekEngine.exe");
        }

        public static (bool Success, string Message) RunRollback()
        {
            try
            {
                string backupRoot = GetBackupRootDirectory();
                if (!Directory.Exists(backupRoot))
                {
                    return (false, $"Backup directory does not exist: {backupRoot}");
                }

                var backupDirs = Directory.GetDirectories(backupRoot)
                    .Select(d => new DirectoryInfo(d))
                    .OrderByDescending(d => d.LastWriteTimeUtc)
                    .ToList();

                if (backupDirs.Count == 0)
                {
                    return (false, $"No backup versions found under {backupRoot}");
                }

                var targetBackup = backupDirs[0];
                string backupVersion = targetBackup.Name;

                // Stop Peek processes
                foreach (var proc in Process.GetProcessesByName("PeekEngine"))
                {
                    try { proc.Kill(); proc.WaitForExit(3000); } catch { }
                }

                string installDir = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
                installDir = Path.Combine(installDir, "Peek");

                using (var peekKey = Registry.LocalMachine.OpenSubKey(PeekRegistryKey))
                {
                    if (peekKey?.GetValue("InstallPath") is string p && !string.IsNullOrEmpty(p))
                    {
                        string expanded = Environment.ExpandEnvironmentVariables(p);
                        if (!expanded.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                        {
                            installDir = expanded;
                        }
                    }
                }

                // Copy files back
                CopyDirectoryRecursive(targetBackup.FullName, installDir);

                // Re-register CP DLL
                string dllPath = Path.Combine(installDir, "PeekCredentialProvider.dll");
                if (File.Exists(dllPath))
                {
                    using var regProc = Process.Start(new ProcessStartInfo
                    {
                        FileName = "regsvr32.exe",
                        Arguments = $"/s \"{dllPath}\"",
                        UseShellExecute = false,
                        CreateNoWindow = true
                    });
                    regProc?.WaitForExit(5000);
                }

                // Restart PeekEngine
                string enginePath = Path.Combine(installDir, "engine", "PeekEngine.exe");
                if (File.Exists(enginePath))
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = enginePath,
                        WorkingDirectory = Path.GetDirectoryName(enginePath),
                        UseShellExecute = false,
                        CreateNoWindow = true
                    });
                }

                return (true, $"Rollback to version {backupVersion} completed successfully.");
            }
            catch (Exception ex)
            {
                return (false, $"Rollback failed: {ex.Message}");
            }
        }

        public static (bool Success, string Message) RunUninstallCleanup(bool removeFaceProfilesAndEntropy = true)
        {
            var cleanedItems = new List<string>();
            var secretStore = new LsaSecretStore();

            try
            {
                // 1. ALWAYS remove all L$PeekCred_<SID> secrets for local users
                try
                {
                    using var profileListKey = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList");
                    if (profileListKey != null)
                    {
                        foreach (string sid in profileListKey.GetSubKeyNames())
                        {
                            if (sid.StartsWith("S-1-5-21-", StringComparison.OrdinalIgnoreCase) ||
                                sid.StartsWith("S-1-5-", StringComparison.OrdinalIgnoreCase))
                            {
                                string secretName = "L$PeekCred_" + sid;
                                secretStore.DeleteSecret(secretName);
                                cleanedItems.Add($"LSA Secret: {secretName}");

                                if (removeFaceProfilesAndEntropy)
                                {
                                    // Clean user AppData\Local\Peek
                                    using var sub = profileListKey.OpenSubKey(sid);
                                    string? profilePath = sub?.GetValue("ProfileImagePath") as string;
                                    if (!string.IsNullOrEmpty(profilePath))
                                    {
                                        string expandedProfile = Environment.ExpandEnvironmentVariables(profilePath);
                                        string userPeekDir = Path.Combine(expandedProfile, "AppData", "Local", "Peek");
                                        if (Directory.Exists(userPeekDir))
                                        {
                                            try { Directory.Delete(userPeekDir, true); cleanedItems.Add($"User Data: {userPeekDir}"); } catch { }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                catch { }

                if (removeFaceProfilesAndEntropy)
                {
                    // Clean current user AppData\Local\Peek
                    string localPeek = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Peek");
                    if (Directory.Exists(localPeek))
                    {
                        try { Directory.Delete(localPeek, true); cleanedItems.Add($"Current User Data: {localPeek}"); } catch { }
                    }
                }

                // 2. ALWAYS Remove HKLM Identities and HKLM\SOFTWARE\Peek
                try
                {
                    using var hklmKey = Registry.LocalMachine.OpenSubKey(@"SOFTWARE", true);
                    if (hklmKey != null)
                    {
                        hklmKey.DeleteSubKeyTree("Peek", false);
                        cleanedItems.Add("Registry: HKLM\\SOFTWARE\\Peek");
                    }
                }
                catch { }

                // 3. Remove entropy file and ProgramData only if removeFaceProfilesAndEntropy is true
                string progDataPeek = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Peek");
                if (removeFaceProfilesAndEntropy)
                {
                    string entropyFile = Path.Combine(progDataPeek, "machine_entropy.bin");
                    if (File.Exists(entropyFile))
                    {
                        try { File.Delete(entropyFile); cleanedItems.Add($"Entropy: {entropyFile}"); } catch { }
                    }

                    if (Directory.Exists(progDataPeek))
                    {
                        try { Directory.Delete(progDataPeek, true); cleanedItems.Add($"ProgramData: {progDataPeek}"); } catch { }
                    }
                }
                else
                {
                    // If preserving face profiles and entropy, clean residual logs, backup, and updates
                    foreach (var sub in new[] { "logs", "backup", "updates" })
                    {
                        string subDir = Path.Combine(progDataPeek, sub);
                        if (Directory.Exists(subDir))
                        {
                            try { Directory.Delete(subDir, true); cleanedItems.Add($"ProgramData Subdir: {subDir}"); } catch { }
                        }
                    }
                }

                return (true, $"Uninstall cleanup finished. Cleaned: {string.Join(", ", cleanedItems)}");
            }
            catch (Exception ex)
            {
                return (false, $"Uninstall cleanup error: {ex.Message}");
            }
        }

        private static void CopyDirectoryRecursive(string sourceDir, string targetDir)
        {
            Directory.CreateDirectory(targetDir);
            foreach (var file in Directory.GetFiles(sourceDir))
            {
                string destFile = Path.Combine(targetDir, Path.GetFileName(file));
                File.Copy(file, destFile, true);
            }
            foreach (var subDir in Directory.GetDirectories(sourceDir))
            {
                string destSub = Path.Combine(targetDir, Path.GetFileName(subDir));
                CopyDirectoryRecursive(subDir, destSub);
            }
        }
    }
}
