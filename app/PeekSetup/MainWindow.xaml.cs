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
    }
}