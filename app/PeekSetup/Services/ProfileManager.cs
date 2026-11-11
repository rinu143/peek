using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace PeekSetup.Services
{
    public class ProfileManager : IProfileManager
    {
        private readonly string _profilesDirectory;
        private readonly IAuthorizationStore _authStore;
        private readonly IAccountSidResolver _sidResolver;
        private readonly Func<byte[]> _entropyProvider;

        public ProfileManager(
            string? profilesDir = null,
            IAuthorizationStore? authStore = null,
            IAccountSidResolver? sidResolver = null,
            Func<byte[]>? entropyProvider = null)
        {
            _profilesDirectory = profilesDir
                ?? Environment.GetEnvironmentVariable("PEEK_PROFILES_DIR")
                ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Peek", "Profiles");
            _authStore = authStore ?? new RegistryAuthorizationStore();
            _sidResolver = sidResolver ?? new AccountSidResolver();
            _entropyProvider = entropyProvider ?? MachineEntropyProvider.GetMachineEntropy;
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
            var result = new List<ProfileModel>();
            if (!Directory.Exists(_profilesDirectory))
            {
                return result;
            }

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

            var files = Directory.GetFiles(_profilesDirectory, "*.peek");
            if (files.Length == 0)
            {
                return result;
            }

            byte[] entropy = _entropyProvider();
            foreach (var file in files)
            {
                try
                {
                    byte[] encrypted = await File.ReadAllBytesAsync(file).ConfigureAwait(false);
                    byte[] decrypted;
                    try
                    {
                        decrypted = ProtectedData.Unprotect(encrypted, entropy, DataProtectionScope.CurrentUser);
                    }
                    catch
                    {
                        // Attempt legacy fallback for backward compatibility
                        decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
                        // Re-encrypt in place with machine-bound entropy
                        try
                        {
                            byte[] reEncrypted = ProtectedData.Protect(decrypted, entropy, DataProtectionScope.CurrentUser);
                            await AtomicWriteProfileAsync(file, reEncrypted).ConfigureAwait(false);
                        }
                        catch { }
                    }
                    string json = Encoding.UTF8.GetString(decrypted);

                    using var doc = JsonDocument.Parse(json);
                    var root = doc.RootElement;

                    string id = root.TryGetProperty("profile_id", out var idProp) ? idProp.GetString() ?? "" : Path.GetFileNameWithoutExtension(file);
                    string name = root.TryGetProperty("display_name", out var nameProp) ? nameProp.GetString() ?? "User" : "User";
                    bool enabled = !root.TryGetProperty("enabled", out var enProp) || enProp.GetBoolean();
                    double createdAt = root.TryGetProperty("created_at", out var catProp) ? catProp.GetDouble() : 0.0;
                    string? userSid = root.TryGetProperty("user_sid", out var sidProp) ? sidProp.GetString() : null;

                    var poseQualities = new List<double>();
                    double totalQuality = 0;
                    int count = 0;
                    if (root.TryGetProperty("templates", out var tProp) && tProp.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var tmpl in tProp.EnumerateArray())
                        {
                            if (tmpl.TryGetProperty("quality_score", out var qsProp))
                            {
                                double q = qsProp.GetDouble();
                                poseQualities.Add(q);
                                totalQuality += q;
                                count++;
                            }
                        }
                    }

                    int? qualityPct = null;
                    if (count > 0)
                    {
                        double avg = totalQuality / count;
                        qualityPct = (int)Math.Clamp(Math.Round(avg * 100), 10, 100);
                    }

                    string effectiveSid = !string.IsNullOrWhiteSpace(userSid) ? userSid : currentSid;
                    var authSet = GetAuthorizedForSid(effectiveSid);
                    bool isAuthorized = authSet.Contains(id);

                    result.Add(new ProfileModel
                    {
                        ProfileId = id,
                        DisplayName = name,
                        Enabled = enabled,
                        Authorized = isAuthorized,
                        QualityPercentage = qualityPct,
                        PoseQualities = poseQualities,
                        TemplateCount = count,
                        CreatedAt = createdAt,
                        UserSid = userSid
                    });
                }
                catch
                {
                    // Ignore unreadable or corrupt profile
                }
            }

            result.Sort((a, b) => a.CreatedAt.CompareTo(b.CreatedAt));
            return result;
        }

        private async Task AtomicWriteProfileAsync(string path, byte[] encryptedData)
        {
            string dir = Path.GetDirectoryName(path) ?? _profilesDirectory;
            if (!Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }

            string tempPath = Path.Combine(dir, $"{Path.GetFileNameWithoutExtension(path)}_{Guid.NewGuid():N}.tmp");
            try
            {
                await File.WriteAllBytesAsync(tempPath, encryptedData).ConfigureAwait(false);
                if (File.Exists(path))
                {
                    File.Replace(tempPath, path, null, ignoreMetadataErrors: true);
                }
                else
                {
                    File.Move(tempPath, path, overwrite: true);
                }
            }
            catch
            {
                if (File.Exists(tempPath))
                {
                    try { File.Delete(tempPath); } catch { }
                }
                throw;
            }
        }

        public async Task<bool> SaveProfileAsync(string profileId, byte[] encryptedData)
        {
            if (string.IsNullOrWhiteSpace(profileId) || encryptedData == null) return false;
            string path = Path.Combine(_profilesDirectory, $"{profileId}.peek");
            try
            {
                await AtomicWriteProfileAsync(path, encryptedData).ConfigureAwait(false);
                return true;
            }
            catch
            {
                return false;
            }
        }

        public async Task<bool> ToggleProfileAsync(string profileId, bool enabled)
        {
            string path = Path.Combine(_profilesDirectory, $"{profileId}.peek");
            if (!File.Exists(path)) return false;

            try
            {
                byte[] entropy = _entropyProvider();
                byte[] encrypted = await File.ReadAllBytesAsync(path).ConfigureAwait(false);
                byte[] decrypted;
                try
                {
                    decrypted = ProtectedData.Unprotect(encrypted, entropy, DataProtectionScope.CurrentUser);
                }
                catch
                {
                    decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
                }
                string json = Encoding.UTF8.GetString(decrypted);

                using var doc = JsonDocument.Parse(json);
                using var stream = new MemoryStream();
                using (var writer = new Utf8JsonWriter(stream))
                {
                    writer.WriteStartObject();
                    bool foundEnabled = false;
                    foreach (var prop in doc.RootElement.EnumerateObject())
                    {
                        if (prop.NameEquals("enabled"))
                        {
                            writer.WriteBoolean("enabled", enabled);
                            foundEnabled = true;
                        }
                        else
                        {
                            prop.WriteTo(writer);
                        }
                    }
                    if (!foundEnabled)
                    {
                        writer.WriteBoolean("enabled", enabled);
                    }
                    writer.WriteEndObject();
                }

                byte[] updatedJson = stream.ToArray();
                byte[] updatedEncrypted = ProtectedData.Protect(updatedJson, entropy, DataProtectionScope.CurrentUser);
                await AtomicWriteProfileAsync(path, updatedEncrypted).ConfigureAwait(false);
                return true;
            }
            catch
            {
                return false;
            }
        }

        public Task<bool> DeleteProfileAsync(string profileId)
        {
            string path = Path.Combine(_profilesDirectory, $"{profileId}.peek");
            if (File.Exists(path))
            {
                try
                {
                    File.Delete(path);
                    return Task.FromResult(true);
                }
                catch
                {
                    return Task.FromResult(false);
                }
            }
            return Task.FromResult(false);
        }

        public async Task<bool> RenameProfileAsync(string profileId, string newDisplayName)
        {
            if (string.IsNullOrWhiteSpace(newDisplayName)) return false;
            string path = Path.Combine(_profilesDirectory, $"{profileId}.peek");
            if (!File.Exists(path)) return false;

            try
            {
                byte[] entropy = _entropyProvider();
                byte[] encrypted = await File.ReadAllBytesAsync(path).ConfigureAwait(false);
                byte[] decrypted;
                try
                {
                    decrypted = ProtectedData.Unprotect(encrypted, entropy, DataProtectionScope.CurrentUser);
                }
                catch
                {
                    decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
                }
                string json = Encoding.UTF8.GetString(decrypted);

                using var doc = JsonDocument.Parse(json);
                using var stream = new MemoryStream();
                using (var writer = new Utf8JsonWriter(stream))
                {
                    writer.WriteStartObject();
                    bool foundName = false;
                    foreach (var prop in doc.RootElement.EnumerateObject())
                    {
                        if (prop.NameEquals("display_name"))
                        {
                            writer.WriteString("display_name", newDisplayName.Trim());
                            foundName = true;
                        }
                        else
                        {
                            prop.WriteTo(writer);
                        }
                    }
                    if (!foundName)
                    {
                        writer.WriteString("display_name", newDisplayName.Trim());
                    }
                    writer.WriteEndObject();
                }

                byte[] updatedJson = stream.ToArray();
                byte[] updatedEncrypted = ProtectedData.Protect(updatedJson, entropy, DataProtectionScope.CurrentUser);
                await AtomicWriteProfileAsync(path, updatedEncrypted).ConfigureAwait(false);
                return true;
            }
            catch
            {
                return false;
            }
        }
    }
}
