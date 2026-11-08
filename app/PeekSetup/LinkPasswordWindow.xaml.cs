using System;
using System.Security;
using System.Windows;
using System.Windows.Input;
using PeekSetup.Services;

namespace PeekSetup
{
    public partial class LinkPasswordWindow : Window
    {
        private readonly string _targetUser;
        private readonly string _targetSid;
        private readonly string _profileId;
        private readonly IPasswordLinker _linker;
        private int _attempts = 0;
        private const int MaxAttempts = 3;

        public int ExitCode { get; private set; } = 1; // Default to 1 (cancelled by user)

        public LinkPasswordWindow(string targetUser, string targetSid, string profileId, IPasswordLinker? linker = null)
        {
            InitializeComponent();
            _targetUser = targetUser;
            _targetSid = targetSid;
            _profileId = profileId;
            _linker = linker ?? new PasswordLinker();

            MaxHeight = SystemParameters.WorkArea.Height * 0.90;
            TargetUserText.Text = $"Target Account: {_targetUser}";
            Loaded += (_, _) => PasswordInput.Focus();
        }

        private void OnLinkClick(object sender, RoutedEventArgs e)
        {
            var securePassword = PasswordInput.SecurePassword;
            if (securePassword == null || securePassword.Length == 0)
            {
                ShowError("Please enter your current Windows password.");
                return;
            }

            try
            {
                bool success = _linker.LinkPassword(_profileId, securePassword, _targetUser, _targetSid);
                if (success)
                {
                    ExitCode = 0; // 0 linked
                    DialogResult = true;
                    Close();
                    return;
                }

                // Password was rejected
                _attempts++;
                PasswordInput.Clear();

                if (_attempts >= MaxAttempts)
                {
                    ExitCode = 2; // 2 wrong password
                    DialogResult = false;
                    Close();
                }
                else
                {
                    int remaining = MaxAttempts - _attempts;
                    ShowError($"Incorrect Windows password. {remaining} attempt(s) remaining.");
                }
            }
            catch (Exception ex)
            {
                ExitCode = 3; // 3 other failure
                ShowError($"Failed to store credential: {ex.Message}");
            }
        }

        private void OnCancelClick(object sender, RoutedEventArgs e)
        {
            ExitCode = 1; // 1 cancelled by user
            DialogResult = false;
            Close();
        }

        private void PasswordInput_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                OnLinkClick(sender, e);
            }
        }

        private void ShowError(string message)
        {
            ErrorMessage.Text = message;
            ErrorBorder.Visibility = Visibility.Visible;
            PasswordInput.Focus();
        }
    }
}
