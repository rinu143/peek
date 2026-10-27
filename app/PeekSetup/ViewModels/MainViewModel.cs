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
        Done = 4,
        AlreadyEnrolled = 5
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
        private readonly IPasswordLinkLauncher _linkLauncher;

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

        // Native Overlay & Face Tracking State
        private bool _hasOverlay;
        private bool _isFaceDetected;
        private double _faceBboxLeft;
        private double _faceBboxTop;
        private double _faceBboxWidth;
        private double _faceBboxHeight;
        private bool _isPoseMatched;
        private bool _isBracketGreen;
        private double _sampleProgress;
        private double _targetDotX = 155.0;
        private double _targetDotY = 155.0;
        private bool _isTargetDotVisible;
        private bool _isPoseFlashActive;
        private bool _isPoseCheckActive;
        private bool _isEnrollmentCompleteRing;
        private bool _isDetailsExpanded;
        private string _rawTechnicalDetails = string.Empty;

        // Advanced Face Tracking, Biometric Constellation & 3D Guidance
        private bool _hasLandmarks;
        private string _biometricMeshPathData = string.Empty;
        private double _leftEyeX, _leftEyeY;
        private double _rightEyeX, _rightEyeY;
        private double _noseX, _noseY;
        private double _leftMouthX, _leftMouthY;
        private double _rightMouthX, _rightMouthY;
        private string _targetPoseKey = EnrollmentProtocol.PoseCenter;
        private double _targetAngleDegrees;
        private double _targetBeaconX = 155.0;
        private double _targetBeaconY = 155.0;
        private double _centerReticleX = 155.0;
        private double _centerReticleY = 155.0;
        private int _samplesCollected;
        private int _samplesTarget = 2;

        // Password linking state
        private string _enrolledProfileId = string.Empty;
        private string _enrolledDisplayName = string.Empty;
        private bool _isVerifyingPassword;
        private bool _hasPasswordError;
        private string _passwordErrorMessage = string.Empty;
        private bool _isPasswordLinked;

        // Existing profile state
        private string _existingProfileName = string.Empty;
        private string _existingProfileId = string.Empty;
        private bool _isCheckingStatus;

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
                    OnPropertyChanged(nameof(IsAlreadyEnrolledStep));
                    OnPropertyChanged(nameof(ShowStepIndicator));
                    OnPropertyChanged(nameof(StepNumberText));
                }
            }
        }

        public bool IsWelcomeStep => CurrentStep == SetupStep.Welcome && !IsCheckingStatus;
        public bool IsEnrollmentStep => CurrentStep == SetupStep.Enrollment;
        public bool IsPasswordLinkStep => CurrentStep == SetupStep.PasswordLink;
        public bool IsDoneStep => CurrentStep == SetupStep.Done;
        public bool IsAlreadyEnrolledStep => CurrentStep == SetupStep.AlreadyEnrolled;
        public bool ShowStepIndicator => CurrentStep != SetupStep.AlreadyEnrolled;
        public string StepNumberText => CurrentStep == SetupStep.AlreadyEnrolled ? "Active Profile" : $"Step {(int)CurrentStep} of 4";

        public string ExistingProfileName
        {
            get => _existingProfileName;
            set
            {
                if (_existingProfileName != value)
                {
                    _existingProfileName = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(AlreadyEnrolledTitle));
                }
            }
        }

        public string ExistingProfileId
        {
            get => _existingProfileId;
            set
            {
                if (_existingProfileId != value)
                {
                    _existingProfileId = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsCheckingStatus
        {
            get => _isCheckingStatus;
            set
            {
                if (_isCheckingStatus != value)
                {
                    _isCheckingStatus = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(IsWelcomeStep));
                }
            }
        }

        public string AlreadyEnrolledTitle => $"You're already set up as {ExistingProfileName}";

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

        public bool HasOverlay
        {
            get => _hasOverlay;
            private set
            {
                if (_hasOverlay != value)
                {
                    _hasOverlay = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsFaceDetected
        {
            get => _isFaceDetected;
            private set
            {
                if (_isFaceDetected != value)
                {
                    _isFaceDetected = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(CornerBracketsData));
                }
            }
        }

        public double FaceBboxLeft
        {
            get => _faceBboxLeft;
            private set
            {
                if (Math.Abs(_faceBboxLeft - value) > 0.1)
                {
                    _faceBboxLeft = value;
                    OnPropertyChanged();
                }
            }
        }

        public double FaceBboxTop
        {
            get => _faceBboxTop;
            private set
            {
                if (Math.Abs(_faceBboxTop - value) > 0.1)
                {
                    _faceBboxTop = value;
                    OnPropertyChanged();
                }
            }
        }

        public double FaceBboxWidth
        {
            get => _faceBboxWidth;
            private set
            {
                if (Math.Abs(_faceBboxWidth - value) > 0.1)
                {
                    _faceBboxWidth = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(CornerBracketsData));
                }
            }
        }

        public double FaceBboxHeight
        {
            get => _faceBboxHeight;
            private set
            {
                if (Math.Abs(_faceBboxHeight - value) > 0.1)
                {
                    _faceBboxHeight = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(CornerBracketsData));
                }
            }
        }

        public string CornerBracketsData
        {
            get
            {
                if (!IsFaceDetected || FaceBboxWidth < 10 || FaceBboxHeight < 10)
                    return string.Empty;

                double w = FaceBboxWidth;
                double h = FaceBboxHeight;
                double arm = Math.Clamp(Math.Min(w, h) * 0.22, 10.0, 24.0);

                return string.Format(System.Globalization.CultureInfo.InvariantCulture,
                    "M 0,{2:F1} L 0,0 L {2:F1},0 M {0:F1},0 L {1:F1},0 L {1:F1},{2:F1} M {1:F1},{3:F1} L {1:F1},{4:F1} L {0:F1},{4:F1} M {2:F1},{4:F1} L 0,{4:F1} L 0,{3:F1}",
                    w - arm, w, arm, h - arm, h);
            }
        }

        public bool IsPoseMatched
        {
            get => _isPoseMatched;
            private set
            {
                if (_isPoseMatched != value)
                {
                    _isPoseMatched = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsBracketGreen
        {
            get => _isBracketGreen;
            private set
            {
                if (_isBracketGreen != value)
                {
                    _isBracketGreen = value;
                    OnPropertyChanged();
                }
            }
        }

        public double SampleProgress
        {
            get => _sampleProgress;
            private set
            {
                if (Math.Abs(_sampleProgress - value) > 0.001)
                {
                    _sampleProgress = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(SampleProgressRingData));
                }
            }
        }

        public string SampleProgressRingData
        {
            get
            {
                if (IsEnrollmentCompleteRing || SampleProgress >= 0.999)
                {
                    return "M 155,7 A 148,148 0 1 1 154.99,7";
                }
                if (SampleProgress <= 0.005)
                {
                    return string.Empty;
                }

                double angle = SampleProgress * 360.0;
                double rad = (angle - 90.0) * Math.PI / 180.0;
                double endX = 155.0 + 148.0 * Math.Cos(rad);
                double endY = 155.0 + 148.0 * Math.Sin(rad);
                int isLargeArc = angle > 180.0 ? 1 : 0;
                return string.Format(System.Globalization.CultureInfo.InvariantCulture,
                    "M 155,7 A 148,148 0 {0} 1 {1:F2},{2:F2}", isLargeArc, endX, endY);
            }
        }

        public double TargetDotX
        {
            get => _targetDotX;
            private set
            {
                if (Math.Abs(_targetDotX - value) > 0.1)
                {
                    _targetDotX = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(TargetDotCanvasLeft));
                }
            }
        }

        public double TargetDotY
        {
            get => _targetDotY;
            private set
            {
                if (Math.Abs(_targetDotY - value) > 0.1)
                {
                    _targetDotY = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(TargetDotCanvasTop));
                }
            }
        }

        public double TargetDotCanvasLeft => TargetDotX - 5.0;
        public double TargetDotCanvasTop => TargetDotY - 5.0;

        public bool IsTargetDotVisible
        {
            get => _isTargetDotVisible;
            private set
            {
                if (_isTargetDotVisible != value)
                {
                    _isTargetDotVisible = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsPoseFlashActive
        {
            get => _isPoseFlashActive;
            set
            {
                if (_isPoseFlashActive != value)
                {
                    _isPoseFlashActive = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsPoseCheckActive
        {
            get => _isPoseCheckActive;
            set
            {
                if (_isPoseCheckActive != value)
                {
                    _isPoseCheckActive = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsEnrollmentCompleteRing
        {
            get => _isEnrollmentCompleteRing;
            set
            {
                if (_isEnrollmentCompleteRing != value)
                {
                    _isEnrollmentCompleteRing = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(SampleProgressRingData));
                }
            }
        }

        public bool HasLandmarks
        {
            get => _hasLandmarks;
            private set
            {
                if (_hasLandmarks != value)
                {
                    _hasLandmarks = value;
                    OnPropertyChanged();
                }
            }
        }

        public string BiometricMeshPathData
        {
            get => _biometricMeshPathData;
            private set
            {
                if (_biometricMeshPathData != value)
                {
                    _biometricMeshPathData = value;
                    OnPropertyChanged();
                }
            }
        }

        public double LeftEyeCanvasLeft => Math.Max(0, _leftEyeX - 2.5);
        public double LeftEyeCanvasTop => Math.Max(0, _leftEyeY - 2.5);
        public double RightEyeCanvasLeft => Math.Max(0, _rightEyeX - 2.5);
        public double RightEyeCanvasTop => Math.Max(0, _rightEyeY - 2.5);
        public double NoseCanvasLeft => Math.Max(0, _noseX - 2.5);
        public double NoseCanvasTop => Math.Max(0, _noseY - 2.5);
        public double LeftMouthCanvasLeft => Math.Max(0, _leftMouthX - 2.5);
        public double LeftMouthCanvasTop => Math.Max(0, _leftMouthY - 2.5);
        public double RightMouthCanvasLeft => Math.Max(0, _rightMouthX - 2.5);
        public double RightMouthCanvasTop => Math.Max(0, _rightMouthY - 2.5);

        public string TargetPoseKey
        {
            get => _targetPoseKey;
            set
            {
                if (_targetPoseKey != value)
                {
                    _targetPoseKey = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(IsCenterTarget));
                    OnPropertyChanged(nameof(IsDirectionalTarget));
                    OnPropertyChanged(nameof(CurrentPoseIconData));
                }
            }
        }

        public bool IsCenterTarget => string.Equals(TargetPoseKey, EnrollmentProtocol.PoseCenter, StringComparison.OrdinalIgnoreCase);
        public bool IsDirectionalTarget => !IsCenterTarget && HasOverlay;

        public double TargetAngleDegrees
        {
            get => _targetAngleDegrees;
            private set
            {
                if (Math.Abs(_targetAngleDegrees - value) > 0.5)
                {
                    _targetAngleDegrees = value;
                    OnPropertyChanged();
                }
            }
        }

        public double TargetBeaconX
        {
            get => _targetBeaconX;
            private set
            {
                if (Math.Abs(_targetBeaconX - value) > 0.1)
                {
                    _targetBeaconX = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(TargetBeaconCanvasLeft));
                }
            }
        }

        public double TargetBeaconY
        {
            get => _targetBeaconY;
            private set
            {
                if (Math.Abs(_targetBeaconY - value) > 0.1)
                {
                    _targetBeaconY = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(TargetBeaconCanvasTop));
                }
            }
        }

        public double TargetBeaconCanvasLeft => TargetBeaconX - 10.0;
        public double TargetBeaconCanvasTop => TargetBeaconY - 10.0;

        public double CenterReticleX
        {
            get => _centerReticleX;
            private set
            {
                if (Math.Abs(_centerReticleX - value) > 0.1)
                {
                    _centerReticleX = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(CenterReticleCanvasLeft));
                }
            }
        }

        public double CenterReticleY
        {
            get => _centerReticleY;
            private set
            {
                if (Math.Abs(_centerReticleY - value) > 0.1)
                {
                    _centerReticleY = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(CenterReticleCanvasTop));
                }
            }
        }

        public double CenterReticleCanvasLeft => CenterReticleX - 35.0;
        public double CenterReticleCanvasTop => CenterReticleY - 35.0;
        public bool IsCenterFocusAligned => IsCenterTarget && IsPoseMatched;

        public int SamplesCollected
        {
            get => _samplesCollected;
            private set
            {
                if (_samplesCollected != value)
                {
                    _samplesCollected = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(IsSample1Captured));
                    OnPropertyChanged(nameof(IsSample2Captured));
                }
            }
        }

        public int SamplesTarget
        {
            get => _samplesTarget;
            private set
            {
                if (_samplesTarget != value)
                {
                    _samplesTarget = value;
                    OnPropertyChanged();
                }
            }
        }

        public bool IsSample1Captured => SamplesCollected >= 1;
        public bool IsSample2Captured => SamplesCollected >= 2;
        public bool SampleBeadsVisible => IsEnrolling && !IsEnrollmentCompleteRing;

        public string CurrentPoseIconData => EnrollmentProtocol.GetPoseIconData(TargetPoseKey);
        public string PoseStatusBadgeText => IsPoseMatched && IsQualityPassed ? "✓ Aligned" : (!IsQualityPassed ? "Adjusting" : "Aligning");

        public bool IsReducedMotion
        {
            get
            {
                try
                {
                    return !SystemParameters.ClientAreaAnimation;
                }
                catch
                {
                    return false;
                }
            }
        }

        public int PoseCompleteDelayMs { get; set; } = 250;
        public int EnrollmentCompleteDelayMs { get; set; } = 600;

        public bool IsDetailsExpanded
        {
            get => _isDetailsExpanded;
            set
            {
                if (_isDetailsExpanded != value)
                {
                    _isDetailsExpanded = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(DetailsToggleText));
                }
            }
        }

        public string RawTechnicalDetails
        {
            get => _rawTechnicalDetails;
            set
            {
                if (_rawTechnicalDetails != value)
                {
                    _rawTechnicalDetails = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(HasTechnicalDetails));
                }
            }
        }

        public bool HasTechnicalDetails => !string.IsNullOrWhiteSpace(RawTechnicalDetails);
        public string DetailsToggleText => IsDetailsExpanded ? "Hide Details" : "Show Details";

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

        public string UnlockBadgeText => IsPasswordLinked ? "Password Linked" : "Password Required";
        public string UnlockBadgeColor => IsPasswordLinked ? "#10B981" : "#F59E0B";

        // Commands
        public ICommand BeginEnrollmentCommand { get; }
        public ICommand CancelEnrollmentCommand { get; }
        public ICommand RetryConnectionCommand { get; }
        public ICommand RetryEnrollmentCommand { get; }
        public ICommand BackToWelcomeCommand { get; }
        public ICommand FinishCommand { get; }
        public ICommand ReEnrollCommand { get; }
        public ICommand GoToPasswordLinkCommand { get; }
        public ICommand LinkPasswordElevatedCommand { get; }
        public ICommand ExitCommand { get; }
        public ICommand ToggleDetailsCommand { get; }

        public MainViewModel()
            : this(null, null, null)
        {
        }

        public MainViewModel(
            IEnrollmentPipeClient? pipeClient = null,
            IPasswordLinker? passwordLinker = null,
            IPasswordLinkLauncher? linkLauncher = null)
        {
            _pipeClient = pipeClient ?? new EnrollmentPipeClient();
            _passwordLinker = passwordLinker ?? new PasswordLinker();
            _linkLauncher = linkLauncher ?? new PasswordLinkLauncher();

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
            FinishCommand = new RelayCommand(FinishSetup);
            ReEnrollCommand = new AsyncRelayCommand(StartEnrollmentFlowAsync);
            GoToPasswordLinkCommand = new RelayCommand(GoToPasswordLink);
            LinkPasswordElevatedCommand = new AsyncRelayCommand(LinkPasswordElevatedAsync, () => CanLinkPassword);
            ExitCommand = new RelayCommand(FinishSetup);
            ToggleDetailsCommand = new RelayCommand(() => IsDetailsExpanded = !IsDetailsExpanded);
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
            TargetPoseKey = EnrollmentProtocol.PoseCenter;
            SamplesCollected = 0;
        }

        public async Task StartEnrollmentFlowAsync()
        {
            bool isReEnrollment = CurrentStep == SetupStep.AlreadyEnrolled || !string.IsNullOrEmpty(ExistingProfileName);
            CurrentStep = SetupStep.Enrollment;
            EnrollmentMode = EnrollmentViewMode.Connecting;
            ResetPoseStates();
            PreviewImage = null;
            EngineErrorMessage = string.Empty;

            try
            {
                await _pipeClient.ConnectAsync();
                EnrollmentMode = EnrollmentViewMode.Enrolling;
                await _pipeClient.StartEnrollmentAsync(DisplayName, confirmed: isReEnrollment);
            }
            catch (EngineUnavailableException ex)
            {
                EnrollmentMode = EnrollmentViewMode.EngineUnavailable;
                EngineErrorMessage = ex.Message;
                RawTechnicalDetails = ex.ToString();
            }
            catch (Exception ex)
            {
                EnrollmentMode = EnrollmentViewMode.Failed;
                EngineErrorMessage = $"Could not initiate face enrollment: {ex.Message}";
                RawTechnicalDetails = ex.ToString();
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
                ClearOverlay();
                IsEnrollmentCompleteRing = false;
                IsPoseFlashActive = false;
                IsPoseCheckActive = false;
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

        public async Task LinkPasswordElevatedAsync()
        {
            IsVerifyingPassword = true;
            HasPasswordError = false;
            PasswordErrorMessage = string.Empty;

            try
            {
                string username = Environment.UserDomainName + "\\" + Environment.UserName;
                string? sid = System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value;
                if (string.IsNullOrEmpty(sid))
                {
                    HasPasswordError = true;
                    PasswordErrorMessage = "Could not resolve current Windows user account SID.";
                    return;
                }

                string profileId = !string.IsNullOrEmpty(EnrolledProfileId) ? EnrolledProfileId : ExistingProfileId;
                if (string.IsNullOrEmpty(profileId))
                {
                    HasPasswordError = true;
                    PasswordErrorMessage = "No enrolled face profile is active.";
                    return;
                }

                int exitCode = await _linkLauncher.LaunchLinkPasswordAsync(username, sid, profileId);
                HandleLinkExitCode(exitCode);
            }
            catch (Exception ex)
            {
                HasPasswordError = true;
                PasswordErrorMessage = $"Failed to launch elevated linking: {ex.Message}";
            }
            finally
            {
                IsVerifyingPassword = false;
            }
        }

        public void HandleLinkExitCode(int exitCode)
        {
            switch (exitCode)
            {
                case 0:
                    IsPasswordLinked = true;
                    HasPasswordError = false;
                    PasswordErrorMessage = string.Empty;
                    if (CurrentStep == SetupStep.PasswordLink)
                    {
                        CurrentStep = SetupStep.Done;
                    }
                    break;

                case 1:
                    HasPasswordError = true;
                    PasswordErrorMessage = "Password linking was cancelled. Windows password linking is required to complete setup.";
                    break;

                case 2:
                    HasPasswordError = true;
                    PasswordErrorMessage = "Incorrect Windows password. Maximum attempts reached.";
                    break;

                case 3:
                    HasPasswordError = true;
                    PasswordErrorMessage = "An unexpected error occurred while securing the credential in Windows LSA.";
                    break;

                case 4:
                    HasPasswordError = true;
                    PasswordErrorMessage = "Administrator permission (UAC) was declined. Windows LSA requires elevation to link password.";
                    break;

                default:
                    HasPasswordError = true;
                    PasswordErrorMessage = $"Password linking exited with code {exitCode}.";
                    break;
            }
        }

        public void FinishSetup()
        {
            Application.Current?.Shutdown();
        }

        public void GoToPasswordLink()
        {
            if (!string.IsNullOrEmpty(ExistingProfileId))
            {
                EnrolledProfileId = ExistingProfileId;
                EnrolledDisplayName = ExistingProfileName;
            }
            CurrentStep = SetupStep.PasswordLink;
        }

        public async Task CheckExistingProfileAsync(CancellationToken ct = default)
        {
            RunOnUi(() => IsCheckingStatus = true);
            try
            {
                if (!_pipeClient.IsConnected)
                {
                    await _pipeClient.ConnectAsync(timeoutMs: 1500, ct: ct).ConfigureAwait(false);
                }

                var status = await _pipeClient.RequestStatusAsync(timeoutMs: 1500, ct: ct).ConfigureAwait(false);
                if (status != null && status.HasEnrolledProfile)
                {
                    string name = !string.IsNullOrWhiteSpace(status.DisplayName) ? status.DisplayName : "Primary User";
                    string id = status.ProfileId;
                    RunOnUi(() =>
                    {
                        ExistingProfileName = name;
                        ExistingProfileId = id;
                        DisplayName = name;
                        EnrolledDisplayName = name;
                        EnrolledProfileId = id;
                        CurrentStep = SetupStep.AlreadyEnrolled;
                    });
                }
                else
                {
                    RunOnUi(() => CurrentStep = SetupStep.Welcome);
                }
            }
            catch (Exception)
            {
                RunOnUi(() => CurrentStep = SetupStep.Welcome);
            }
            finally
            {
                try
                {
                    if (CurrentStep != SetupStep.Enrollment)
                    {
                        await _pipeClient.DisconnectAsync().ConfigureAwait(false);
                    }
                }
                catch
                {
                    // Ignore disconnect error on startup check
                }
                RunOnUi(() => IsCheckingStatus = false);
            }
        }

        public void ApplyOverlay(EnrollmentOverlayData overlay)
        {
            HasOverlay = true;
            IsPoseMatched = overlay.PoseMatched;
            IsBracketGreen = overlay.PoseMatched && overlay.QualityPassed;

            if (overlay.Face != null && overlay.Face.Found && overlay.Face.Bbox != null && overlay.Face.Bbox.Length >= 4)
            {
                IsFaceDetected = true;
                double bx1 = overlay.Face.Bbox[0];
                double by1 = overlay.Face.Bbox[1];
                double bx2 = overlay.Face.Bbox[2];
                double by2 = overlay.Face.Bbox[3];

                double left = Math.Clamp(bx1 * 296.0, 0, 296.0);
                double top = Math.Clamp(by1 * 296.0, 0, 296.0);
                double right = Math.Clamp(bx2 * 296.0, left, 296.0);
                double bottom = Math.Clamp(by2 * 296.0, top, 296.0);

                FaceBboxLeft = Math.Round(left, 1);
                FaceBboxTop = Math.Round(top, 1);
                FaceBboxWidth = Math.Round(right - left, 1);
                FaceBboxHeight = Math.Round(bottom - top, 1);

                if (overlay.Face.Landmarks != null && overlay.Face.Landmarks.Length >= 5)
                {
                    HasLandmarks = true;
                    _leftEyeX = Math.Clamp(overlay.Face.Landmarks[0][0] * 296.0, 0, 296.0);
                    _leftEyeY = Math.Clamp(overlay.Face.Landmarks[0][1] * 296.0, 0, 296.0);
                    _rightEyeX = Math.Clamp(overlay.Face.Landmarks[1][0] * 296.0, 0, 296.0);
                    _rightEyeY = Math.Clamp(overlay.Face.Landmarks[1][1] * 296.0, 0, 296.0);
                    _noseX = Math.Clamp(overlay.Face.Landmarks[2][0] * 296.0, 0, 296.0);
                    _noseY = Math.Clamp(overlay.Face.Landmarks[2][1] * 296.0, 0, 296.0);
                    _leftMouthX = Math.Clamp(overlay.Face.Landmarks[3][0] * 296.0, 0, 296.0);
                    _leftMouthY = Math.Clamp(overlay.Face.Landmarks[3][1] * 296.0, 0, 296.0);
                    _rightMouthX = Math.Clamp(overlay.Face.Landmarks[4][0] * 296.0, 0, 296.0);
                    _rightMouthY = Math.Clamp(overlay.Face.Landmarks[4][1] * 296.0, 0, 296.0);

                    OnPropertyChanged(nameof(LeftEyeCanvasLeft));
                    OnPropertyChanged(nameof(LeftEyeCanvasTop));
                    OnPropertyChanged(nameof(RightEyeCanvasLeft));
                    OnPropertyChanged(nameof(RightEyeCanvasTop));
                    OnPropertyChanged(nameof(NoseCanvasLeft));
                    OnPropertyChanged(nameof(NoseCanvasTop));
                    OnPropertyChanged(nameof(LeftMouthCanvasLeft));
                    OnPropertyChanged(nameof(LeftMouthCanvasTop));
                    OnPropertyChanged(nameof(RightMouthCanvasLeft));
                    OnPropertyChanged(nameof(RightMouthCanvasTop));

                    BiometricMeshPathData = string.Format(System.Globalization.CultureInfo.InvariantCulture,
                        "M {0:F1},{1:F1} L {2:F1},{3:F1} M {0:F1},{1:F1} L {4:F1},{5:F1} L {2:F1},{3:F1} M {4:F1},{5:F1} L {6:F1},{7:F1} L {8:F1},{9:F1} L {4:F1},{5:F1}",
                        _leftEyeX, _leftEyeY, _rightEyeX, _rightEyeY, _noseX, _noseY, _leftMouthX, _leftMouthY, _rightMouthX, _rightMouthY);
                }
                else
                {
                    HasLandmarks = false;
                    BiometricMeshPathData = string.Empty;
                }
            }
            else
            {
                IsFaceDetected = false;
                HasLandmarks = false;
                BiometricMeshPathData = string.Empty;
            }

            if (overlay.Samples != null && overlay.Samples.Target > 0)
            {
                SamplesCollected = overlay.Samples.Collected;
                SamplesTarget = overlay.Samples.Target;
                SampleProgress = Math.Clamp((double)overlay.Samples.Collected / overlay.Samples.Target, 0.0, 1.0);
            }
            else
            {
                SamplesCollected = 0;
                SampleProgress = 0.0;
            }

            if (overlay.Target != null)
            {
                if (!string.IsNullOrEmpty(overlay.Target.Pose))
                {
                    TargetPoseKey = overlay.Target.Pose;
                }

                double targetYaw = overlay.Target.Yaw;
                double targetPitch = overlay.Target.Pitch;
                double uy = overlay.Face != null ? overlay.Face.Yaw : 0.0;
                double up = overlay.Face != null ? overlay.Face.Pitch : 0.0;

                bool isCenter = (targetYaw == 0.0 && targetPitch == 0.0) || string.Equals(TargetPoseKey, EnrollmentProtocol.PoseCenter, StringComparison.OrdinalIgnoreCase);

                if (isCenter)
                {
                    TargetAngleDegrees = 0.0;
                    TargetBeaconX = 155.0;
                    TargetBeaconY = 155.0;

                    if (overlay.PoseMatched)
                    {
                        CenterReticleX = 155.0;
                        CenterReticleY = 155.0;
                        TargetDotX = 155.0;
                        TargetDotY = 155.0;
                    }
                    else
                    {
                        CenterReticleX = Math.Clamp(155.0 + Math.Clamp(uy / 0.30, -1.0, 1.0) * 40.0, 115.0, 195.0);
                        CenterReticleY = Math.Clamp(155.0 + Math.Clamp(up / 0.20, -1.0, 1.0) * 40.0, 115.0, 195.0);
                        TargetDotX = CenterReticleX;
                        TargetDotY = CenterReticleY;
                    }
                }
                else
                {
                    double angle = Math.Atan2(targetPitch, targetYaw);
                    TargetAngleDegrees = angle * 180.0 / Math.PI;

                    double ringX = 155.0 + 148.0 * Math.Cos(angle);
                    double ringY = 155.0 + 148.0 * Math.Sin(angle);
                    TargetBeaconX = ringX;
                    TargetBeaconY = ringY;

                    if (overlay.PoseMatched)
                    {
                        TargetDotX = ringX;
                        TargetDotY = ringY;
                    }
                    else
                    {
                        double userYawOffset = (targetYaw - uy);
                        double userPitchOffset = (targetPitch - up);
                        TargetDotX = Math.Clamp(ringX + userYawOffset * 15.0, 15.0, 295.0);
                        TargetDotY = Math.Clamp(ringY + userPitchOffset * 15.0, 15.0, 295.0);
                    }
                }

                IsTargetDotVisible = IsEnrolling && !IsEnrollmentCompleteRing;
                OnPropertyChanged(nameof(IsCenterFocusAligned));
                OnPropertyChanged(nameof(IsDirectionalTarget));
                OnPropertyChanged(nameof(PoseStatusBadgeText));
                OnPropertyChanged(nameof(SampleBeadsVisible));
            }
            else
            {
                IsTargetDotVisible = false;
            }
        }

        public void ClearOverlay()
        {
            HasOverlay = false;
            IsFaceDetected = false;
            HasLandmarks = false;
            BiometricMeshPathData = string.Empty;
            IsTargetDotVisible = false;
            SampleProgress = 0.0;
            SamplesCollected = 0;
            FaceBboxLeft = 0;
            FaceBboxTop = 0;
            FaceBboxWidth = 0;
            FaceBboxHeight = 0;
            TargetBeaconX = 155.0;
            TargetBeaconY = 155.0;
            CenterReticleX = 155.0;
            CenterReticleY = 155.0;
            OnPropertyChanged(nameof(SampleBeadsVisible));
            OnPropertyChanged(nameof(IsDirectionalTarget));
            OnPropertyChanged(nameof(IsCenterFocusAligned));
        }

        // IPC Event Handlers
        private void OnPreviewFrameReceived(object? sender, PreviewFrameEventArgs e)
        {
            RunOnUi(() =>
            {
                PreviewImage = e.PreviewImage;
                if (!string.IsNullOrEmpty(e.TargetPose))
                {
                    TargetPoseKey = e.TargetPose;
                }
                GuidanceText = string.IsNullOrWhiteSpace(e.GuidanceText)
                    ? EnrollmentProtocol.GetPoseFriendlyName(e.TargetPose)
                    : e.GuidanceText;
                TargetPoseName = EnrollmentProtocol.GetPoseFriendlyName(e.TargetPose);
                ProgressPercentage = e.ProgressPercentage;

                IsQualityPassed = string.Equals(e.QualityState, "PASSED", StringComparison.OrdinalIgnoreCase);
                QualityStatusText = FormatQualityText(e.QualityState);

                if (e.Overlay != null)
                {
                    ApplyOverlay(e.Overlay);
                }
                else
                {
                    ClearOverlay();
                }
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

        private async void OnPoseCompleted(object? sender, PoseCompletedEventArgs e)
        {
            int completedIdx = e.PoseIndex;

            RunOnUi(() =>
            {
                IsPoseFlashActive = true;
                IsPoseCheckActive = true;
            });

            if (!IsReducedMotion && PoseCompleteDelayMs > 0)
            {
                await Task.Delay(PoseCompleteDelayMs).ConfigureAwait(false);
            }

            RunOnUi(() =>
            {
                IsPoseFlashActive = false;
                IsPoseCheckActive = false;

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

        private async void OnEnrollmentCompleted(object? sender, EnrollmentCompletedEventArgs e)
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
                IsEnrollmentCompleteRing = true;
                IsPoseCheckActive = true;
            });

            if (!IsReducedMotion && EnrollmentCompleteDelayMs > 0)
            {
                await Task.Delay(EnrollmentCompleteDelayMs).ConfigureAwait(false);
            }

            RunOnUi(() =>
            {
                IsPoseCheckActive = false;
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
                RawTechnicalDetails = $"Reason Code: {e.ReasonCode}\nDetail: {e.Detail}";
            });
        }

        private void OnErrorOccurred(object? sender, EnrollmentErrorEventArgs e)
        {
            if (e.ReasonCode == EnrollmentProtocol.ReasonConfirmationRequired)
            {
                // Auto-confirm re-enrollment when user initiated enrollment from setup UI
                _ = _pipeClient.StartEnrollmentAsync(DisplayName, confirmed: true);
                return;
            }

            RunOnUi(() =>
            {
                RawTechnicalDetails = $"Error Code: {e.ReasonCode}\nDetail: {e.Detail}";
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
                app.Dispatcher.Invoke(action);
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
