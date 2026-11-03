using System;
using System.Collections.Generic;
using System.IO;
using System.Security;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using PeekSetup.Models;
using PeekSetup.Services;
using PeekSetup.ViewModels;
using Xunit;

namespace PeekSetup.Tests
{
    public class MockProfileManager : IProfileManager
    {
        public List<ProfileModel> Profiles { get; set; } = new();
        public bool ToggleCalled { get; private set; }
        public string? LastToggledProfileId { get; private set; }
        public bool LastToggledState { get; private set; }
        public bool DeleteCalled { get; private set; }
        public string? LastDeletedProfileId { get; private set; }
        public bool RenameCalled { get; private set; }
        public string? LastRenamedProfileId { get; private set; }
        public string? LastRenamedDisplayName { get; private set; }
        public bool SaveCalled { get; private set; }
        public string? LastSavedProfileId { get; private set; }
        public byte[]? LastSavedData { get; private set; }

        public Task<List<ProfileModel>> GetProfilesAsync()
        {
            return Task.FromResult(new List<ProfileModel>(Profiles));
        }

        public Task<bool> ToggleProfileAsync(string profileId, bool enabled)
        {
            ToggleCalled = true;
            LastToggledProfileId = profileId;
            LastToggledState = enabled;
            var p = Profiles.Find(x => x.ProfileId == profileId);
            if (p != null) p.Enabled = enabled;
            return Task.FromResult(true);
        }

        public Task<bool> DeleteProfileAsync(string profileId)
        {
            DeleteCalled = true;
            LastDeletedProfileId = profileId;
            Profiles.RemoveAll(x => x.ProfileId == profileId);
            return Task.FromResult(true);
        }

        public Task<bool> RenameProfileAsync(string profileId, string newDisplayName)
        {
            RenameCalled = true;
            LastRenamedProfileId = profileId;
            LastRenamedDisplayName = newDisplayName;
            var p = Profiles.Find(x => x.ProfileId == profileId);
            if (p != null) p.DisplayName = newDisplayName;
            return Task.FromResult(true);
        }

        public Task<bool> SaveProfileAsync(string profileId, byte[] encryptedData)
        {
            SaveCalled = true;
            LastSavedProfileId = profileId;
            LastSavedData = encryptedData;
            return Task.FromResult(true);
        }
    }

    public class MultiFaceProfileTests
    {
        [Fact]
        public void QualityTicks_AlwaysGeneratesNinePoseTicks_AndAppliesColorCoding()
        {
            // 9 pose qualities: 0.85 (green), 0.65 (amber), 0.40 (red), null (gray)
            var poseQualities = new List<double> { 0.85, 0.65, 0.40, 0.75, 0.55, 0.35, 0.90, 0.60 }; // 8 poses, 9th missing
            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, true, 63, poseQualities,
                (i, e) => Task.CompletedTask,
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            Assert.Equal(9, item.QualityBars.Count);
            Assert.Equal("Capture quality • 63%", item.CaptureQualityText);
            Assert.True(item.IsAuthorized);
            Assert.False(item.IsPending);

            // Tick 0: 0.85 >= 0.70 -> Green (#30D158)
            Assert.Same(IdentityItemViewModel.GreenBrush, item.QualityBars[0].FillBrush);
            // Tick 1: 0.65 -> Amber (#FF9500)
            Assert.Same(IdentityItemViewModel.AmberBrush, item.QualityBars[1].FillBrush);
            // Tick 2: 0.40 -> Red (#FF453A)
            Assert.Same(IdentityItemViewModel.RedBrush, item.QualityBars[2].FillBrush);
            // Tick 8 (index 8): missing -> Gray (#8E8E93)
            Assert.Same(IdentityItemViewModel.GrayBrush, item.QualityBars[8].FillBrush);
        }

        [Fact]
        public void QualityTicks_WhenQualityNull_ShowsQualityUnknownAndGrayTicks()
        {
            var item = new IdentityItemViewModel(
                "prof_unknown", "Unknown Person", true, false, null, null,
                (i, e) => Task.CompletedTask,
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            Assert.Equal("Quality unknown", item.CaptureQualityText);
            Assert.Equal(9, item.QualityBars.Count);
            Assert.False(item.IsAuthorized);
            Assert.True(item.IsPending);
            Assert.Equal("Not activated", item.StatusBadgeText);

            foreach (var bar in item.QualityBars)
            {
                Assert.Same(IdentityItemViewModel.GrayBrush, bar.FillBrush);
            }
        }

        [Fact]
        public void StartAddIdentity_DoesNotAskPreEnrollmentPassword()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);

            vm.StartAddIdentityCommand.Execute(null);

            Assert.True(vm.IsAddingNewIdentity);
            Assert.True(vm.IsPasswordVerifiedForAdd);
            Assert.Empty(vm.PasswordVerificationError);
            Assert.NotEmpty(vm.NewIdentityName);
        }

        [Fact]
        public async Task AddFlow_EnrollmentCompleted_TransitionsToConfirmIdentityAuthorize()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_1", DisplayName = "Primary", Enabled = true, QualityPercentage = 80 }
                }
            };
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles)
            {
                EnrollmentCompleteDelayMs = 0
            };
            await vm.RefreshIdentitiesAsync();

            vm.StartAddIdentityCommand.Execute(null);
            vm.NewIdentityName = "Secondary Identity";
            await ((AsyncRelayCommand)vm.ConfirmAddIdentityCommand).ExecuteAsync(null);

            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);

            // Simulate enrollment complete
            mockPipe.TriggerEnrollmentCompleted("prof_2", "Secondary Identity");
            await Task.Delay(30);

            // Should be on ConfirmIdentityAuthorize step (not AlreadyEnrolled and not PasswordLink)
            Assert.Equal(SetupStep.ConfirmIdentityAuthorize, vm.CurrentStep);
            Assert.True(vm.IsConfirmAuthorizeStep);
            Assert.Equal("prof_2", vm.EnrolledProfileId);
            Assert.Equal("Secondary Identity", vm.EnrolledDisplayName);
        }

        [Fact]
        public async Task AuthorizeEnrolledIdentity_OnSuccess_EnablesProfileAndReturnsToAlreadyEnrolled()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockLauncher = new MockPasswordLinkLauncher { ReturnExitCode = PasswordLinker.ExitOk };
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_2", DisplayName = "Secondary", Enabled = false }
                }
            };
            var vm = new MainViewModel(mockPipe, linkLauncher: mockLauncher, profileManager: mockProfiles);
            vm.EnrolledProfileId = "prof_2";
            vm.EnrolledDisplayName = "Secondary";
            vm.CurrentStep = SetupStep.ConfirmIdentityAuthorize;

            await ((AsyncRelayCommand)vm.AuthorizeEnrolledIdentityCommand).ExecuteAsync(null);

            Assert.True(mockProfiles.ToggleCalled);
            Assert.Equal("prof_2", mockProfiles.LastToggledProfileId);
            Assert.True(mockProfiles.LastToggledState);
            Assert.Equal(SetupStep.AlreadyEnrolled, vm.CurrentStep);
            Assert.Empty(vm.AuthorizeErrorMessage);
        }

        [Theory]
        [InlineData(PasswordLinker.ExitCancelled, "Authorization was cancelled")]
        [InlineData(PasswordLinker.ExitWrongPassword, "Incorrect Windows password")]
        [InlineData(PasswordLinker.ExitUacDeclined, "Administrator permission (UAC) was declined")]
        [InlineData(PasswordLinker.ExitIdentityLimit, "Identity limit reached")]
        [InlineData(PasswordLinker.ExitNotLinkedYet, "Link your Windows password first")]
        public async Task AuthorizeEnrolledIdentity_ExitCodeMappings_ShowClearMessages(int exitCode, string expectedSnippet)
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockLauncher = new MockPasswordLinkLauncher { ReturnExitCode = exitCode };
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_2", DisplayName = "Secondary", Enabled = false }
                }
            };
            var vm = new MainViewModel(mockPipe, linkLauncher: mockLauncher, profileManager: mockProfiles);
            vm.EnrolledProfileId = "prof_2";
            vm.EnrolledDisplayName = "Secondary";
            vm.CurrentStep = SetupStep.ConfirmIdentityAuthorize;

            await ((AsyncRelayCommand)vm.AuthorizeEnrolledIdentityCommand).ExecuteAsync(null);

            Assert.Equal(exitCode, vm.AuthorizeExitCode);
            Assert.Contains(expectedSnippet, vm.AuthorizeErrorMessage);
            Assert.Equal(SetupStep.ConfirmIdentityAuthorize, vm.CurrentStep);

            if (exitCode == PasswordLinker.ExitNotLinkedYet)
            {
                Assert.True(vm.IsAuthorizeNotLinkedYet);
            }
            else
            {
                Assert.False(vm.IsAuthorizeNotLinkedYet);
            }
        }

        [Fact]
        public async Task CancelAuthorizeEnrolledIdentity_KeepsProfilePendingInAlreadyEnrolled()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_pending", DisplayName = "Pending Person", Enabled = false, Authorized = false }
                }
            };
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);
            vm.EnrolledProfileId = "prof_pending";
            vm.CurrentStep = SetupStep.ConfirmIdentityAuthorize;

            await ((AsyncRelayCommand)vm.CancelAuthorizeEnrolledIdentityCommand).ExecuteAsync(null);

            Assert.Equal(SetupStep.AlreadyEnrolled, vm.CurrentStep);
            Assert.Single(vm.Identities);
            Assert.False(vm.Identities[0].IsAuthorized);
            Assert.True(vm.Identities[0].IsPending);
            Assert.Equal("Not activated", vm.Identities[0].StatusBadgeText);
        }

        [Fact]
        public async Task RecaptureProfile_PromptsInAppPassword_AndStartsEnrollmentOnSuccess()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles, passwordValidator: mockValidator);

            var item = new IdentityItemViewModel(
                "prof_target", "Dikson Recapture", true, true, 70, null,
                (i, e) => Task.CompletedTask,
                i => vm.RecaptureProfileAsync(i),
                i => Task.CompletedTask);

            await vm.RecaptureProfileAsync(item);

            Assert.True(vm.IsConfirmPasswordDialogOpen);
            Assert.Contains("recapture", vm.ConfirmPasswordDialogDescription);

            // Verify password
            using var sec = new SecureString();
            sec.AppendChar('P');
            await vm.VerifyConfirmPasswordAsync(sec);

            Assert.False(vm.IsConfirmPasswordDialogOpen);
            Assert.True(mockPipe.LastStartedKeepExisting);
            Assert.Equal("prof_target", mockPipe.LastStartedProfileId);
            Assert.Equal("Dikson Recapture", vm.DisplayName);
            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);
        }

        [Fact]
        public async Task RecaptureProfile_WrongPasswordThreeTimes_CancelsDialogWithoutEnrollment()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var mockValidator = new MockPasswordValidator(shouldAccept: false);
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles, passwordValidator: mockValidator);

            var item = new IdentityItemViewModel(
                "prof_target", "Dikson Recapture", true, true, 70, null,
                (i, e) => Task.CompletedTask,
                i => vm.RecaptureProfileAsync(i),
                i => Task.CompletedTask);

            await vm.RecaptureProfileAsync(item);
            Assert.True(vm.IsConfirmPasswordDialogOpen);

            using var sec = new SecureString();
            sec.AppendChar('W');

            // Attempt 1
            await vm.VerifyConfirmPasswordAsync(sec);
            Assert.True(vm.IsConfirmPasswordDialogOpen);
            Assert.Equal(2, vm.ConfirmPasswordAttemptsRemaining);

            // Attempt 2
            await vm.VerifyConfirmPasswordAsync(sec);
            Assert.True(vm.IsConfirmPasswordDialogOpen);
            Assert.Equal(1, vm.ConfirmPasswordAttemptsRemaining);

            // Attempt 3 -> closes
            await vm.VerifyConfirmPasswordAsync(sec);
            Assert.False(vm.IsConfirmPasswordDialogOpen);
            Assert.Null(mockPipe.LastStartedDisplayName);
            Assert.NotEqual(SetupStep.Enrollment, vm.CurrentStep);
        }

        [Fact]
        public async Task ToggleProfile_DisablingRequiresNoPassword()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);

            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, true, 80, null,
                (i, e) => vm.ToggleProfileAsync(i, e),
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            await vm.ToggleProfileAsync(item, false);

            Assert.True(mockProfiles.ToggleCalled);
            Assert.Equal("prof_1", mockProfiles.LastToggledProfileId);
            Assert.False(mockProfiles.LastToggledState);
            Assert.False(vm.IsConfirmPasswordDialogOpen);
        }

        [Fact]
        public async Task ToggleProfile_EnablingUnauthorizedLaunchesAuthorizeFlow()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var mockLauncher = new MockPasswordLinkLauncher { ReturnExitCode = PasswordLinker.ExitOk };
            var vm = new MainViewModel(mockPipe, linkLauncher: mockLauncher, profileManager: mockProfiles);

            var item = new IdentityItemViewModel(
                "prof_unauth", "Unauth Person", false, false, 50, null,
                (i, e) => vm.ToggleProfileAsync(i, e),
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            await vm.ToggleProfileAsync(item, true);

            // Should have launched authorize flow directly without in-app dialog
            Assert.False(vm.IsConfirmPasswordDialogOpen);
            Assert.True(mockProfiles.ToggleCalled);
            Assert.Equal("prof_unauth", mockProfiles.LastToggledProfileId);
            Assert.True(mockProfiles.LastToggledState);
        }

        [Fact]
        public async Task ToggleProfile_EnablingAuthorizedShowsInAppPasswordDialog()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles, passwordValidator: mockValidator);

            var item = new IdentityItemViewModel(
                "prof_auth", "Auth Person", false, true, 85, null,
                (i, e) => vm.ToggleProfileAsync(i, e),
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            await vm.ToggleProfileAsync(item, true);

            Assert.True(vm.IsConfirmPasswordDialogOpen);

            using var sec = new SecureString();
            sec.AppendChar('A');
            await vm.VerifyConfirmPasswordAsync(sec);

            Assert.False(vm.IsConfirmPasswordDialogOpen);
            Assert.True(mockProfiles.ToggleCalled);
            Assert.Equal("prof_auth", mockProfiles.LastToggledProfileId);
            Assert.True(mockProfiles.LastToggledState);
        }

        [Fact]
        public async Task DeleteProfile_ShowsConfirmation_AndOffersRevokeAuthorization()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_1", DisplayName = "Primary", Enabled = true, Authorized = true },
                    new ProfileModel { ProfileId = "prof_2", DisplayName = "Secondary", Enabled = true, Authorized = true }
                }
            };
            var mockLauncher = new MockPasswordLinkLauncher();
            var vm = new MainViewModel(mockPipe, linkLauncher: mockLauncher, profileManager: mockProfiles);
            await vm.RefreshIdentitiesAsync();
            Assert.Equal(2, vm.Identities.Count);

            var target = vm.Identities[1];
            await vm.DeleteProfileAsync(target);

            // Confirmation dialog opens
            Assert.True(vm.IsDeleteConfirmationDialogOpen);
            Assert.Equal("Secondary", vm.DeletingProfileName);

            // Confirm delete
            await ((AsyncRelayCommand)vm.ConfirmDeleteCommand).ExecuteAsync(null);

            Assert.False(vm.IsDeleteConfirmationDialogOpen);
            Assert.True(mockProfiles.DeleteCalled);
            Assert.Equal("prof_2", mockProfiles.LastDeletedProfileId);
            Assert.Single(vm.Identities);

            // Because it was authorized and there is still another identity left, offer revoke dialog opens
            Assert.True(vm.IsOfferRevokeDialogOpen);
            Assert.Equal("Secondary", vm.RevokingProfileName);

            // Confirm revoke
            await ((AsyncRelayCommand)vm.ConfirmRevokeIdentityCommand).ExecuteAsync(null);
            Assert.False(vm.IsOfferRevokeDialogOpen);
            Assert.Equal("prof_2", mockLauncher.LastProfileId);
        }

        [Fact]
        public async Task DeleteProfile_LastIdentity_OffersToUnlinkPassword()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_sole", DisplayName = "Sole User", Enabled = true, Authorized = true }
                }
            };
            var mockLauncher = new MockPasswordLinkLauncher();
            var vm = new MainViewModel(mockPipe, linkLauncher: mockLauncher, profileManager: mockProfiles);
            await vm.RefreshIdentitiesAsync();
            Assert.Single(vm.Identities);

            var target = vm.Identities[0];
            await vm.DeleteProfileAsync(target);
            Assert.True(vm.IsDeleteConfirmationDialogOpen);

            await ((AsyncRelayCommand)vm.ConfirmDeleteCommand).ExecuteAsync(null);

            Assert.False(vm.IsDeleteConfirmationDialogOpen);
            Assert.True(mockProfiles.DeleteCalled);
            Assert.Empty(vm.Identities);

            // Deleting the last identity offers to unlink password
            Assert.True(vm.IsOfferUnlinkDialogOpen);

            await ((AsyncRelayCommand)vm.ConfirmUnlinkPasswordCommand).ExecuteAsync(null);
            Assert.False(vm.IsOfferUnlinkDialogOpen);
            Assert.NotNull(mockLauncher.LastSid);
            Assert.Equal(SetupStep.Welcome, vm.CurrentStep);
        }

        [Fact]
        public async Task IdentityLimit_AtFiveIdentities_DisablesAddWithExplanation()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "p1", DisplayName = "One", Enabled = true, Authorized = true },
                    new ProfileModel { ProfileId = "p2", DisplayName = "Two", Enabled = true, Authorized = true },
                    new ProfileModel { ProfileId = "p3", DisplayName = "Three", Enabled = true, Authorized = true },
                    new ProfileModel { ProfileId = "p4", DisplayName = "Four", Enabled = true, Authorized = true },
                    new ProfileModel { ProfileId = "p5", DisplayName = "Five", Enabled = true, Authorized = true }
                }
            };
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);
            await vm.RefreshIdentitiesAsync();

            Assert.Equal(5, vm.Identities.Count);
            Assert.False(vm.CanAddIdentity);
            Assert.True(vm.IsIdentityLimitReached);
            Assert.Contains("Maximum 5 identities reached", vm.IdentityLimitExplanation);
        }

        [Theory]
        [InlineData("IDENTITY_LIMIT", "Maximum identity limit reached (5 identities)")]
        [InlineData("DUPLICATE_IDENTITY", "already exists")]
        public void EngineReasons_ShowFriendlyMessages(string reason, string expectedSnippet)
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe);
            vm.CurrentStep = SetupStep.Enrollment;

            mockPipe.TriggerEnrollmentFailed(reason, $"Technical reason: {reason}");

            Assert.Contains(expectedSnippet, vm.EngineErrorMessage);
        }

        [Fact]
        public void DuplicateIdentity_WithDetectedNameInDetail_ShowsProfileNameAlreadyExists()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe);
            vm.CurrentStep = SetupStep.Enrollment;

            mockPipe.TriggerEnrollmentFailed("DUPLICATE_IDENTITY", "Duplicate identity detected matching 'Rinu'");

            Assert.Equal("Profile \"Rinu\" already exists.", vm.EngineErrorMessage);
        }

        [Fact]
        public void DuplicateIdentity_WithDuplicateNameProperty_ShowsProfileNameAlreadyExists()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var vm = new MainViewModel(mockPipe);
            vm.CurrentStep = SetupStep.Enrollment;

            mockPipe.TriggerEnrollmentFailed("DUPLICATE_IDENTITY", "Duplicate identity detected", "Rinu");

            Assert.Equal("Profile \"Rinu\" already exists.", vm.EngineErrorMessage);
        }

        [Fact]
        public void IdentityItem_BeginAndCancelEdit_RevertsChanges()
        {
            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, true, 80, null,
                (i, e) => Task.CompletedTask,
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            Assert.False(item.IsEditingName);
            Assert.Equal("Dikson", item.DisplayName);

            item.BeginEditCommand.Execute(null);
            Assert.True(item.IsEditingName);
            Assert.Equal("Dikson", item.EditingName);

            item.EditingName = "Modified Name";
            item.CancelEditCommand.Execute(null);

            Assert.False(item.IsEditingName);
            Assert.Equal("Dikson", item.DisplayName);
            Assert.Equal("Dikson", item.EditingName);
        }

        [Fact]
        public async Task IdentityItem_SaveEdit_InvokesRenameCallbackAndUpdatesDisplayName()
        {
            bool callbackInvoked = false;
            string receivedName = string.Empty;

            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, true, 80, null,
                (i, e) => Task.CompletedTask,
                i => Task.CompletedTask,
                i => Task.CompletedTask,
                onRename: (i, newName) =>
                {
                    callbackInvoked = true;
                    receivedName = newName;
                    return Task.CompletedTask;
                });

            item.BeginEditCommand.Execute(null);
            item.EditingName = "Dikson Work Profile";

            await ((AsyncRelayCommand)item.SaveEditCommand).ExecuteAsync(null);

            Assert.True(callbackInvoked);
            Assert.Equal("Dikson Work Profile", receivedName);
            Assert.Equal("Dikson Work Profile", item.DisplayName);
            Assert.False(item.IsEditingName);
        }

        [Fact]
        public async Task ProfileManager_RenameProfileAsync_AtomicWriteEncryptsAndUpdatesFile()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTestProfiles_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempDir);
            try
            {
                string profileId = "test_profile_id";
                string filePath = Path.Combine(tempDir, $"{profileId}.peek");
                string originalJson = "{\"profile_id\":\"test_profile_id\",\"display_name\":\"OriginalName\",\"enabled\":true,\"created_at\":1000.0}";
                byte[] encrypted = ProtectedData.Protect(Encoding.UTF8.GetBytes(originalJson), null, DataProtectionScope.CurrentUser);
                await File.WriteAllBytesAsync(filePath, encrypted);

                var pm = new ProfileManager(tempDir);
                var profiles = await pm.GetProfilesAsync();
                Assert.Single(profiles);
                Assert.Equal("OriginalName", profiles[0].DisplayName);

                bool renameSuccess = await pm.RenameProfileAsync(profileId, "RenamedName");
                Assert.True(renameSuccess);

                var updatedProfiles = await pm.GetProfilesAsync();
                Assert.Single(updatedProfiles);
                Assert.Equal("RenamedName", updatedProfiles[0].DisplayName);

                bool invalidRename = await pm.RenameProfileAsync(profileId, "   ");
                Assert.False(invalidRename);

                bool missingProfile = await pm.RenameProfileAsync("missing_id", "Name");
                Assert.False(missingProfile);
            }
            finally
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
        }

        [Fact]
        public async Task ProfileManager_SaveProfileAsync_AtomicWriteAndReadsBack()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTestSave_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempDir);
            try
            {
                var pm = new ProfileManager(tempDir);
                string profileId = "new_save_id";
                string json = "{\"profile_id\":\"new_save_id\",\"display_name\":\"Saved User\",\"enabled\":false,\"created_at\":2000.0}";
                byte[] encrypted = ProtectedData.Protect(Encoding.UTF8.GetBytes(json), null, DataProtectionScope.CurrentUser);

                bool saved = await pm.SaveProfileAsync(profileId, encrypted);
                Assert.True(saved);

                var profiles = await pm.GetProfilesAsync();
                Assert.Single(profiles);
                Assert.Equal("Saved User", profiles[0].DisplayName);
                Assert.False(profiles[0].Enabled);

                // Ensure temp file was cleaned up by atomic replace
                string tempFile = Path.Combine(tempDir, $"{profileId}.peek.tmp");
                Assert.False(File.Exists(tempFile));
            }
            finally
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
        }
    }
}
