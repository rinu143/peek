using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public static class NotificationService
    {
        public static void ShowUpdateNotification(UpdateManifest manifest)
        {
            string title = manifest.Critical ? "Peek Critical Security Update" : "Peek Update Available";
            string body = manifest.Critical
                ? $"Peek {manifest.Version} is a critical update. Open Peek Setup to install."
                : $"Peek {manifest.Version} is available.";

            // Attempt 1: Native Windows 10/11 Toast via Windows Script / PowerShell
            bool toastShown = TryShowWindowsToast(title, body, manifest.Critical);

            // Attempt 2: Balloon tip fallback
            if (!toastShown)
            {
                ShowBalloonFallback(title, body, manifest.Critical ? ToolTipIcon.Warning : ToolTipIcon.Info);
            }
        }

        private static bool TryShowWindowsToast(string title, string body, bool isCritical)
        {
            try
            {
                // PowerShell snippet to fire native Windows toast
                string psScript = $@"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = '<toast><visual><binding template=""ToastGeneric""><text>{EscapeXml(title)}</text><text>{EscapeXml(body)}</text></binding></visual></toast>'
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Peek').Show($toast)
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
                    proc.WaitForExit(3000);
                    return proc.ExitCode == 0;
                }
            }
            catch
            {
                // Fall back to balloon
            }

            return false;
        }

        public static void ShowBalloonFallback(string title, string message, ToolTipIcon icon)
        {
            try
            {
                using var notifyIcon = new NotifyIcon();
                notifyIcon.Icon = SystemIcons.Shield;
                notifyIcon.Visible = true;
                notifyIcon.ShowBalloonTip(5000, title, message, icon);

                // Wait briefly for the balloon tip to dispatch
                System.Threading.Thread.Sleep(500);
                notifyIcon.Visible = false;
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"Balloon fallback error: {ex.Message}");
            }
        }

        private static string EscapeXml(string text)
        {
            return text.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;").Replace("\"", "&quot;").Replace("'", "&apos;");
        }
    }
}
