using System;
using System.Security;
using System.Threading;
using System.Threading.Tasks;
using PeekSetup.Models;
using PeekSetup.Services;
using PeekSetup.ViewModels;
using Xunit;

namespace PeekSetup.Tests
{
    public class MockEnrollmentPipeClient : IEnrollmentPipeClient
    {
        public bool IsConnected { get; set; }
        public bool ThrowOnConnect { get; set; }
        public bool ThrowGenericOnConnect { get; set; }
        public StatusResponseData? MockStatusResponse { get; set; }
        public bool ThrowOnStatusRequest { get; set; }
        public bool StatusRequestCalled { get; private set; }
        public string? LastStartedDisplayName { get; private set; }
        public bool LastStartedConfirmed { get; private set; }
        public string? LastStartedPassword { get; private set; }
        public bool CancelCalled { get; private set; }
        public bool DisconnectCalled { get; private set; }

        public event EventHandler<PreviewFrameEventArgs>? PreviewFrameReceived;
        public event EventHandler<PoseCompletedEventArgs>? PoseCompleted;
        public event EventHandler<EnrollmentCompletedEventArgs>? EnrollmentCompleted;
        public event EventHandler<EnrollmentFailedEventArgs>? EnrollmentFailed;
        public event EventHandler<EnrollmentErrorEventArgs>? ErrorOccurred;
        public event EventHandler? ReadyReceived;
        public event EventHandler? Disconnected;

        public Task ConnectAsync(string pipeName = "PeekEnrollment", int timeoutMs = 4000, CancellationToken ct = default)
        {
            if (ThrowOnConnect)
            {
                throw new EngineUnavailableException("Peek engine isn't running — start it and try again.");
            }
            if (ThrowGenericOnConnect)
            {
                throw new InvalidOperationException("Failed to open pipe handle");
            }
            IsConnected = true;
            return Task.CompletedTask;
        }

        public Task<StatusResponseData?> RequestStatusAsync(int timeoutMs = 2000, CancellationToken ct = default)
        {
            StatusRequestCalled = true;
            if (ThrowOnStatusRequest)
            {
                throw new EngineUnavailableException("Peek engine isn't running — start it and try again.");
            }
            return Task.FromResult(MockStatusResponse);
        }

        public Task StartEnrollmentAsync(string displayName, bool confirmed = false, string? password = null, CancellationToken ct = default)
        {
            LastStartedDisplayName = displayName;
            LastStartedConfirmed = confirmed;
            LastStartedPassword = password;
            return Task.CompletedTask;
        }

        public Task CancelEnrollmentAsync(string reason = "USER_CANCELLED", CancellationToken ct = default)
        {
            CancelCalled = true;
            return Task.CompletedTask;
        }

        public Task DisconnectAsync()
        {
            DisconnectCalled = true;
            IsConnected = false;
            return Task.CompletedTask;
        }

        public ValueTask DisposeAsync()
        {
            return ValueTask.CompletedTask;
        }

        public void TriggerPreviewFrame(string targetPose, string qualityState, double progress, string guidance)
        {
            var r = new System.Windows.Media.Imaging.RenderTargetBitmap(1, 1, 96, 96, System.Windows.Media.PixelFormats.Pbgra32);
            PreviewFrameReceived?.Invoke(this, new PreviewFrameEventArgs(r, targetPose, qualityState, progress, guidance, 1.0));
        }

        public void TriggerPoseCompleted(string pose, int index, int total = 9)
        {
            PoseCompleted?.Invoke(this, new PoseCompletedEventArgs(pose, index, total, "Pose completed"));
        }

        public void TriggerReady()
        {
            ReadyReceived?.Invoke(this, EventArgs.Empty);
        }

        public void TriggerDisconnected()
        {
            Disconnected?.Invoke(this, EventArgs.Empty);
        }

        public void TriggerEnrollmentCompleted(string profileId, string displayName)
        {
            EnrollmentCompleted?.Invoke(this, new EnrollmentCompletedEventArgs(profileId, displayName, "Profile saved"));
        }

        public void TriggerEnrollmentFailed(string reasonCode, string detail)
        {
            EnrollmentFailed?.Invoke(this, new EnrollmentFailedEventArgs(reasonCode, detail));
        }

        public void TriggerError(string reasonCode, string detail)
        {
            ErrorOccurred?.Invoke(this, new EnrollmentErrorEventArgs(reasonCode, detail));
        }
    }

    public class MockPasswordLinker : IPasswordLinker
    {
        public bool ShouldLinkSucceed { get; set; } = true;
        public string? LastProfileId { get; private set; }
        public int CallCount { get; private set; }

        public bool LinkPassword(string profileId, SecureString password, string? username = null, string? sid = null)
        {
            CallCount++;
            LastProfileId = profileId;
            return ShouldLinkSucceed;
        }

        public bool UnlinkPassword(string sid)
        {
            return true;
        }
    }

    public class MainViewModelTests
    {
        [Fact]
        public void InitialState_IsWelcomeStepWithPrefilledDisplayName()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockLinker = new MockPasswordLinker();
            var vm = new MainViewModel(mockPipe, mockLinker);

            Assert.Equal(SetupStep.Welcome, vm.CurrentStep);
            Assert.True(vm.IsWelcomeStep);
            Assert.False(string.IsNullOrWhiteSpace(vm.DisplayName));
            Assert.True(vm.CanBeginEnrollment);
            Assert.Equal(9, vm.Poses.Count);
        }

        [Theory]
        [InlineData("")]
        [InlineData("   ")]
        public void BlankDisplayName_DisablesBeginEnrollment(string blankName)
        {
            var vm = new MainViewModel(new MockEnrollmentPipeClient(), new MockPasswordLinker());
            vm.DisplayName = blankName;

            Assert.False(vm.CanBeginEnrollment);
            Assert.False(vm.BeginEnrollmentCommand.CanExecute(null));
        }

        [Fact]
        public async Task StartEnrollment_WhenEngineUnavailable_ShowsActionableErrorMessage()
        {
            var mockPipe = new MockEnrollmentPipeClient { ThrowOnConnect = true };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();

            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);
            Assert.Equal(EnrollmentViewMode.EngineUnavailable, vm.EnrollmentMode);
            Assert.True(vm.IsEngineUnavailable);
            Assert.Contains("Peek engine isn't running", vm.EngineErrorMessage);
        }

        [Fact]
        public async Task StartEnrollment_WhenEngineReady_TransitionsToEnrolling()
        {
            var mockPipe = new MockEnrollmentPipeClient { ThrowOnConnect = false };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());
            vm.DisplayName = "Test Alice";

            await vm.StartEnrollmentFlowAsync();

            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);
            Assert.Equal(EnrollmentViewMode.Enrolling, vm.EnrollmentMode);
            Assert.True(vm.IsEnrolling);
            Assert.Equal("Test Alice", mockPipe.LastStartedDisplayName);
        }

        [Fact]
        public async Task CancelEnrollment_InvokesCancelOnPipeAndReturnsToWelcome()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();
            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);

            await vm.CancelEnrollmentFlowAsync();

            Assert.True(mockPipe.CancelCalled);
            Assert.Equal(SetupStep.Welcome, vm.CurrentStep);
        }

        [Fact]
        public async Task EnrollmentCompleted_AdvancesToPasswordLinkStep()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();
            mockPipe.TriggerEnrollmentCompleted("prof_abc123", "Alice");

            Assert.Equal(SetupStep.PasswordLink, vm.CurrentStep);
            Assert.True(vm.IsPasswordLinkStep);
            Assert.Equal("prof_abc123", vm.EnrolledProfileId);
            Assert.Equal("Alice", vm.EnrolledDisplayName);
        }

        [Fact]
        public void PasswordLinkStep_IsMandatory_CannotAdvanceWithoutLinking()
        {
            var vm = new MainViewModel(new MockEnrollmentPipeClient(), new MockPasswordLinker());
            vm.CurrentStep = SetupStep.PasswordLink;
            vm.EnrolledProfileId = "p_mandatory";

            // Simulating user cancelling elevated password linking (exit code 1)
            vm.HandleLinkExitCode(1);

            // Must remain on PasswordLink step and cannot advance to Done
            Assert.Equal(SetupStep.PasswordLink, vm.CurrentStep);
            Assert.True(vm.IsPasswordLinkStep);
            Assert.False(vm.IsDoneStep);
            Assert.False(vm.IsPasswordLinked);
            Assert.True(vm.HasPasswordError);
            Assert.Contains("cancelled", vm.PasswordErrorMessage, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("required", vm.PasswordErrorMessage, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public async Task LinkPassword_OnSuccess_AdvancesToDoneWithPasswordLinked()
        {
            var mockLinker = new MockPasswordLinker { ShouldLinkSucceed = true };
            var vm = new MainViewModel(new MockEnrollmentPipeClient(), mockLinker);
            vm.CurrentStep = SetupStep.PasswordLink;
            vm.EnrolledProfileId = "p_link";

            using var pass = new SecureString();
            pass.AppendChar('x');
            pass.MakeReadOnly();

            await vm.LinkPasswordAsync(pass);

            Assert.Equal(1, mockLinker.CallCount);
            Assert.Equal("p_link", mockLinker.LastProfileId);
            Assert.Equal(SetupStep.Done, vm.CurrentStep);
            Assert.True(vm.IsDoneStep);
            Assert.True(vm.IsPasswordLinked);
            Assert.Equal("Password Linked", vm.UnlockBadgeText);
            Assert.False(vm.HasPasswordError);
        }

        [Fact]
        public async Task LinkPassword_OnFailure_ShowsErrorAndNeverAdvances()
        {
            var mockLinker = new MockPasswordLinker { ShouldLinkSucceed = false };
            var vm = new MainViewModel(new MockEnrollmentPipeClient(), mockLinker);
            vm.CurrentStep = SetupStep.PasswordLink;
            vm.EnrolledProfileId = "p_fail";

            using var pass = new SecureString();
            pass.AppendChar('x');
            pass.MakeReadOnly();

            await vm.LinkPasswordAsync(pass);

            Assert.Equal(1, mockLinker.CallCount);
            Assert.Equal(SetupStep.PasswordLink, vm.CurrentStep); // Did not silently advance!
            Assert.False(vm.IsPasswordLinked);
            Assert.True(vm.HasPasswordError);
            Assert.Contains("not accepted", vm.PasswordErrorMessage);
        }

        [Fact]
        public async Task PreviewFrameReceived_UpdatesGuidanceAndProgress()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();
            mockPipe.TriggerPreviewFrame("LEFT", "PASSED", 33.3, "Turn Head Left Slowly");

            Assert.Equal("Turn Head Left Slowly", vm.GuidanceText);
            Assert.Equal("Turn Head Left", vm.TargetPoseName);
            Assert.Equal(33.3, vm.ProgressPercentage);
            Assert.True(vm.IsQualityPassed);
            Assert.NotNull(vm.PreviewImage);
        }

        [Fact]
        public async Task PoseCompleted_AdvancesActivePoseIndex()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();
            mockPipe.TriggerPoseCompleted("CENTER", 0);

            Assert.True(vm.Poses[0].IsCompleted);
            Assert.True(vm.Poses[1].IsActive);
            Assert.Equal(1, vm.CurrentPoseIndex);
        }

        [Fact]
        public async Task EnrollmentFailed_SetsFailedModeWithDetail()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();
            mockPipe.TriggerEnrollmentFailed("TIMEOUT", "Session timed out");

            Assert.Equal(EnrollmentViewMode.Failed, vm.EnrollmentMode);
            Assert.True(vm.IsEnrollmentFailed);
            Assert.Contains("timed out", vm.EngineErrorMessage);
        }

        [Fact]
        public async Task ErrorOccurred_CameraUnavailable_SetsActionableErrorMessage()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();
            mockPipe.TriggerError("CAMERA_UNAVAILABLE", "Camera device busy");

            Assert.Equal(EnrollmentViewMode.Failed, vm.EnrollmentMode);
            Assert.Contains("Webcam unavailable", vm.EngineErrorMessage);
        }

        [Fact]
        public async Task Disconnected_DuringEnrollment_SetsEngineUnavailable()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.StartEnrollmentFlowAsync();
            Assert.Equal(EnrollmentViewMode.Enrolling, vm.EnrollmentMode);

            mockPipe.TriggerDisconnected();

            Assert.Equal(EnrollmentViewMode.EngineUnavailable, vm.EnrollmentMode);
            Assert.Contains("lost", vm.EngineErrorMessage);
        }

        [Fact]
        public async Task CheckExistingProfile_WhenProfileExists_ShowsAlreadySetUpScreen()
        {
            var mockPipe = new MockEnrollmentPipeClient
            {
                MockStatusResponse = new StatusResponseData(true, "Alice Cooper", "profile_alice_123")
            };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.CheckExistingProfileAsync();

            Assert.True(mockPipe.StatusRequestCalled);
            Assert.Equal(SetupStep.AlreadyEnrolled, vm.CurrentStep);
            Assert.True(vm.IsAlreadyEnrolledStep);
            Assert.False(vm.IsWelcomeStep);
            Assert.Equal("Alice Cooper", vm.ExistingProfileName);
            Assert.Equal("profile_alice_123", vm.ExistingProfileId);
            Assert.Equal("You're already set up as Alice Cooper", vm.AlreadyEnrolledTitle);
        }

        [Fact]
        public async Task CheckExistingProfile_WhenNoProfileExists_FallsThroughToWelcome()
        {
            var mockPipe = new MockEnrollmentPipeClient
            {
                MockStatusResponse = new StatusResponseData(false, "", "")
            };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.CheckExistingProfileAsync();

            Assert.True(mockPipe.StatusRequestCalled);
            Assert.Equal(SetupStep.Welcome, vm.CurrentStep);
            Assert.True(vm.IsWelcomeStep);
            Assert.False(vm.IsAlreadyEnrolledStep);
        }

        [Fact]
        public async Task CheckExistingProfile_WhenEngineUnreachableOnConnect_FallsThroughToWelcome()
        {
            var mockPipe = new MockEnrollmentPipeClient
            {
                ThrowOnConnect = true
            };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.CheckExistingProfileAsync();

            Assert.Equal(SetupStep.Welcome, vm.CurrentStep);
            Assert.True(vm.IsWelcomeStep);
            Assert.False(vm.IsAlreadyEnrolledStep);
        }

        [Fact]
        public async Task CheckExistingProfile_WhenEngineFailsOnStatusRequest_FallsThroughToWelcome()
        {
            var mockPipe = new MockEnrollmentPipeClient
            {
                ThrowOnStatusRequest = true
            };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.CheckExistingProfileAsync();

            Assert.Equal(SetupStep.Welcome, vm.CurrentStep);
            Assert.True(vm.IsWelcomeStep);
            Assert.False(vm.IsAlreadyEnrolledStep);
        }

        [Fact]
        public async Task AlreadyEnrolledScreen_ReEnrollCommand_TransitionsToEnrollmentFlow()
        {
            var mockPipe = new MockEnrollmentPipeClient
            {
                MockStatusResponse = new StatusResponseData(true, "Alice", "prof_123")
            };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.CheckExistingProfileAsync();
            Assert.Equal(SetupStep.AlreadyEnrolled, vm.CurrentStep);

            // Re-enroll replaces current profile using standard enrollment flow
            vm.ReEnrollCommand.Execute(null);

            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);
            Assert.True(vm.IsEnrollmentStep);
        }

        [Fact]
        public async Task AlreadyEnrolledScreen_GoToPasswordLinkCommand_TransitionsWithExistingProfile()
        {
            var mockPipe = new MockEnrollmentPipeClient
            {
                MockStatusResponse = new StatusResponseData(true, "Alice", "prof_123")
            };
            var vm = new MainViewModel(mockPipe, new MockPasswordLinker());

            await vm.CheckExistingProfileAsync();
            Assert.Equal(SetupStep.AlreadyEnrolled, vm.CurrentStep);

            // Link/replace password transitions to PasswordLink step with existing profile ID
            vm.GoToPasswordLinkCommand.Execute(null);

            Assert.Equal(SetupStep.PasswordLink, vm.CurrentStep);
            Assert.True(vm.IsPasswordLinkStep);
            Assert.Equal("prof_123", vm.EnrolledProfileId);
            Assert.Equal("Alice", vm.EnrolledDisplayName);
        }
    }
}
