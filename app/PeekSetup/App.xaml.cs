using System;
using System.Windows;
using PeekSetup.Services;

namespace PeekSetup
{
    /// <summary>
    /// Interaction logic for App.xaml
    /// </summary>
    public partial class App : Application
    {
        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);

            // Initialize dynamic theme based on user's Windows system preference (Light / Dark)
            ThemeManager.InitializeTheme();

            string[] args = e.Args;

            // Check for --unlink --sid <SID>
            if (HasArg(args, "--unlink"))
            {
                string? sid = GetArgValue(args, "--sid");
                if (!string.IsNullOrEmpty(sid))
                {
                    var linker = new PasswordLinker();
                    linker.UnlinkPassword(sid);
                }
                Environment.Exit(0);
                return;
            }

            // Check for --link-password --user <DOMAIN\user> --sid <SID> --profile-id <id>
            if (HasArg(args, "--link-password"))
            {
                string? user = GetArgValue(args, "--user");
                string? sid = GetArgValue(args, "--sid");
                string? profileId = GetArgValue(args, "--profile-id");

                if (string.IsNullOrWhiteSpace(user) || string.IsNullOrWhiteSpace(sid) || string.IsNullOrWhiteSpace(profileId))
                {
                    Environment.Exit(3); // 3 other failure
                    return;
                }

                // Verify user resolves to sid
                var resolver = new AccountSidResolver();
                if (!resolver.ValidateUserSid(user, sid))
                {
                    Environment.Exit(3); // other failure / account mismatch
                    return;
                }

                var window = new LinkPasswordWindow(user, sid, profileId);
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
