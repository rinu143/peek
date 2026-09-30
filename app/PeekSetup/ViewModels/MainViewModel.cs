using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Security;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using PeekSetup.Models;
using PeekSetup.Services;

namespace PeekSetup.ViewModels
{
    public enum SetupStep
    {
        Welcome = 1,
        Enrollment = 2,
        PasswordLink = 3,
        Done = 4
    }

    public enum EnrollmentViewMode
    {
        Connecting,
        Enrolling,
        EngineUnavailable,
        Failed
    }

    public class MainViewModel : INotifyPropertyChanged
    {
        private readonly IEnrollmentPipeClient _pipeClient;
        private readonly IPasswordLinker _passwordLinker;

        private SetupStep _currentStep = SetupStep.Welcome;
        private string _displayName = Environment.UserName;

        // Enrollment view state
        private EnrollmentViewMode _enrollmentMode = EnrollmentViewMode.Connecting;
        private BitmapSource? _previewImage;
        private string _guidanceText = "Preparing camera...";
        private string _targetPoseName = "Look Straight";
        private string _qualityStatusText = "Initializing...";
        private bool _isQualityPassed = true;
        private double _progressPercentage;
        private int _currentPoseIndex;
        private string _engineErrorMessage = string.Empty;

        // Password linking state
        private string _enrolledProfileId = string.Empty;
        private string _enrolledDisplayName = string.Empty;
        private bool _isVerifyingPassword;
        private bool _hasPasswordError;
        private string _passwordErrorMessage = string.Empty;
        private bool _isPasswordLinked;

        public SetupStep CurrentStep
        {
            get => _currentStep;
            set
            {
                if (_currentStep != value)
                {
                    _currentStep = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(IsWelcomeStep));
                    OnPropertyChanged(nameof(IsEnrollmentStep));
                    OnPropertyChanged(nameof(IsPasswordLinkStep));
                    OnPropertyChanged(nameof(IsDoneStep));
                    OnPropertyChanged(nameof(StepNumberText));
                }
            }
        }

        public bool IsWelcomeStep => CurrentStep == SetupStep.Welcome;
        public bool IsEnrollmentStep => CurrentStep == SetupStep.Enrollment;
        public bool IsPasswordLinkStep => CurrentStep == SetupStep.PasswordLink;
        public bool IsDoneStep => CurrentStep == SetupStep.Done;
        public string StepNumberText => $"Step {(int)CurrentStep} of 4";

        public string DisplayName
        {
            get => _displayName;
            set
            {
                if (_displayName != value)
                {
                    _displayName = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(CanBeginEnrollment));
                }
            }
        }

        public bool CanBeginEnrollment => !string.IsNullOrWhiteSpace(DisplayName);

        public EnrollmentViewMode EnrollmentMode
        {
            get => _enrollmentMode;
            set
            {
                if (_enrollmentMode != value)
                {
                    _enrollmentMode = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(IsConnecting));
                    OnPropertyChanged(nameof(IsEnrolling));
                    OnPropertyChanged(nameof(IsEngineUnavailable));
                    OnPropertyChanged(nameof(IsEnrollmentFailed));
                }
            }
        }

        public bool IsConnecting => EnrollmentMode == EnrollmentViewMode.Connecting;
        public bool IsEnrolling => EnrollmentMode == EnrollmentViewMode.Enrolling;
        public bool IsEngineUnavailable => EnrollmentMode == EnrollmentViewMode.EngineUnavailable;
        public bool IsEnrollmentFailed => EnrollmentMode == EnrollmentViewMode.Failed;

        public BitmapSource? PreviewImage
        {
            get => _previewImage;
            set
            {
                _previewImage = value;
                OnPropertyChanged();
            }
        }

        public string GuidanceText
        {
            get => _guidanceText;
            set
            {
                if (_guidanceText != value)
                {
                    _guidanceText = value;
                    OnPropertyChanged();
                }
            }
        }

        public string TargetPoseName
        {
            get => _targetPoseName;
            set
            {
                if (_targetPoseName != value)
                {
                    _targetPoseName = value;
                    OnPropertyChanged();
                }
            }
        }

        public string QualityStatusText
        {
            get => _qualityStatusText;
            set
            {
                if (_qualityStatusText != value)
                {
                    _qualityStatusText = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsQualityPassed
        {
            get => _isQualityPassed;
            set
            {
                if (_isQualityPassed != value)
                {
                    _isQualityPassed = value;
                    OnPropertyChanged();
                }
            }
        }

        public double ProgressPercentage
        {
            get => _progressPercentage;
            set
            {
                if (Math.Abs(_progressPercentage - value) > 0.01)
                {
                    _progressPercentage = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(ProgressPercentageText));
                }
            }
        }

        public string ProgressPercentageText => $"{Math.Clamp(Math.Round(ProgressPercentage), 0, 100)}%";

        public int CurrentPoseIndex
        {
            get => _currentPoseIndex;
            set
            {
                if (_currentPoseIndex != value)
                {
                    _currentPoseIndex = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(PoseStepCounterText));
                }
            }
        }

        public string PoseStepCounterText => $"Pose {Math.Min(CurrentPoseIndex + 1, 9)} of 9";

        public ObservableCollection<PoseItemViewModel> Poses { get; } = new();

        public string EngineErrorMessage
        {
            get => _engineErrorMessage;
            set
            {
                if (_engineErrorMessage != value)
                {
                    _engineErrorMessage = value;
                    OnPropertyChanged();
                }
            }
        }

        // Password linking properties
        public string EnrolledProfileId
        {
            get => _enrolledProfileId;
            set
            {
                if (_enrolledProfileId != value)
                {
                    _enrolledProfileId = value;
                    OnPropertyChanged();
                }
            }
        }

        public string EnrolledDisplayName
        {
            get => _enrolledDisplayName;
            set
            {
                if (_enrolledDisplayName != value)
                {
                    _enrolledDisplayName = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsVerifyingPassword
        {
            get => _isVerifyingPassword;
            set
            {
                if (_isVerifyingPassword != value)
                {
                    _isVerifyingPassword = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(CanLinkPassword));
                }
            }
        }

        public bool CanLinkPassword => !IsVerifyingPassword;

        public bool HasPasswordError
        {
            get => _hasPasswordError;
            set
            {
                if (_hasPasswordError != value)
                {
                    _hasPasswordError = value;
                    OnPropertyChanged();
                }
            }
        }

        public string PasswordErrorMessage
        {
            get => _passwordErrorMessage;
            set
            {
                if (_passwordErrorMessage != value)
                {
                    _passwordErrorMessage = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsPasswordLinked
        {
            get => _isPasswordLinked;
            set
            {
                if (_isPasswordLinked != value)
                {
                    _isPasswordLinked = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(SummaryUnlockMode));
                    OnPropertyChanged(nameof(UnlockBadgeText));
                    OnPropertyChanged(nameof(UnlockBadgeColor));
                }
            }
        }

        public string SummaryUnlockMode => IsPasswordLinked
            ? "Automatic Windows Unlock Enabled"
            : "Face Recognition Only (PIN/Password fallback)";

        public string UnlockBadgeText => IsPasswordLinked ? "Password Linked" : "Face Only (Password Skipped)";
        public string UnlockBadgeColor => IsPasswordLinked ? "#10B981" : "#F59E0B";

        // Commands
        public ICommand BeginEnrollmentCommand { get; }
        public ICommand CancelEnrollmentCommand { get; }
        public ICommand RetryConnectionCommand { get; }
        public ICommand RetryEnrollmentCommand { get; }
        public ICommand BackToWelcomeCommand { get; }
        public ICommand SkipPasswordCommand { get; }
        public ICommand FinishCommand { get; }

        public MainViewModel(IEnrollmentPipeClient? pipeClient = null, IPasswordLinker? passwordLinker = null)
        {
            _pipeClient = pipeClient ?? new EnrollmentPipeClient();
            _passwordLinker = passwordLinker ?? new PasswordLinker();

            InitializePoses();

            _pipeClient.PreviewFrameReceived += OnPreviewFrameReceived;
            _pipeClient.PoseCompleted += OnPoseCompleted;
            _pipeClient.EnrollmentCompleted += OnEnrollmentCompleted;
            _pipeClient.EnrollmentFailed += OnEnrollmentFailed;
            _pipeClient.ErrorOccurred += OnErrorOccurred;
            _pipeClient.Disconnected += OnDisconnected;

            BeginEnrollmentCommand = new AsyncRelayCommand(StartEnrollmentFlowAsync, () => CanBeginEnrollment);
            CancelEnrollmentCommand = new AsyncRelayCommand(CancelEnrollmentFlowAsync);
            RetryConnectionCommand = new AsyncRelayCommand(StartEnrollmentFlowAsync);
            RetryEnrollmentCommand = new AsyncRelayCommand(StartEnrollmentFlowAsync);
            BackToWelcomeCommand = new RelayCommand(GoBackToWelcome);
            SkipPasswordCommand = new RelayCommand(SkipPasswordLinking);
            FinishCommand = new RelayCommand(FinishSetup);
        }

        private void InitializePoses()
        {
            Poses.Clear();
            for (int i = 0; i < EnrollmentProtocol.OrderedPoses.Length; i++)
            {
                string poseKey = EnrollmentProtocol.OrderedPoses[i];
                string label = EnrollmentProtocol.GetPoseFriendlyName(poseKey);
                Poses.Add(new PoseItemViewModel(poseKey, label, i));
            }
        }

        private void ResetPoseStates()
        {
            for (int i = 0; i < Poses.Count; i++)
            {
                Poses[i].State = i == 0 ? PoseState.Active : PoseState.Pending;
            }
            CurrentPoseIndex = 0;
            ProgressPercentage = 0;
            TargetPoseName = EnrollmentProtocol.GetPoseFriendlyName(EnrollmentProtocol.PoseCenter);
            GuidanceText = "Look directly at the camera";
            QualityStatusText = "Connecting...";
            IsQualityPassed = true;
        }

        public async Task StartEnrollmentFlowAsync()
        {
            CurrentStep = SetupStep.Enrollment;
            EnrollmentMode = EnrollmentViewMode.Connecting;
            ResetPoseStates();
            PreviewImage = null;
            EngineErrorMessage = string.Empty;

            try
            {
                await _pipeClient.ConnectAsync();
                EnrollmentMode = EnrollmentViewMode.Enrolling;
                await _pipeClient.StartEnrollmentAsync(DisplayName);
            }
            catch (EngineUnavailableException ex)
            {
                EnrollmentMode = EnrollmentViewMode.EngineUnavailable;
                EngineErrorMessage = ex.Message;
            }
            catch (Exception ex)
            {
                EnrollmentMode = EnrollmentViewMode.Failed;
                EngineErrorMessage = $"Could not initiate face enrollment: {ex.Message}";
            }
        }

        public async Task CancelEnrollmentFlowAsync()
        {
            try
            {
                await _pipeClient.CancelEnrollmentAsync();
                await _pipeClient.DisconnectAsync();
            }
            catch
            {
                // Ignored during cancel
            }
            finally
            {
                CurrentStep = SetupStep.Welcome;
                PreviewImage = null;
            }
        }

        public void GoBackToWelcome()
        {
            CurrentStep = SetupStep.Welcome;
            PreviewImage = null;
        }

        public async Task LinkPasswordAsync(SecureString password)
        {
            if (password == null || password.Length == 0)
            {
                HasPasswordError = true;
                PasswordErrorMessage = "Please enter your current Windows password.";
                return;
            }

            IsVerifyingPassword = true;
            HasPasswordError = false;
            PasswordErrorMessage = string.Empty;

            try
            {
                // Verify and link password in background thread
                bool success = await Task.Run(() =>
                    _passwordLinker.LinkPassword(EnrolledProfileId, password, Environment.UserName));

                if (success)
                {
                    IsPasswordLinked = true;
                    CurrentStep = SetupStep.Done;
                }
                else
                {
                    HasPasswordError = true;
                    PasswordErrorMessage = "The Windows password was not accepted. Please check your password and try again.";
                }
            }
            catch (Exception ex)
            {
                HasPasswordError = true;
                PasswordErrorMessage = $"Failed to secure password: {ex.Message}";
            }
            finally
            {
                IsVerifyingPassword = false;
            }
        }

        public void SkipPasswordLinking()
        {
            IsPasswordLinked = false;
            CurrentStep = SetupStep.Done;
        }

        public void FinishSetup()
        {
            Application.Current?.Shutdown();
        }

        // IPC Event Handlers
        private void OnPreviewFrameReceived(object? sender, PreviewFrameEventArgs e)
        {
            RunOnUi(() =>
            {
                PreviewImage = e.PreviewImage;
                GuidanceText = string.IsNullOrWhiteSpace(e.GuidanceText)
                    ? EnrollmentProtocol.GetPoseFriendlyName(e.TargetPose)
                    : e.GuidanceText;
                TargetPoseName = EnrollmentProtocol.GetPoseFriendlyName(e.TargetPose);
                ProgressPercentage = e.ProgressPercentage;

                IsQualityPassed = string.Equals(e.QualityState, "PASSED", StringComparison.OrdinalIgnoreCase);
                QualityStatusText = FormatQualityText(e.QualityState);
            });
        }

        private static string FormatQualityText(string qualityState) => qualityState?.ToUpperInvariant() switch
        {
            "PASSED" => "Face detected & aligned",
            "POOR_LIGHTING" => "Lighting is poor — face a light source",
            "BLURRY" => "Image blurry — hold steady",
            "OCCLUDED" => "Face occluded — clear face area",
            "NO_FACE" => "No face detected — center in frame",
            "MULTIPLE_FACES" => "Multiple faces detected — only one person allowed",
            _ => string.IsNullOrEmpty(qualityState) ? "Analyzing frame..." : qualityState
        };

        private void OnPoseCompleted(object? sender, PoseCompletedEventArgs e)
        {
            RunOnUi(() =>
            {
                int completedIdx = e.PoseIndex;
                if (completedIdx >= 0 && completedIdx < Poses.Count)
                {
                    Poses[completedIdx].State = PoseState.Completed;
                }

                int nextIdx = completedIdx + 1;
                CurrentPoseIndex = nextIdx;
                if (nextIdx < Poses.Count)
                {
                    Poses[nextIdx].State = PoseState.Active;
                    TargetPoseName = Poses[nextIdx].Label;
                }
            });
        }

        private void OnEnrollmentCompleted(object? sender, EnrollmentCompletedEventArgs e)
        {
            RunOnUi(() =>
            {
                EnrolledProfileId = e.ProfileId;
                EnrolledDisplayName = e.DisplayName;

                for (int i = 0; i < Poses.Count; i++)
                {
                    Poses[i].State = PoseState.Completed;
                }
                ProgressPercentage = 100.0;

                // Advance to Step 3: Password Linking
                CurrentStep = SetupStep.PasswordLink;
            });
        }

        private void OnEnrollmentFailed(object? sender, EnrollmentFailedEventArgs e)
        {
            RunOnUi(() =>
            {
                EnrollmentMode = EnrollmentViewMode.Failed;
                EngineErrorMessage = string.IsNullOrWhiteSpace(e.Detail)
                    ? $"Enrollment could not be completed ({e.ReasonCode})."
                    : e.Detail;
            });
        }

        private void OnErrorOccurred(object? sender, EnrollmentErrorEventArgs e)
        {
            RunOnUi(() =>
            {
                if (e.ReasonCode == EnrollmentProtocol.ReasonCameraUnavailable ||
                    e.ReasonCode == EnrollmentProtocol.ReasonCameraError)
                {
                    EnrollmentMode = EnrollmentViewMode.Failed;
                    EngineErrorMessage = string.IsNullOrWhiteSpace(e.Detail)
                        ? "Webcam unavailable or in use by another application."
                        : $"Webcam unavailable: {e.Detail}";
                }
                else if (e.ReasonCode == EnrollmentProtocol.ReasonSessionBusy)
                {
                    EnrollmentMode = EnrollmentViewMode.Failed;
                    EngineErrorMessage = "Face recognition engine is busy. Please try again in a moment.";
                }
                else
                {
                    EnrollmentMode = EnrollmentViewMode.Failed;
                    EngineErrorMessage = e.Detail;
                }
            });
        }

        private void OnDisconnected(object? sender, EventArgs e)
        {
            RunOnUi(() =>
            {
                if (EnrollmentMode == EnrollmentViewMode.Enrolling)
                {
                    EnrollmentMode = EnrollmentViewMode.EngineUnavailable;
                    EngineErrorMessage = "Connection to Peek engine was lost.";
                }
            });
        }

        private static void RunOnUi(Action action)
        {
            var app = Application.Current;
            if (app != null && app.Dispatcher != null && !app.Dispatcher.CheckAccess())
            {
                app.Dispatcher.InvokeAsync(action);
            }
            else
            {
                action();
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string? name = null) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
