using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Input;
using PeekUpdate.Models;
using PeekUpdate.Services;

namespace PeekSetup.ViewModels
{
    public partial class MainViewModel
    {
        private readonly PolicyManager _updatePolicyManager = new();
        private readonly SignatureVerifier _updateSignatureVerifier = new();
        private UpdateChecker? _updateChecker;
        private UpdateDownloader? _updateDownloader;

        private bool _isUpdatesDialogOpen;
        public bool IsUpdatesDialogOpen
        {
            get => _isUpdatesDialogOpen;
            set
            {
                if (_isUpdatesDialogOpen != value)
                {
                    _isUpdatesDialogOpen = value;
                    OnPropertyChanged();
                    if (value) RefreshUpdateState();
                }
            }
        }

        private string _updateCurrentVersion = string.Empty;
        public string UpdateCurrentVersion
        {
            get => _updateCurrentVersion;
            set { _updateCurrentVersion = value; OnPropertyChanged(); }
        }

        private string _updateChannel = "stable";
        public string UpdateChannel
        {
            get => _updateChannel;
            set { _updateChannel = value; OnPropertyChanged(); }
        }

        private bool _isChannelSelectionEnabled = true;
        public bool IsChannelSelectionEnabled
        {
            get => _isChannelSelectionEnabled;
            set { _isChannelSelectionEnabled = value; OnPropertyChanged(); }
        }

        private bool _updateAutoCheckEnabled = true;
        public bool UpdateAutoCheckEnabled
        {
            get => _updateAutoCheckEnabled;
            set { _updateAutoCheckEnabled = value; OnPropertyChanged(); }
        }

        private bool _isAutoCheckToggleEnabled = true;
        public bool IsAutoCheckToggleEnabled
        {
            get => _isAutoCheckToggleEnabled;
            set { _isAutoCheckToggleEnabled = value; OnPropertyChanged(); }
        }

        private string _updatePolicyManagedNotice = string.Empty;
        public string UpdatePolicyManagedNotice
        {
            get => _updatePolicyManagedNotice;
            set { _updatePolicyManagedNotice = value; OnPropertyChanged(); }
        }

        private bool _isPolicyManaged;
        public bool IsPolicyManaged
        {
            get => _isPolicyManaged;
            set { _isPolicyManaged = value; OnPropertyChanged(); }
        }

        private string _updateLastCheckedText = "Never";
        public string UpdateLastCheckedText
        {
            get => _updateLastCheckedText;
            set { _updateLastCheckedText = value; OnPropertyChanged(); }
        }

        private bool _isCheckingForUpdates;
        public bool IsCheckingForUpdates
        {
            get => _isCheckingForUpdates;
            set { _isCheckingForUpdates = value; OnPropertyChanged(); }
        }

        private bool _isUpdateAvailable;
        public bool IsUpdateAvailable
        {
            get => _isUpdateAvailable;
            set { _isUpdateAvailable = value; OnPropertyChanged(); }
        }

        private string _updateAvailableVersion = string.Empty;
        public string UpdateAvailableVersion
        {
            get => _updateAvailableVersion;
            set { _updateAvailableVersion = value; OnPropertyChanged(); }
        }

        private bool _isCriticalUpdate;
        public bool IsCriticalUpdate
        {
            get => _isCriticalUpdate;
            set { _isCriticalUpdate = value; OnPropertyChanged(); }
        }

        private string _updateReleaseDate = string.Empty;
        public string UpdateReleaseDate
        {
            get => _updateReleaseDate;
            set { _updateReleaseDate = value; OnPropertyChanged(); }
        }

        private string _updateReleaseNotesUrl = string.Empty;
        public string UpdateReleaseNotesUrl
        {
            get => _updateReleaseNotesUrl;
            set { _updateReleaseNotesUrl = value; OnPropertyChanged(); }
        }

        private string _updateStatusMessage = string.Empty;
        public string UpdateStatusMessage
        {
            get => _updateStatusMessage;
            set { _updateStatusMessage = value; OnPropertyChanged(); }
        }

        private string _updateErrorMessage = string.Empty;
        public string UpdateErrorMessage
        {
            get => _updateErrorMessage;
            set { _updateErrorMessage = value; OnPropertyChanged(); }
        }

        private bool _hasUpdateError;
        public bool HasUpdateError
        {
            get => _hasUpdateError;
            set { _hasUpdateError = value; OnPropertyChanged(); }
        }

        private bool _isDownloadingUpdate;
        public bool IsDownloadingUpdate
        {
            get => _isDownloadingUpdate;
            set { _isDownloadingUpdate = value; OnPropertyChanged(); }
        }

        private double _downloadProgressPercentage;
        public double DownloadProgressPercentage
        {
            get => _downloadProgressPercentage;
            set { _downloadProgressPercentage = value; OnPropertyChanged(); }
        }

        private string _downloadProgressText = string.Empty;
        public string DownloadProgressText
        {
            get => _downloadProgressText;
            set { _downloadProgressText = value; OnPropertyChanged(); }
        }

        private bool _isUpdateDownloaded;
        public bool IsUpdateDownloaded
        {
            get => _isUpdateDownloaded;
            set { _isUpdateDownloaded = value; OnPropertyChanged(); }
        }

        private bool _isInstallingUpdate;
        public bool IsInstallingUpdate
        {
            get => _isInstallingUpdate;
            set { _isInstallingUpdate = value; OnPropertyChanged(); }
        }

        private bool _hasUpdateNotificationBadge;
        public bool HasUpdateNotificationBadge
        {
            get => _hasUpdateNotificationBadge;
            set { _hasUpdateNotificationBadge = value; OnPropertyChanged(); }
        }

        // Commands
        public ICommand OpenUpdatesDialogCommand { get; private set; } = null!;
        public ICommand CloseUpdatesDialogCommand { get; private set; } = null!;
        public ICommand CheckUpdatesNowCommand { get; private set; } = null!;
        public ICommand OpenReleaseNotesCommand { get; private set; } = null!;
        public ICommand InstallUpdateCommand { get; private set; } = null!;
        public ICommand ToggleAutoCheckCommand { get; private set; } = null!;
        public ICommand SwitchChannelCommand { get; private set; } = null!;
        public ICommand SnoozeUpdateCommand { get; private set; } = null!;
        public ICommand SkipVersionCommand { get; private set; } = null!;

        private void InitializeUpdates()
        {
            _updateChecker = new UpdateChecker(_updateSignatureVerifier, _updatePolicyManager);
            _updateDownloader = new UpdateDownloader();

            UpdateCurrentVersion = "v" + App.GetAppVersion();

            OpenUpdatesDialogCommand = new RelayCommand(() => IsUpdatesDialogOpen = true);
            CloseUpdatesDialogCommand = new RelayCommand(() => IsUpdatesDialogOpen = false);
            CheckUpdatesNowCommand = new AsyncRelayCommand(CheckUpdatesNowAsync);
            OpenReleaseNotesCommand = new RelayCommand(OpenReleaseNotes);
            InstallUpdateCommand = new AsyncRelayCommand(InstallUpdateAsync);
            ToggleAutoCheckCommand = new RelayCommand(ToggleAutoCheck);
            SwitchChannelCommand = new RelayCommand(p => SwitchChannel(p as string));
            SnoozeUpdateCommand = new RelayCommand(SnoozeUpdate);
            SkipVersionCommand = new RelayCommand(SkipVersion);

            RefreshUpdateState();
        }

        public void RefreshUpdateState()
        {
            UpdateCurrentVersion = "v" + App.GetAppVersion();

            // Load policy & settings
            int? policyAutoCheck = _updatePolicyManager.GetHklmAutoCheck();
            string? policyChannel = _updatePolicyManager.GetHklmChannel();

            IsAutoCheckToggleEnabled = !policyAutoCheck.HasValue;
            IsChannelSelectionEnabled = string.IsNullOrWhiteSpace(policyChannel);

            IsPolicyManaged = policyAutoCheck.HasValue || !string.IsNullOrWhiteSpace(policyChannel);
            UpdatePolicyManagedNotice = IsPolicyManaged ? "Some settings are managed by your administrator policy." : string.Empty;

            UpdateAutoCheckEnabled = _updatePolicyManager.GetEffectiveAutoCheck();
            UpdateChannel = _updatePolicyManager.GetEffectiveChannel();

            // Load state
            var state = _updatePolicyManager.LoadUserState();
            if (!string.IsNullOrWhiteSpace(state.LastChecked))
            {
                if (DateTime.TryParse(state.LastChecked, out var dt))
                {
                    UpdateLastCheckedText = dt.ToLocalTime().ToString("g");
                }
                else
                {
                    UpdateLastCheckedText = state.LastChecked;
                }
            }
            else
            {
                UpdateLastCheckedText = "Never";
            }

            if (state.Status == "update_available" && !string.IsNullOrWhiteSpace(state.AvailableVersion))
            {
                IsUpdateAvailable = true;
                UpdateAvailableVersion = state.AvailableVersion;
                IsCriticalUpdate = state.Critical;
                UpdateReleaseDate = state.ReleaseDate ?? string.Empty;
                UpdateReleaseNotesUrl = state.NotesUrl ?? string.Empty;
                UpdateStatusMessage = $"Peek {state.AvailableVersion} is available.";
                HasUpdateNotificationBadge = true;

                // Check if already downloaded
                if (!string.IsNullOrWhiteSpace(state.LocalInstallerPath) && File.Exists(state.LocalInstallerPath))
                {
                    IsUpdateDownloaded = true;
                }
            }
            else
            {
                IsUpdateAvailable = false;
                HasUpdateNotificationBadge = false;
                UpdateStatusMessage = "Peek is up to date.";
            }

            HasUpdateError = !string.IsNullOrWhiteSpace(state.ErrorMessage);
            UpdateErrorMessage = state.ErrorMessage ?? string.Empty;
        }

        private async Task CheckUpdatesNowAsync()
        {
            if (IsCheckingForUpdates) return;

            IsCheckingForUpdates = true;
            HasUpdateError = false;
            UpdateErrorMessage = string.Empty;
            UpdateStatusMessage = "Checking for updates...";

            try
            {
                _updateChecker ??= new UpdateChecker(_updateSignatureVerifier, _updatePolicyManager);
                var result = await _updateChecker.CheckForUpdatesAsync(isInteractive: true);

                var state = _updatePolicyManager.LoadUserState();
                state.LastChecked = DateTime.UtcNow.ToString("o");
                state.InstalledVersion = result.InstalledVersion;
                state.Channel = result.Channel;

                if (!result.Success)
                {
                    HasUpdateError = true;
                    UpdateErrorMessage = result.ErrorMessage ?? "Update check failed.";
                    UpdateStatusMessage = "Check failed.";
                    state.Status = "error";
                    state.ErrorMessage = UpdateErrorMessage;
                    _updatePolicyManager.SaveUserState(state);
                    return;
                }

                if (result.UpdateAvailable && result.Manifest != null)
                {
                    var m = result.Manifest;
                    state.Status = "update_available";
                    state.AvailableVersion = m.Version;
                    state.Critical = m.Critical;
                    state.ReleaseDate = m.Released;
                    state.NotesUrl = m.NotesUrl;
                    state.DownloadUrl = m.Url;
                    state.Sha256 = m.Sha256;
                    state.Size = m.Size;
                    state.PublisherThumbprint = m.PublisherThumbprint;
                    state.ErrorMessage = null;
                    _updatePolicyManager.SaveUserState(state);

                    IsUpdateAvailable = true;
                    UpdateAvailableVersion = m.Version;
                    IsCriticalUpdate = m.Critical;
                    UpdateReleaseDate = m.Released;
                    UpdateReleaseNotesUrl = m.NotesUrl;
                    UpdateStatusMessage = $"Peek {m.Version} is available.";
                    HasUpdateNotificationBadge = true;
                    IsUpdateDownloaded = false;
                }
                else
                {
                    state.Status = "up_to_date";
                    state.AvailableVersion = null;
                    state.ErrorMessage = null;
                    _updatePolicyManager.SaveUserState(state);

                    IsUpdateAvailable = false;
                    HasUpdateNotificationBadge = false;
                    UpdateStatusMessage = "Peek is up to date. You are running the latest version.";
                }

                UpdateLastCheckedText = DateTime.Now.ToString("g");
            }
            catch (Exception ex)
            {
                HasUpdateError = true;
                UpdateErrorMessage = $"Unexpected check error: {ex.Message}";
                UpdateStatusMessage = "Check failed.";
            }
            finally
            {
                IsCheckingForUpdates = false;
            }
        }

        private void OpenReleaseNotes()
        {
            if (string.IsNullOrWhiteSpace(UpdateReleaseNotesUrl)) return;
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = UpdateReleaseNotesUrl,
                    UseShellExecute = true
                });
            }
            catch { }
        }

        private async Task InstallUpdateAsync()
        {
            var state = _updatePolicyManager.LoadUserState();
            if (string.IsNullOrWhiteSpace(state.AvailableVersion) || string.IsNullOrWhiteSpace(state.DownloadUrl))
            {
                HasUpdateError = true;
                UpdateErrorMessage = "No update details available to install.";
                return;
            }

            // 1. Download if not already downloaded
            string? installerPath = state.LocalInstallerPath;
            if (string.IsNullOrWhiteSpace(installerPath) || !File.Exists(installerPath))
            {
                IsDownloadingUpdate = true;
                DownloadProgressPercentage = 0;
                DownloadProgressText = "Starting download...";
                HasUpdateError = false;
                UpdateErrorMessage = string.Empty;

                try
                {
                    var manifest = new UpdateManifest
                    {
                        Version = state.AvailableVersion,
                        Url = state.DownloadUrl,
                        Sha256 = state.Sha256 ?? string.Empty,
                        Size = state.Size,
                        PublisherThumbprint = state.PublisherThumbprint ?? string.Empty,
                        Critical = state.Critical
                    };

                    _updateDownloader ??= new UpdateDownloader();
                    installerPath = await _updateDownloader.DownloadAndVerifyAsync(manifest, (read, total) =>
                    {
                        if (total > 0)
                        {
                            double pct = (double)read / total * 100.0;
                            DownloadProgressPercentage = pct;
                            DownloadProgressText = $"{pct:F0}% ({read / 1024 / 1024} MB / {total / 1024 / 1024} MB)";
                        }
                    });

                    state.LocalInstallerPath = installerPath;
                    state.Status = "downloaded";
                    _updatePolicyManager.SaveUserState(state);
                    IsUpdateDownloaded = true;
                }
                catch (Exception ex)
                {
                    HasUpdateError = true;
                    UpdateErrorMessage = $"Download or verification failed: {ex.Message}";
                    return;
                }
                finally
                {
                    IsDownloadingUpdate = false;
                }
            }

            // 2. Launch elevated apply flow
            IsInstallingUpdate = true;
            UpdateStatusMessage = "Launching installer (UAC elevation)...";

            bool launched = ElevatedInstaller.RelaunchElevated(state.AvailableVersion);
            if (!launched)
            {
                HasUpdateError = true;
                UpdateErrorMessage = "Administrator elevation was cancelled or failed.";
                IsInstallingUpdate = false;
            }
            else
            {
                UpdateStatusMessage = "Update installation in progress. Peek will restart when complete.";
            }
        }

        private void ToggleAutoCheck()
        {
            if (!IsAutoCheckToggleEnabled) return;
            UpdateAutoCheckEnabled = !UpdateAutoCheckEnabled;
            var settings = _updatePolicyManager.LoadUserSettings();
            settings.AutoCheck = UpdateAutoCheckEnabled;
            _updatePolicyManager.SaveUserSettings(settings);
        }

        private void SwitchChannel(string? newChannel)
        {
            if (!IsChannelSelectionEnabled || string.IsNullOrWhiteSpace(newChannel)) return;
            string cleanChannel = newChannel.ToLowerInvariant() == "beta" ? "beta" : "stable";
            UpdateChannel = cleanChannel;
            var settings = _updatePolicyManager.LoadUserSettings();
            settings.Channel = cleanChannel;
            _updatePolicyManager.SaveUserSettings(settings);

            // Re-check for updates on the new channel
            _ = CheckUpdatesNowAsync();
        }

        private void SnoozeUpdate()
        {
            _updatePolicyManager.Snooze(TimeSpan.FromDays(7));
            IsUpdatesDialogOpen = false;
        }

        private void SkipVersion()
        {
            if (!string.IsNullOrWhiteSpace(UpdateAvailableVersion))
            {
                _updatePolicyManager.SkipVersion(UpdateAvailableVersion);
                IsUpdateAvailable = false;
                HasUpdateNotificationBadge = false;
                IsUpdatesDialogOpen = false;
            }
        }
    }
}
