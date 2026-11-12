using System;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Windows;
using PeekSetup.Services;

namespace PeekSetup
{
    /// <summary>
    /// Interaction logic for App.xaml
    /// </summary>
    public partial class App : Application
    {
        [DllImport("kernel32.dll")]
        private static extern bool AttachConsole(int dwProcessId);
        private const int ATTACH_PARENT_PROCESS = -1;

        public static string GetAppVersion()
        {
            return typeof(App).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
                ?? typeof(App).Assembly.GetName().Version?.ToString(3)
                ?? "0.2.0-beta.1";
        }

        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);

            string[] args = e.Args;

            // CLI commands should attach to caller's console if executed from terminal
            if (args.Length > 0)
            {
                AttachConsole(ATTACH_PARENT_PROCESS);
            }

            // Check for --version / -v
            if (HasArg(args, "--version") || HasArg(args, "-v"))
            {
                Console.WriteLine($"PeekSetup {GetAppVersion()}");
                Environment.Exit(0);
                return;
            }

            // Check for --self-test
            if (HasArg(args, "--self-test"))
            {
                var (success, messages) = SetupOperations.RunSelfTest();
                foreach (var msg in messages)
                {
                    Console.WriteLine(msg);
                }
                Environment.Exit(success ? 0 : 1);
                return;
            }

            // Check for --rollback
            if (HasArg(args, "--rollback"))
            {
                var (success, message) = SetupOperations.RunRollback();
                Console.WriteLine(message);
                Environment.Exit(success ? 0 : 1);
                return;
            }

            // Check for --uninstall-cleanup
            if (HasArg(args, "--uninstall-cleanup"))
            {
                var (success, message) = SetupOperations.RunUninstallCleanup();
                Console.WriteLine(message);
                Environment.Exit(success ? 0 : 1);
                return;
            }

            // Initialize dynamic theme based on user's Windows system preference (Light / Dark)
            ThemeManager.InitializeTheme();

            // Check for --unlink --sid <SID>
            if (HasArg(args, "--unlink"))
            {
                string? sid = GetArgValue(args, "--sid");
                if (string.IsNullOrWhiteSpace(sid))
                {
                    Environment.Exit(PasswordLinker.ExitError); // 3
                    return;
                }

                var linker = new PasswordLinker();
                linker.UnlinkPassword(sid);
                Environment.Exit(PasswordLinker.ExitOk); // 0
                return;
            }

            // Check for --revoke-identity --sid <SID> --profile-id <id>
            if (HasArg(args, "--revoke-identity"))
            {
                string? sid = GetArgValue(args, "--sid");
                string? profileId = GetArgValue(args, "--profile-id");

                if (string.IsNullOrWhiteSpace(sid) || string.IsNullOrWhiteSpace(profileId))
                {
                    Environment.Exit(PasswordLinker.ExitError); // 3
                    return;
                }

                var linker = new PasswordLinker();
                int exitCode = linker.RevokeIdentity(profileId, sid);
                Environment.Exit(exitCode);
                return;
            }

            // Check for --authorize-identity --sid <SID> --profile-id <id> [--user <user>]
            if (HasArg(args, "--authorize-identity"))
            {
                string? sid = GetArgValue(args, "--sid");
                string? profileId = GetArgValue(args, "--profile-id");
                string? user = GetArgValue(args, "--user");

                if (string.IsNullOrWhiteSpace(sid) || string.IsNullOrWhiteSpace(profileId))
                {
                    Environment.Exit(PasswordLinker.ExitError); // 3
                    return;
                }

                var authStore = new RegistryAuthorizationStore();
                var existing = authStore.GetAuthorizedIdentities(sid);

                // Require that a link already exists (registry list non-empty, otherwise exit 6)
                if (existing == null || existing.Length == 0)
                {
                    Environment.Exit(PasswordLinker.ExitNotLinkedYet); // 6
                    return;
                }

                // Identity limit check (cap 5)
                if (!existing.Contains(profileId, StringComparer.OrdinalIgnoreCase) && existing.Length >= RegistryAuthorizationStore.MaxAuthorizedIdentities)
                {
                    Environment.Exit(PasswordLinker.ExitIdentityLimit); // 5
                    return;
                }

                var resolver = new AccountSidResolver();
                if (string.IsNullOrWhiteSpace(user))
                {
                    try
                    {
                        var secId = new SecurityIdentifier(sid);
                        user = ((NTAccount)secId.Translate(typeof(NTAccount))).Value;
                    }
                    catch
                    {
                        user = Environment.UserName;
                    }
                }
                else
                {
                    if (!resolver.ValidateUserSid(user, sid))
                    {
                        Environment.Exit(PasswordLinker.ExitError); // 3
                        return;
                    }
                }

                var window = new LinkPasswordWindow(user, sid, profileId, null, LinkPasswordMode.AuthorizeIdentity);
                window.ShowDialog();
                Environment.Exit(window.ExitCode);
                return;
            }

            // Check for --link-password --user <DOMAIN\user> --sid <SID> --profile-id <id>
            if (HasArg(args, "--link-password"))
            {
                string? user = GetArgValue(args, "--user");
                string? sid = GetArgValue(args, "--sid");
                string? profileId = GetArgValue(args, "--profile-id");

                if (string.IsNullOrWhiteSpace(sid) || string.IsNullOrWhiteSpace(profileId))
                {
                    Environment.Exit(PasswordLinker.ExitError); // 3 other failure
                    return;
                }

                var authStore = new RegistryAuthorizationStore();
                var existing = authStore.GetAuthorizedIdentities(sid);
                if (existing != null && !existing.Contains(profileId, StringComparer.OrdinalIgnoreCase) && existing.Length >= RegistryAuthorizationStore.MaxAuthorizedIdentities)
                {
                    Environment.Exit(PasswordLinker.ExitIdentityLimit); // 5
                    return;
                }

                var resolver = new AccountSidResolver();
                if (string.IsNullOrWhiteSpace(user))
                {
                    try
                    {
                        var secId = new SecurityIdentifier(sid);
                        user = ((NTAccount)secId.Translate(typeof(NTAccount))).Value;
                    }
                    catch
                    {
                        user = Environment.UserName;
                    }
                }
                else
                {
                    // Verify user resolves to sid
                    if (!resolver.ValidateUserSid(user, sid))
                    {
                        Environment.Exit(PasswordLinker.ExitError); // 3 account mismatch
                        return;
                    }
                }

                var window = new LinkPasswordWindow(user, sid, profileId, null, LinkPasswordMode.LinkPassword);
                window.ShowDialog();
                Environment.Exit(window.ExitCode);
                return;
            }

            // Normal application startup
            var mainWindow = new MainWindow();
            mainWindow.Show();
        }

        private static bool HasArg(string[] args, string flag)
        {
            foreach (var arg in args)
            {
                if (string.Equals(arg, flag, StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }

        private static string? GetArgValue(string[] args, string flag)
        {
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (string.Equals(args[i], flag, StringComparison.OrdinalIgnoreCase))
                {
                    return args[i + 1];
                }
            }
            return null;
        }
    }
}
