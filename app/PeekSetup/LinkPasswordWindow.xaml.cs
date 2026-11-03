using System;
using System.Security;
using System.Windows;
using System.Windows.Input;
using PeekSetup.Services;

namespace PeekSetup
{
    public enum LinkPasswordMode
    {
        LinkPassword,
        AuthorizeIdentity
    }

    public partial class LinkPasswordWindow : Window
    {
        private readonly string _targetUser;
        private readonly string _targetSid;
        private readonly string _profileId;
        private readonly IPasswordLinker _linker;
        private readonly LinkPasswordMode _mode;
        private int _attempts = 0;
        private const int MaxAttempts = 3;

        public int ExitCode { get; private set; } = PasswordLinker.ExitCancelled; // Default to 1 (cancelled by user)

        public LinkPasswordWindow(
            string targetUser,
            string targetSid,
            string profileId,
            IPasswordLinker? linker = null,
            LinkPasswordMode mode = LinkPasswordMode.LinkPassword)
        {
            InitializeComponent();
            _targetUser = targetUser;
            _targetSid = targetSid;
            _profileId = profileId;
            _linker = linker ?? new PasswordLinker();
            _mode = mode;

            MaxHeight = SystemParameters.WorkArea.Height * 0.90;
            TargetUserText.Text = $"Target Account: {_targetUser}";

            if (_mode == LinkPasswordMode.AuthorizeIdentity)
            {
                Title = "Authorize Windows Identity";
                LinkButton.Content = "Authorize";
            }

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
                if (_mode == LinkPasswordMode.AuthorizeIdentity)
                {
                    int res = _linker.AuthorizeIdentity(_profileId, securePassword, _targetUser, _targetSid);
                    if (res == PasswordLinker.ExitOk)
                    {
                        ExitCode = PasswordLinker.ExitOk; // 0
                        DialogResult = true;
                        Close();
                        return;
                    }

                    if (res == PasswordLinker.ExitWrongPassword) // 2
                    {
                        _attempts++;
                        PasswordInput.Clear();
                        if (_attempts >= MaxAttempts)
                        {
                            ExitCode = PasswordLinker.ExitWrongPassword;
                            DialogResult = false;
                            Close();
                        }
                        else
                        {
                            int remaining = MaxAttempts - _attempts;
                            ShowError($"Incorrect Windows password. {remaining} attempt(s) remaining.");
                        }
                        return;
                    }

                    ExitCode = res;
                    DialogResult = false;
                    Close();
                    return;
                }
                else
                {
                    int res = _linker.LinkPassword(_profileId, securePassword, _targetUser, _targetSid);
                    if (res == PasswordLinker.ExitOk)
                    {
                        ExitCode = PasswordLinker.ExitOk; // 0 linked
                        DialogResult = true;
                        Close();
                        return;
                    }

                    if (res == PasswordLinker.ExitWrongPassword)
                    {
                        // Password was rejected
                        _attempts++;
                        PasswordInput.Clear();

                        if (_attempts >= MaxAttempts)
                        {
                            ExitCode = PasswordLinker.ExitWrongPassword; // 2 wrong password
                            DialogResult = false;
                            Close();
                        }
                        else
                        {
                            int remaining = MaxAttempts - _attempts;
                            ShowError($"Incorrect Windows password. {remaining} attempt(s) remaining.");
                        }
                        return;
                    }

                    ExitCode = res;
                    DialogResult = false;
                    Close();
                    return;
                }
            }
            catch (Exception ex)
            {
                ExitCode = PasswordLinker.ExitError; // 3 other failure
                ShowError($"Failed to store credential: {ex.Message}");
            }
        }

        private void OnCancelClick(object sender, RoutedEventArgs e)
        {
            PasswordInput.Clear();
            ExitCode = PasswordLinker.ExitCancelled; // 1 cancelled by user
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
