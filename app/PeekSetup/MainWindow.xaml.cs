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
        }

        private async void OnLinkPasswordClicked(object sender, RoutedEventArgs e)
        {
            if (DataContext is MainViewModel vm)
            {
                // CRITICAL SECURITY REQUIREMENT:
                // Use SecurePassword directly from PasswordBox without ever converting to a managed String
                var securePass = PasswordInput.SecurePassword;
                await vm.LinkPasswordAsync(securePass);
                PasswordInput.Clear();
            }
        }

        private void PasswordInput_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                OnLinkPasswordClicked(sender, e);
            }
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
    }
}