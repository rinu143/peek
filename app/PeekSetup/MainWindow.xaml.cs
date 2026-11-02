using System;
using System.Windows;
using System.Windows.Input;
using PeekSetup.ViewModels;

namespace PeekSetup
{
    /// <summary>
    /// Interaction logic for MainWindow.xaml
    /// </summary>
    public partial class MainWindow : Window
    {
        public MainWindow()
        {
            InitializeComponent();
            Loaded += async (_, _) =>
            {
                try
                {
                    var workArea = SystemParameters.WorkArea;
                    if (Width > workArea.Width) Width = workArea.Width;
                    if (Height > workArea.Height) Height = workArea.Height;
                }
                catch
                {
                    // Non-fatal fallback
                }

                if (DataContext is MainViewModel vm)
                {
                    await vm.CheckExistingProfileAsync();
                }
            };
        }

        protected override async void OnClosing(System.ComponentModel.CancelEventArgs e)
        {
            if (DataContext is MainViewModel vm && vm.IsEnrolling)
            {
                // Cleanly cancel pipe enrollment on window close
                try
                {
                    await vm.CancelEnrollmentFlowAsync();
                }
                catch
                {
                    // Ignore during shutdown
                }
            }
            base.OnClosing(e);
        }

        private async void ConfirmDialogSubmitButton_Click(object sender, RoutedEventArgs e)
        {
            if (DataContext is MainViewModel vm)
            {
                var sec = ConfirmDialogPasswordBox.SecurePassword;
                ConfirmDialogPasswordBox.Clear();
                await vm.VerifyConfirmPasswordAsync(sec);
            }
        }

        private async void ConfirmDialogPasswordBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (DataContext is MainViewModel vm)
            {
                if (e.Key == Key.Enter)
                {
                    var sec = ConfirmDialogPasswordBox.SecurePassword;
                    ConfirmDialogPasswordBox.Clear();
                    await vm.VerifyConfirmPasswordAsync(sec);
                    e.Handled = true;
                }
                else if (e.Key == Key.Escape)
                {
                    ConfirmDialogPasswordBox.Clear();
                    if (vm.CancelConfirmPasswordDialogCommand.CanExecute(null))
                    {
                        vm.CancelConfirmPasswordDialogCommand.Execute(null);
                    }
                    e.Handled = true;
                }
            }
        }

        private void ConfirmDialogPasswordBox_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (sender is System.Windows.Controls.PasswordBox pb && pb.IsVisible)
            {
                pb.Clear();
                pb.Focus();
            }
        }

        private void NewIdentityNameBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (DataContext is MainViewModel vm)
            {
                if (e.Key == Key.Enter && vm.ConfirmAddIdentityCommand.CanExecute(null))
                {
                    vm.ConfirmAddIdentityCommand.Execute(null);
                }
                else if (e.Key == Key.Escape && vm.CancelAddIdentityCommand.CanExecute(null))
                {
                    vm.CancelAddIdentityCommand.Execute(null);
                }
            }
        }

        private void NewIdentityNameBox_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (sender is System.Windows.Controls.TextBox tb && tb.IsVisible)
            {
                tb.Focus();
                tb.SelectAll();
            }
        }

        private void WelcomeDisplayNameBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter && DataContext is MainViewModel vm)
            {
                if (vm.BeginEnrollmentCommand.CanExecute(null))
                {
                    vm.BeginEnrollmentCommand.Execute(null);
                }
            }
        }

        private void EditNameBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (sender is System.Windows.Controls.TextBox tb && tb.DataContext is IdentityItemViewModel item)
            {
                if (e.Key == Key.Enter)
                {
                    if (item.SaveEditCommand.CanExecute(null))
                    {
                        item.SaveEditCommand.Execute(null);
                    }
                    e.Handled = true;
                }
                else if (e.Key == Key.Escape)
                {
                    if (item.CancelEditCommand.CanExecute(null))
                    {
                        item.CancelEditCommand.Execute(null);
                    }
                    e.Handled = true;
                }
            }
        }

        private void EditNameBox_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (sender is System.Windows.Controls.TextBox tb && tb.IsVisible)
            {
                tb.Focus();
                tb.SelectAll();
            }
        }
    }
}