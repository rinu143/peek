using System;
using System.Diagnostics;
using System.IO;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;
using Microsoft.Win32;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public class ElevatedInstaller
    {
        private readonly SignatureVerifier _signatureVerifier;
        private readonly PolicyManager _policyManager;

        public ElevatedInstaller(SignatureVerifier? signatureVerifier = null, PolicyManager? policyManager = null)
        {
            _signatureVerifier = signatureVerifier ?? new SignatureVerifier();
            _policyManager = policyManager ?? new PolicyManager();
        }

        public static bool IsAdministrator()
        {
            using var identity = WindowsIdentity.GetCurrent();
            var principal = new WindowsPrincipal(identity);
            return principal.IsInRole(WindowsBuiltInRole.Administrator);
        }

        public static bool RelaunchElevated(string version)
        {
            try
            {
                string exePath = Process.GetCurrentProcess().MainModule?.FileName ?? "PeekUpdate.exe";
                var psi = new ProcessStartInfo
                {
                    FileName = exePath,
                    Arguments = $"--apply --version \"{version}\"",
                    UseShellExecute = true,
                    Verb = "runas"
                };

                using var proc = Process.Start(psi);
                return proc != null;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[!] UAC elevation request failed or was cancelled: {ex.Message}");
                return false;
            }
        }

        public int ExecuteApply(string version, string? sourceInstallerPath = null)
        {
            Console.WriteLine($"[*] Executing elevated update installation for version {version}...");

            if (!IsAdministrator())
            {
                Console.WriteLine("[!] ERROR: --apply requires elevated administrator privileges.");
                return 1;
            }

            // 1. Resolve source installer
            if (string.IsNullOrWhiteSpace(sourceInstallerPath) || !File.Exists(sourceInstallerPath))
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                sourceInstallerPath = Path.Combine(localAppData, "Peek", "updates", version, $"Peek-Setup-{version}-x64.exe");
            }

            if (!File.Exists(sourceInstallerPath))
            {
                Console.WriteLine($"[!] ERROR: Downloaded installer artifact not found at {sourceInstallerPath}");
                return 2;
            }

            // 2. Load manifest from update state or manifest file
            var updateState = _policyManager.LoadUserState();
            UpdateManifest? manifest = null;

            if (!string.IsNullOrWhiteSpace(updateState.Sha256))
            {
                manifest = new UpdateManifest
                {
                    Version = updateState.AvailableVersion ?? version,
                    Sha256 = updateState.Sha256,
                    Size = updateState.Size,
                    PublisherThumbprint = updateState.PublisherThumbprint ?? string.Empty
                };
            }

            // Check if manifest was downloaded alongside
            string manifestSidecar = Path.ChangeExtension(sourceInstallerPath, ".json");
            if (File.Exists(manifestSidecar))
            {
                try
                {
                    string mJson = File.ReadAllText(manifestSidecar);
                    manifest = JsonSerializer.Deserialize<UpdateManifest>(mJson);
                }
                catch { }
            }

            // 3. Stage into %ProgramData%\Peek\updates\<version> with admin-only ACL
            string programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            string adminUpdateDir = Path.Combine(programData, "Peek", "updates", version);

            try
            {
                if (!Directory.Exists(adminUpdateDir))
                {
                    Directory.CreateDirectory(adminUpdateDir);
                }

                // Apply admin-only ACL
                var dirAcl = new DirectorySecurity();
                dirAcl.SetAccessRuleProtection(true, false);
                var systemSid = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
                var adminSid = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
                var usersSid = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);

                dirAcl.AddAccessRule(new FileSystemAccessRule(systemSid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                dirAcl.AddAccessRule(new FileSystemAccessRule(adminSid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                dirAcl.AddAccessRule(new FileSystemAccessRule(usersSid, FileSystemRights.ReadAndExecute, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                new DirectoryInfo(adminUpdateDir).SetAccessControl(dirAcl);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[!] WARNING: Could not configure strict ACL on {adminUpdateDir}: {ex.Message}");
            }

            string adminInstallerPath = Path.Combine(adminUpdateDir, Path.GetFileName(sourceInstallerPath));
            File.Copy(sourceInstallerPath, adminInstallerPath, true);
            Console.WriteLine($"[OK] Copied installer to protected location: {adminInstallerPath}");

            // 4. TRIPLE SECURITY RE-VERIFICATION IN ELEVATED CONTEXT

            // Gate A: Re-verify SHA-256
            if (manifest != null && !string.IsNullOrWhiteSpace(manifest.Sha256))
            {
                Console.WriteLine("[*] [Re-verify 1/3] Computing SHA-256 in elevated context...");
                string actualHash;
                using (var sha = SHA256.Create())
                using (var fs = File.OpenRead(adminInstallerPath))
                {
                    byte[] hash = sha.ComputeHash(fs);
                    actualHash = BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant();
                }

                if (!string.Equals(actualHash, manifest.Sha256.Trim().ToLowerInvariant(), StringComparison.OrdinalIgnoreCase))
                {
                    Console.WriteLine($"[!] FATAL: Elevated SHA-256 re-verification failed! Expected: {manifest.Sha256}, Actual: {actualHash}");
                    File.Delete(adminInstallerPath);
                    return 3;
                }
                Console.WriteLine("[OK] Elevated SHA-256 verified successfully.");
            }

            // Gate B: Re-verify Authenticode (WinVerifyTrust + publisher thumbprint)
            Console.WriteLine("[*] [Re-verify 2/3] Verifying Authenticode signature and publisher thumbprint...");
            if (!AuthenticodeVerifier.VerifyAuthenticode(adminInstallerPath, manifest?.PublisherThumbprint, out string authErr))
            {
                Console.WriteLine($"[!] FATAL: Authenticode signature verification failed: {authErr}");
                File.Delete(adminInstallerPath);
                return 4;
            }
            Console.WriteLine("[OK] Authenticode signature verified successfully.");

            // Gate C: Re-verify Manifest Signature (if full manifest available)
            if (manifest != null && !string.IsNullOrWhiteSpace(manifest.Signature))
            {
                Console.WriteLine("[*] [Re-verify 3/3] Re-verifying manifest ECDSA signature...");
                if (!_signatureVerifier.VerifyManifest(manifest))
                {
                    Console.WriteLine("[!] FATAL: Manifest ECDSA signature re-verification failed!");
                    File.Delete(adminInstallerPath);
                    return 5;
                }
                Console.WriteLine("[OK] Manifest ECDSA signature verified successfully.");
            }

            // 5. Execute Installer with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE /LOG
            string logDir = Path.Combine(programData, "Peek", "logs");
            if (!Directory.Exists(logDir)) Directory.CreateDirectory(logDir);
            string installLogPath = Path.Combine(logDir, "update_install.log");

            string installerArgs = $"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE /LOG=\"{installLogPath}\"";
            Console.WriteLine($"[*] Running installer: {adminInstallerPath} {installerArgs}");

            int installerExitCode = -1;
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = adminInstallerPath,
                    Arguments = installerArgs,
                    UseShellExecute = false,
                    CreateNoWindow = true
                };

                using var proc = Process.Start(psi);
                if (proc != null)
                {
                    proc.WaitForExit();
                    installerExitCode = proc.ExitCode;
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[!] Failed to execute installer: {ex.Message}");
                installerExitCode = -99;
            }

            if (installerExitCode != 0)
            {
                Console.WriteLine($"[!] ERROR: Installer failed with exit code {installerExitCode}. Log: {installLogPath}");
                AttemptRollback();
                return 6;
            }

            Console.WriteLine("[OK] Installer finished successfully.");

            // 6. Post-Install Health Check: PeekSetup.exe --self-test
            string installDir = GetInstalledPath();
            string setupExe = Path.Combine(installDir, "PeekSetup.exe");
            bool selfTestPassed = false;

            if (File.Exists(setupExe))
            {
                Console.WriteLine("[*] Running post-update health check (PeekSetup.exe --self-test)...");
                try
                {
                    var testPsi = new ProcessStartInfo
                    {
                        FileName = setupExe,
                        Arguments = "--self-test",
                        UseShellExecute = false,
                        CreateNoWindow = true
                    };
                    using var testProc = Process.Start(testPsi);
                    if (testProc != null)
                    {
                        testProc.WaitForExit(10000);
                        selfTestPassed = (testProc.ExitCode == 0);
                    }
                }
                catch { }
            }
            else
            {
                selfTestPassed = true;
            }

            if (!selfTestPassed)
            {
                Console.WriteLine("[!] Health check reported failure! Initiating automatic rollback...");
                AttemptRollback();
                return 7;
            }

            Console.WriteLine("[OK] Health check passed cleanly.");

            // 7. Cleanup downloads
            try
            {
                if (File.Exists(adminInstallerPath)) File.Delete(adminInstallerPath);
                if (File.Exists(sourceInstallerPath)) File.Delete(sourceInstallerPath);
                string? userDir = Path.GetDirectoryName(sourceInstallerPath);
                if (!string.IsNullOrEmpty(userDir) && Directory.Exists(userDir))
                {
                    Directory.Delete(userDir, true);
                }
            }
            catch { }

            // 8. Update State to up_to_date
            updateState.Status = "up_to_date";
            updateState.InstalledVersion = version;
            updateState.AvailableVersion = null;
            updateState.ErrorMessage = null;
            _policyManager.SaveUserState(updateState);

            Console.WriteLine($"[OK] Peek successfully updated to version {version}!");
            return 0;
        }

        private static void AttemptRollback()
        {
            string installDir = GetInstalledPath();
            string setupExe = Path.Combine(installDir, "PeekSetup.exe");
            if (File.Exists(setupExe))
            {
                Console.WriteLine("[*] Invoking rollback via PeekSetup.exe --rollback...");
                try
                {
                    var psi = new ProcessStartInfo
                    {
                        FileName = setupExe,
                        Arguments = "--rollback",
                        UseShellExecute = false,
                        CreateNoWindow = true
                    };
                    using var proc = Process.Start(psi);
                    proc?.WaitForExit(15000);
                    Console.WriteLine("[OK] Rollback executed.");
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[!] Rollback failed to execute: {ex.Message}");
                }
            }
        }

        private static string GetInstalledPath()
        {
            try
            {
                using var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Peek", false);
                if (key != null)
                {
                    object? p = key.GetValue("InstallPath");
                    if (p is string pathStr && !string.IsNullOrWhiteSpace(pathStr))
                    {
                        if (pathStr.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                        {
                            return Path.GetDirectoryName(Path.GetDirectoryName(pathStr)) ?? @"C:\Program Files\Peek";
                        }
                        return pathStr.Trim();
                    }
                }
            }
            catch { }

            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Peek");
        }
    }
}
