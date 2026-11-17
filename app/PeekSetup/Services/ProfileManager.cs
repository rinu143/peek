using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Security.Principal;
using System.Threading.Tasks;
using PeekSetup.Models;

namespace PeekSetup.Services
{
    public class ProfileManager : IProfileManager
    {
        private readonly IEnrollmentPipeClient _pipeClient;
        private readonly IAuthorizationStore _authStore;
        private readonly IAccountSidResolver _sidResolver;

        public ProfileManager(
            IEnrollmentPipeClient? pipeClient = null,
            IAuthorizationStore? authStore = null,
            IAccountSidResolver? sidResolver = null)
        {
            _pipeClient = pipeClient ?? new EnrollmentPipeClient();
            _authStore = authStore ?? new RegistryAuthorizationStore();
            _sidResolver = sidResolver ?? new AccountSidResolver();
        }

        private string GetCurrentUserSid()
        {
            try
            {
                return WindowsIdentity.GetCurrent().User?.Value ?? string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }

        public async Task<List<ProfileModel>> GetProfilesAsync()
        {
            try
            {
                var profiles = await _pipeClient.ListProfilesAsync().ConfigureAwait(false);
                string currentSid = GetCurrentUserSid();
                var authorizedCache = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);

                HashSet<string> GetAuthorizedForSid(string sid)
                {
                    if (string.IsNullOrWhiteSpace(sid))
                        return new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                    if (!authorizedCache.TryGetValue(sid, out var set))
                    {
                        set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                        try
                        {
                            var ids = _authStore.GetAuthorizedIdentities(sid);
                            if (ids != null)
                            {
                                foreach (var id in ids)
                                {
                                    set.Add(id);
                                }
                            }
                        }
                        catch
                        {
                            // Ignore registry errors
                        }
                        authorizedCache[sid] = set;
                    }
                    return set;
                }

                foreach (var p in profiles)
                {
                    string effectiveSid = !string.IsNullOrWhiteSpace(p.UserSid) ? p.UserSid : currentSid;
                    var authSet = GetAuthorizedForSid(effectiveSid);
                    p.Authorized = authSet.Contains(p.ProfileId);
                }

                profiles.Sort((a, b) => a.CreatedAt.CompareTo(b.CreatedAt));
                return profiles;
            }
            catch (Exception ex)
            {
                Trace.TraceWarning($"ProfileManager.GetProfilesAsync failed via pipe: {ex.Message}");
                return new List<ProfileModel>();
            }
        }

        public async Task<bool> ToggleProfileAsync(string profileId, bool enabled)
        {
            if (string.IsNullOrWhiteSpace(profileId)) return false;
            try
            {
                return await _pipeClient.SetProfileEnabledAsync(profileId, enabled).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                Trace.TraceError($"ProfileManager.ToggleProfileAsync failed via pipe: {ex.Message}");
                return false;
            }
        }

        public async Task<bool> DeleteProfileAsync(string profileId)
        {
            if (string.IsNullOrWhiteSpace(profileId)) return false;
            try
            {
                return await _pipeClient.DeleteProfileAsync(profileId).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                Trace.TraceError($"ProfileManager.DeleteProfileAsync failed via pipe: {ex.Message}");
                return false;
            }
        }

        public async Task<bool> RenameProfileAsync(string profileId, string newDisplayName)
        {
            if (string.IsNullOrWhiteSpace(profileId) || string.IsNullOrWhiteSpace(newDisplayName)) return false;
            try
            {
                return await _pipeClient.RenameProfileAsync(profileId, newDisplayName.Trim()).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                Trace.TraceError($"ProfileManager.RenameProfileAsync failed via pipe: {ex.Message}");
                return false;
            }
        }

        public Task<bool> SaveProfileAsync(string profileId, byte[] encryptedData)
        {
            // The engine performs all profile file I/O directly via enrollment.
            // PeekSetup never writes profile files directly.
            return Task.FromResult(true);
        }
    }
}
