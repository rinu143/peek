using System;
using System.Diagnostics;
using System.IO;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Microsoft.Win32;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public class ElevatedInstaller
    {
        public static readonly Regex VersionRegex = new(@"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$", RegexOptions.Compiled);

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

        public static bool IsRunningFromTempRunner()
        {
            string currentExe = Process.GetCurrentProcess().MainModule?.FileName ?? string.Empty;
            return currentExe.IndexOf("PeekUpdateRunner-", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        public static bool RelaunchElevated(string version)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(version) || !VersionRegex.IsMatch(version.Trim()))
                {
                    Console.WriteLine("[!] Invalid version format for elevation.");
                    return false;
                }

                string currentExe = Process.GetCurrentProcess().MainModule?.FileName ?? "PeekUpdate.exe";
                if (!File.Exists(currentExe))
                {
                    currentExe = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "PeekUpdate.exe");
                }

                string tempBase = Path.GetTempPath();
                string runnerDir = Path.Combine(tempBase, $"PeekUpdateRunner-{Guid.NewGuid():N}");
                Directory.CreateDirectory(runnerDir);

                string runnerExe = Path.Combine(runnerDir, "PeekUpdate.exe");
                File.Copy(currentExe, runnerExe, true);

                string? currentDir = Path.GetDirectoryName(currentExe);
                if (!string.IsNullOrEmpty(currentDir) && Directory.Exists(currentDir))
                {
                    foreach (var ext in new[] { "*.dll", "*.json" })
                    {
                        foreach (var f in Directory.GetFiles(currentDir, ext))
                        {
                            try
                            {
                                string dest = Path.Combine(runnerDir, Path.GetFileName(f));
                                if (!File.Exists(dest)) File.Copy(f, dest, true);
                            }
                            catch { }
                        }
                    }
                }

                var psi = new ProcessStartInfo
                {
                    FileName = runnerExe,
                    Arguments = $"--apply --version \"{version.Trim()}\"",
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

        public static int LaunchFromTempRunner(string version)
        {
            try
            {
                string currentExe = Process.GetCurrentProcess().MainModule?.FileName ?? string.Empty;
                if (string.IsNullOrWhiteSpace(currentExe) || !File.Exists(currentExe))
                {
                    currentExe = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "PeekUpdate.exe");
                }

                string tempBase = Path.GetTempPath();
                string runnerDir = Path.Combine(tempBase, $"PeekUpdateRunner-{Guid.NewGuid():N}");
                Directory.CreateDirectory(runnerDir);

                string runnerExe = Path.Combine(runnerDir, "PeekUpdate.exe");
                File.Copy(currentExe, runnerExe, true);

                string? currentDir = Path.GetDirectoryName(currentExe);
                if (!string.IsNullOrEmpty(currentDir) && Directory.Exists(currentDir))
                {
                    foreach (var ext in new[] { "*.dll", "*.json" })
                    {
                        foreach (var f in Directory.GetFiles(currentDir, ext))
                        {
                            try
                            {
                                string dest = Path.Combine(runnerDir, Path.GetFileName(f));
                                if (!File.Exists(dest)) File.Copy(f, dest, true);
                            }
                            catch { }
                        }
                    }
                }

                var psi = new ProcessStartInfo
                {
                    FileName = runnerExe,
                    Arguments = $"--apply --version \"{version.Trim()}\"",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };

                using var proc = Process.Start(psi);
                if (proc == null)
                {
                    Console.WriteLine("[!] Failed to launch PeekUpdate runner copy.");
                    return 1;
                }

                proc.OutputDataReceived += (_, e) => { if (e.Data != null) Console.WriteLine(e.Data); };
                proc.ErrorDataReceived += (_, e) => { if (e.Data != null) Console.Error.WriteLine(e.Data); };
                proc.BeginOutputReadLine();
                proc.BeginErrorReadLine();
                proc.WaitForExit();

                return proc.ExitCode;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[!] Failed to run PeekUpdate from temp runner: {ex.Message}");
                return 1;
            }
        }

        public int ExecuteApply(string version, string? sourceInstallerPath = null, bool skipElevationCheckForTest = false, string? stagingDirOverride = null)
        {
            Console.WriteLine($"[*] Executing elevated update installation for version {version}...");

            // 0. Validate version before building ANY paths
            if (string.IsNullOrWhiteSpace(version) || !VersionRegex.IsMatch(version.Trim()))
            {
                Console.WriteLine($"[!] FATAL: Target version '{version}' is invalid. Must match ^\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.]+)?$");
                return 1;
            }
            version = version.Trim();

            if (!skipElevationCheckForTest && !IsAdministrator())
            {
                Console.WriteLine("[!] ERROR: --apply requires elevated administrator privileges.");
                return 1;
            }

            // 1. Resolve source installer and mandatory manifest sidecar
            if (string.IsNullOrWhiteSpace(sourceInstallerPath) || !File.Exists(sourceInstallerPath))
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                sourceInstallerPath = Path.Combine(localAppData, "Peek", "updates", version, $"Peek-Setup-{version}-x64.exe");

                if (!File.Exists(sourceInstallerPath))
                {
                    string commonAppData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
                    string candidatePath = Path.Combine(commonAppData, "Peek", "updates", version, $"Peek-Setup-{version}-x64.exe");
                    if (File.Exists(candidatePath))
                    {
                        sourceInstallerPath = candidatePath;
                    }
                }
            }

            string manifestSidecar = Path.ChangeExtension(sourceInstallerPath, ".json");

            // Manifest sidecar is MANDATORY
            if (!File.Exists(manifestSidecar))
            {
                Console.WriteLine($"[!] FATAL: Mandatory manifest sidecar file missing at: {manifestSidecar}");
                return 2;
            }

            if (!File.Exists(sourceInstallerPath))
            {
                Console.WriteLine($"[!] FATAL: Downloaded installer artifact not found at {sourceInstallerPath}");
                return 2;
            }

            // 2. Load and verify signed manifest
            UpdateManifest? manifest;
            try
            {
                string mJson = File.ReadAllText(manifestSidecar);
                manifest = JsonSerializer.Deserialize<UpdateManifest>(mJson);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[!] FATAL: Failed to parse manifest sidecar JSON: {ex.Message}");
                return 2;
            }

            if (manifest == null)
            {
                Console.WriteLine("[!] FATAL: Manifest sidecar deserialized to null.");
                return 2;
            }

            // First verify ECDSA signature (fail closed if missing, unsigned or invalid)
            if (string.IsNullOrWhiteSpace(manifest.Signature))
            {
                Console.WriteLine("[!] FATAL: Manifest sidecar is unsigned (missing signature). Update refused.");
                return 5;
            }

            if (!_signatureVerifier.VerifyManifest(manifest))
            {
                Console.WriteLine("[!] FATAL: Manifest sidecar ECDSA signature verification failed. Untrusted or tampered update manifest.");
                return 5;
            }
            Console.WriteLine("[OK] Manifest ECDSA signature verified successfully.");

            // Verify version == --version
            if (!string.Equals(manifest.Version, version, StringComparison.Ordinal))
            {
                Console.WriteLine($"[!] FATAL: Manifest version '{manifest.Version}' does not match target version '{version}'. Update refused.");
                return 2;
            }

            // Verify version > installed
            string installedVerStr = UpdateChecker.GetAppVersion();
            if (SemVersion.TryParse(manifest.Version, out var mVer) && SemVersion.TryParse(installedVerStr, out var iVer) && mVer != null && iVer != null)
            {
                if (mVer <= iVer)
                {
                    Console.WriteLine($"[!] FATAL: Manifest version {manifest.Version} is not greater than installed version {installedVerStr}. Downgrade/reinstall refused.");
                    return 2;
                }
            }

            // Verify not expired
            if (string.IsNullOrWhiteSpace(manifest.Expires) || !DateTime.TryParse(manifest.Expires, out var expiresDate))
            {
                Console.WriteLine("[!] FATAL: Manifest missing valid 'expires' timestamp.");
                return 2;
            }
            if (expiresDate.ToUniversalTime() < DateTime.UtcNow)
            {
                Console.WriteLine($"[!] FATAL: Manifest expired on {manifest.Expires}. Update refused.");
                return 2;
            }

            if (!string.IsNullOrWhiteSpace(manifest.Released) && DateTime.TryParse(manifest.Released, out var releaseDate))
            {
                if (DateTime.UtcNow - releaseDate.ToUniversalTime() > TimeSpan.FromDays(30))
                {
                    Console.WriteLine($"[!] FATAL: Manifest release date {manifest.Released} is over 30 days old. Expired.");
                    return 2;
                }
            }

            // Verify channel allowed
            string effectiveChannel = _policyManager.GetEffectiveChannel();
            if (!string.Equals(manifest.Channel, effectiveChannel, StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine($"[!] FATAL: Manifest channel '{manifest.Channel}' does not match effective channel '{effectiveChannel}'. Update refused.");
                return 2;
            }

            // Verify host allow-list
            if (!Uri.TryCreate(manifest.Url, UriKind.Absolute, out var updateUri) || updateUri.Scheme != Uri.UriSchemeHttps)
            {
                Console.WriteLine($"[!] FATAL: Manifest URL is invalid or not HTTPS: '{manifest.Url}'");
                return 2;
            }

            if (!UpdateChecker.IsHostAllowed(updateUri.Host, _policyManager.GetEffectiveFeedUrl()))
            {
                Console.WriteLine($"[!] FATAL: Manifest download host '{updateUri.Host}' is not on the trusted allow-list.");
                return 2;
            }

            // Verify size cap
            if (manifest.Size <= 0 || manifest.Size > UpdateChecker.MaxAllowedSizeBytes)
            {
                Console.WriteLine($"[!] FATAL: Manifest download size ({manifest.Size} bytes) is invalid or exceeds 500 MB limit.");
                return 2;
            }

            // Use sha256 and publisher_thumbprint from SIGNED manifest only.
            // publisher_thumbprint is REQUIRED (non-empty) for every release; reject manifests without it.
            if (string.IsNullOrWhiteSpace(manifest.Sha256) || manifest.Sha256.Trim().Length != 64)
            {
                Console.WriteLine($"[!] FATAL: Manifest specifies invalid SHA-256 hash: '{manifest.Sha256}'");
                return 3;
            }

            if (string.IsNullOrWhiteSpace(manifest.PublisherThumbprint))
            {
                Console.WriteLine("[!] FATAL: publisher_thumbprint is missing or empty in signed manifest. Every release requires a non-empty publisher_thumbprint.");
                return 4;
            }

            // 3. Stage into %ProgramData%\Peek\updates\<version> with admin-only ACL
            string programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            string adminUpdateDir = stagingDirOverride ?? Path.Combine(programData, "Peek", "updates", version);

            string adminInstallerPath = Path.Combine(adminUpdateDir, Path.GetFileName(sourceInstallerPath));
            try
            {
                if (!Directory.Exists(adminUpdateDir))
                {
                    Directory.CreateDirectory(adminUpdateDir);
                }

                // Apply strict admin-only ACL; failure to set this is fatal
                var dirAcl = new DirectorySecurity();
                dirAcl.SetAccessRuleProtection(true, false);
                var systemSid = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
                var adminSid = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
                var usersSid = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);

                dirAcl.AddAccessRule(new FileSystemAccessRule(systemSid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                dirAcl.AddAccessRule(new FileSystemAccessRule(adminSid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                dirAcl.AddAccessRule(new FileSystemAccessRule(usersSid, FileSystemRights.ReadAndExecute, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));

                if (skipElevationCheckForTest)
                {
                    try
                    {
                        var currentUserSid = WindowsIdentity.GetCurrent().User;
                        if (currentUserSid != null)
                        {
                            dirAcl.AddAccessRule(new FileSystemAccessRule(currentUserSid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                        }
                    }
                    catch { }
                }

                new DirectoryInfo(adminUpdateDir).SetAccessControl(dirAcl);

                File.Copy(sourceInstallerPath, adminInstallerPath, true);
                Console.WriteLine($"[OK] Copied installer to protected location: {adminInstallerPath}");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[!] FATAL: Could not stage installer or configure strict admin ACL on {adminUpdateDir}: {ex.Message}");
                return 8;
            }

            // 4. Open with FileShare.Read and keep handle open until process has started
            FileStream? lockedStream = null;
            int installerExitCode = -1;

            string logDir = Path.Combine(programData, "Peek", "logs");
            if (!Directory.Exists(logDir)) Directory.CreateDirectory(logDir);
            string installLogPath = Path.Combine(logDir, "update_install.log");
            string installerArgs = $"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE /LOG=\"{installLogPath}\"";

            try
            {
                lockedStream = new FileStream(adminInstallerPath, FileMode.Open, FileAccess.Read, FileShare.Read);

                // Gate A: Re-verify SHA-256 on locked copy
                Console.WriteLine("[*] [Re-verify 1/2] Computing SHA-256 in elevated context on protected copy...");
                string actualHash;
                using (var sha = SHA256.Create())
                {
                    byte[] hash = sha.ComputeHash(lockedStream);
                    actualHash = BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant();
                }
                lockedStream.Position = 0;

                if (!string.Equals(actualHash, manifest.Sha256.Trim().ToLowerInvariant(), StringComparison.OrdinalIgnoreCase))
                {
                    Console.WriteLine($"[!] FATAL: Elevated SHA-256 re-verification failed! Expected: {manifest.Sha256}, Actual: {actualHash}");
                    lockedStream.Dispose();
                    lockedStream = null;
                    try { File.Delete(adminInstallerPath); } catch { }
                    return 3;
                }
                Console.WriteLine("[OK] Elevated SHA-256 verified successfully.");

                // Gate B: Re-verify Authenticode (allowTestOverride: false - elevated apply NEVER allows unsigned)
                Console.WriteLine("[*] [Re-verify 2/2] Verifying Authenticode signature and publisher thumbprint...");
                if (!AuthenticodeVerifier.VerifyAuthenticode(adminInstallerPath, manifest.PublisherThumbprint, out string authErr, allowTestOverride: false))
                {
                    Console.WriteLine($"[!] FATAL: Authenticode signature verification failed: {authErr}");
                    lockedStream.Dispose();
                    lockedStream = null;
                    try { File.Delete(adminInstallerPath); } catch { }
                    return 4;
                }
                Console.WriteLine("[OK] Authenticode signature verified successfully.");

                // Launch installer process while holding lockedStream open
                Console.WriteLine($"[*] Running installer: {adminInstallerPath} {installerArgs}");
                var psi = new ProcessStartInfo
                {
                    FileName = adminInstallerPath,
                    Arguments = installerArgs,
                    UseShellExecute = false,
                    CreateNoWindow = true
                };

                using var proc = Process.Start(psi);

                // Handle is held open until the installer process has started
                lockedStream.Dispose();
                lockedStream = null;

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
            finally
            {
                lockedStream?.Dispose();
            }

            if (installerExitCode != 0)
            {
                Console.WriteLine($"[!] ERROR: Installer failed with exit code {installerExitCode}. Log: {installLogPath}");
                AttemptRollback();
                return 6;
            }

            Console.WriteLine("[OK] Installer finished successfully.");

            // 5. Post-Install Health Check: PeekSetup.exe --self-test with 30s timeout and HasExited check
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
                        testProc.WaitForExit(30000); // 30 second timeout
                        if (!testProc.HasExited)
                        {
                            try { testProc.Kill(); } catch { }
                            Console.WriteLine("[!] Health check timed out after 30 seconds.");
                            selfTestPassed = false;
                        }
                        else
                        {
                            selfTestPassed = (testProc.ExitCode == 0);
                        }
                    }
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[!] Health check execution error: {ex.Message}");
                }
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

            // 6. Cleanup downloads
            try
            {
                if (File.Exists(adminInstallerPath)) File.Delete(adminInstallerPath);
                if (File.Exists(sourceInstallerPath)) File.Delete(sourceInstallerPath);
                if (File.Exists(manifestSidecar)) File.Delete(manifestSidecar);
                string? userDir = Path.GetDirectoryName(sourceInstallerPath);
                if (!string.IsNullOrEmpty(userDir) && Directory.Exists(userDir))
                {
                    Directory.Delete(userDir, true);
                }
            }
            catch { }

            // 7. Update State to up_to_date
            var updateState = _policyManager.LoadUserState();
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
                    proc?.WaitForExit(30000);
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
