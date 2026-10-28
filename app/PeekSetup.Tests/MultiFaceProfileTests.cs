using System;
using System.Collections.Generic;
using System.Security;
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

        public bool RenameCalled { get; private set; }
        public string? LastRenamedProfileId { get; private set; }
        public string? LastRenamedDisplayName { get; private set; }

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
    }

    public class MultiFaceProfileTests
    {
        [Fact]
        public void QualityBars_AlwaysGeneratesTwentyEqualizerBars()
        {
            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, 39,
                (i, e) => Task.CompletedTask,
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            Assert.Equal(20, item.QualityBars.Count);
            Assert.Equal("Capture quality • 39%", item.CaptureQualityText);

            // In 39% quality, 39% of 20 = 7.8 -> first 8 ticks should be active
            for (int i = 0; i < 8; i++)
            {
                Assert.True(item.QualityBars[i].IsActive);
            }
            // Later ticks should be inactive
            for (int i = 8; i < 20; i++)
            {
                Assert.False(item.QualityBars[i].IsActive);
            }
        }

        [Fact]
        public void IdentityItem_ToggleCommand_TriggersCallback()
        {
            bool callbackInvoked = false;
            bool targetState = false;

            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, 75,
                (i, state) =>
                {
                    callbackInvoked = true;
                    targetState = state;
                    return Task.CompletedTask;
                },
                i => Task.CompletedTask,
                i => Task.CompletedTask);

            item.IsEnabled = false;

            Assert.True(callbackInvoked);
            Assert.False(targetState);
        }

        [Fact]
        public void StartAddIdentity_OpensPasswordVerificationStep()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);

            vm.StartAddIdentityCommand.Execute(null);

            Assert.True(vm.IsAddingNewIdentity);
            Assert.False(vm.IsPasswordVerifiedForAdd);
            Assert.Empty(vm.PasswordVerificationError);
        }

        [Fact]
        public async Task VerifyPasswordForAdd_WhenEmpty_ShowsError()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var mockValidator = new MockPasswordValidator();
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles, passwordValidator: mockValidator);

            vm.StartAddIdentityCommand.Execute(null);
            vm.PasswordVerificationInput = "";

            await ((AsyncRelayCommand)vm.VerifyPasswordForAddCommand).ExecuteAsync(null);

            Assert.False(vm.IsPasswordVerifiedForAdd);
            Assert.True(vm.HasPasswordVerificationError);
            Assert.Contains("Please enter your Windows password", vm.PasswordVerificationError);
        }

        [Fact]
        public async Task VerifyPasswordForAdd_WhenInvalid_ShowsIncorrectPasswordError()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var mockValidator = new MockPasswordValidator(shouldAccept: false);
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles, passwordValidator: mockValidator);

            vm.StartAddIdentityCommand.Execute(null);
            vm.PasswordVerificationInput = "WrongPassword123";

            await ((AsyncRelayCommand)vm.VerifyPasswordForAddCommand).ExecuteAsync(null);

            Assert.True(mockValidator.ValidateCallCount > 0);
            Assert.False(vm.IsPasswordVerifiedForAdd);
            Assert.True(vm.HasPasswordVerificationError);
            Assert.Contains("Incorrect password", vm.PasswordVerificationError);
        }

        [Fact]
        public async Task VerifyPasswordForAdd_WhenValid_AdvancesToNameInput()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var mockValidator = new MockPasswordValidator(shouldAccept: true);
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles, passwordValidator: mockValidator);

            vm.StartAddIdentityCommand.Execute(null);
            vm.PasswordVerificationInput = "CorrectPassword123";

            await ((AsyncRelayCommand)vm.VerifyPasswordForAddCommand).ExecuteAsync(null);

            Assert.True(mockValidator.ValidateCallCount > 0);
            Assert.True(vm.IsPasswordVerifiedForAdd);
            Assert.False(vm.HasPasswordVerificationError);
            Assert.NotEmpty(vm.NewIdentityName);
        }

        [Fact]
        public async Task ConfirmAddIdentity_InitiatesEnrollmentWithKeepExisting()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);

            vm.StartAddIdentityCommand.Execute(null);
            vm.IsPasswordVerifiedForAdd = true;
            vm.NewIdentityName = "Dikson Glasses";

            await ((AsyncRelayCommand)vm.ConfirmAddIdentityCommand).ExecuteAsync(null);

            Assert.False(vm.IsAddingNewIdentity);
            Assert.Equal("Dikson Glasses", vm.DisplayName);
            Assert.True(mockPipe.LastStartedKeepExisting);
            Assert.Null(mockPipe.LastStartedProfileId);
            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);
        }

        [Fact]
        public async Task ConfirmAddIdentity_ResetsEnrollmentCompleteRing_AndInitializesGuidanceProperly()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);

            // Simulate that a prior enrollment completed and set the complete ring
            vm.IsEnrollmentCompleteRing = true;
            Assert.Equal("Face Enrollment Complete", vm.FriendlyGuidanceText);

            vm.StartAddIdentityCommand.Execute(null);
            vm.IsPasswordVerifiedForAdd = true;
            vm.NewIdentityName = "Dikson Secondary";

            await ((AsyncRelayCommand)vm.ConfirmAddIdentityCommand).ExecuteAsync(null);

            // The ring must be cleanly reset for the new enrollment session
            Assert.False(vm.IsEnrollmentCompleteRing);
            Assert.False(vm.IsPoseCheckActive);
            Assert.Equal(0.0, vm.ProgressPercentage);
            Assert.NotEqual("Face Enrollment Complete", vm.FriendlyGuidanceText);
            Assert.Equal("Look straight at the camera", vm.FriendlyGuidanceText);
            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);
        }

        [Fact]
        public async Task SecondaryEnrollment_OnCompletion_ReturnsDirectlyToAlreadyEnrolledWithoutPasswordLink()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_1", DisplayName = "Dikson", Enabled = true, QualityPercentage = 80 }
                }
            };
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles)
            {
                EnrollmentCompleteDelayMs = 0
            };
            await vm.RefreshIdentitiesAsync();
            Assert.Single(vm.Identities);

            // Start secondary identity addition
            vm.StartAddIdentityCommand.Execute(null);
            vm.IsPasswordVerifiedForAdd = true;
            vm.NewIdentityName = "Dikson Team";
            await ((AsyncRelayCommand)vm.ConfirmAddIdentityCommand).ExecuteAsync(null);

            // Simulate enrollment completion event from pipe client
            mockPipe.TriggerEnrollmentCompleted("prof_2", "Dikson Team");
            await Task.Delay(30);

            // Secondary face must bypass password linking and return to Profile Section
            Assert.Equal(SetupStep.AlreadyEnrolled, vm.CurrentStep);
            Assert.True(vm.IsAlreadyEnrolledStep);
            Assert.False(vm.IsPasswordLinkStep);
        }

        [Fact]
        public async Task RecaptureProfile_StartsEnrollmentWithTargetProfileId()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager();
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);

            var item = new IdentityItemViewModel(
                "prof_target", "Dikson Recapture", true, 50,
                (i, e) => Task.CompletedTask,
                i => vm.RecaptureProfileAsync(i),
                i => Task.CompletedTask);

            await vm.RecaptureProfileAsync(item);

            Assert.True(mockPipe.LastStartedKeepExisting);
            Assert.Equal("prof_target", mockPipe.LastStartedProfileId);
            Assert.Equal("Dikson Recapture", vm.DisplayName);
            Assert.Equal(SetupStep.Enrollment, vm.CurrentStep);
        }

        [Fact]
        public async Task DeleteProfile_RemovesFromListAndStorage()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_1", DisplayName = "Dikson 1", Enabled = true, QualityPercentage = 70 },
                    new ProfileModel { ProfileId = "prof_2", DisplayName = "Dikson 2", Enabled = true, QualityPercentage = 85 }
                }
            };
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);
            await vm.RefreshIdentitiesAsync();
            Assert.Equal(2, vm.Identities.Count);

            var target = vm.Identities[0];
            await vm.DeleteProfileAsync(target);

            Assert.True(mockProfiles.DeleteCalled);
            Assert.Equal("prof_1", mockProfiles.LastDeletedProfileId);
            Assert.Single(vm.Identities);
            Assert.Equal("prof_2", vm.Identities[0].ProfileId);
        }

        [Fact]
        public void IdentityItem_BeginAndCancelEdit_RevertsChanges()
        {
            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, 80,
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
                "prof_1", "Dikson", true, 80,
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
        public async Task IdentityItem_SaveEdit_WhenEmptyOrWhitespace_DiscardsChange()
        {
            bool callbackInvoked = false;

            var item = new IdentityItemViewModel(
                "prof_1", "Dikson", true, 80,
                (i, e) => Task.CompletedTask,
                i => Task.CompletedTask,
                i => Task.CompletedTask,
                onRename: (i, newName) =>
                {
                    callbackInvoked = true;
                    return Task.CompletedTask;
                });

            item.BeginEditCommand.Execute(null);
            item.EditingName = "   ";

            await ((AsyncRelayCommand)item.SaveEditCommand).ExecuteAsync(null);

            Assert.False(callbackInvoked);
            Assert.Equal("Dikson", item.DisplayName);
            Assert.Equal("Dikson", item.EditingName);
            Assert.False(item.IsEditingName);
        }

        [Fact]
        public async Task MainViewModel_RenameProfile_PersistsAndUpdatesActiveProfileName()
        {
            var mockPipe = new MockEnrollmentPipeClient();
            var mockProfiles = new MockProfileManager
            {
                Profiles = new List<ProfileModel>
                {
                    new ProfileModel { ProfileId = "prof_primary", DisplayName = "Original Primary", Enabled = true, QualityPercentage = 80 },
                    new ProfileModel { ProfileId = "prof_secondary", DisplayName = "Secondary", Enabled = true, QualityPercentage = 60 }
                }
            };
            var vm = new MainViewModel(mockPipe, profileManager: mockProfiles);
            await vm.RefreshIdentitiesAsync();

            Assert.Equal("Original Primary", vm.ExistingProfileName);
            Assert.Equal("prof_primary", vm.ExistingProfileId);

            var primaryItem = vm.Identities[0];
            await vm.RenameProfileAsync(primaryItem, "Renamed Primary");

            Assert.True(mockProfiles.RenameCalled);
            Assert.Equal("prof_primary", mockProfiles.LastRenamedProfileId);
            Assert.Equal("Renamed Primary", mockProfiles.LastRenamedDisplayName);
            Assert.Equal("Renamed Primary", vm.ExistingProfileName);
        }

        [Fact]
        public async Task ProfileManager_RenameProfileAsync_EncryptsAndUpdatesFile()
        {
            string tempDir = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "PeekTestProfiles_" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(tempDir);
            try
            {
                string profileId = "test_profile_id";
                string filePath = System.IO.Path.Combine(tempDir, $"{profileId}.peek");
                string originalJson = "{\"profile_id\":\"test_profile_id\",\"display_name\":\"OriginalName\",\"enabled\":true,\"created_at\":1000.0}";
                byte[] encrypted = System.Security.Cryptography.ProtectedData.Protect(System.Text.Encoding.UTF8.GetBytes(originalJson), null, System.Security.Cryptography.DataProtectionScope.CurrentUser);
                await System.IO.File.WriteAllBytesAsync(filePath, encrypted);

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
                if (System.IO.Directory.Exists(tempDir))
                {
                    System.IO.Directory.Delete(tempDir, true);
                }
            }
        }
    }
}
