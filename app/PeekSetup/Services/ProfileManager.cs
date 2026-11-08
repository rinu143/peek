using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace PeekSetup.Services
{
    public class ProfileManager : IProfileManager
    {
        private readonly string _profilesDirectory;

        public ProfileManager(string? profilesDir = null)
        {
            _profilesDirectory = profilesDir
                ?? Environment.GetEnvironmentVariable("PEEK_PROFILES_DIR")
                ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Peek", "Profiles");
        }

        public async Task<List<ProfileModel>> GetProfilesAsync()
        {
            var result = new List<ProfileModel>();
            if (!Directory.Exists(_profilesDirectory))
            {
                return result;
            }

            var files = Directory.GetFiles(_profilesDirectory, "*.peek");
            foreach (var file in files)
            {
                try
                {
                    byte[] encrypted = await File.ReadAllBytesAsync(file).ConfigureAwait(false);
                    byte[] decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
                    string json = Encoding.UTF8.GetString(decrypted);

                    using var doc = JsonDocument.Parse(json);
                    var root = doc.RootElement;

                    string id = root.TryGetProperty("profile_id", out var idProp) ? idProp.GetString() ?? "" : Path.GetFileNameWithoutExtension(file);
                    string name = root.TryGetProperty("display_name", out var nameProp) ? nameProp.GetString() ?? "User" : "User";
                    bool enabled = !root.TryGetProperty("enabled", out var enProp) || enProp.GetBoolean();
                    double createdAt = root.TryGetProperty("created_at", out var catProp) ? catProp.GetDouble() : 0.0;
                    string? userSid = root.TryGetProperty("user_sid", out var sidProp) ? sidProp.GetString() : null;

                    double totalQuality = 0;
                    int count = 0;
                    if (root.TryGetProperty("templates", out var tProp) && tProp.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var tmpl in tProp.EnumerateArray())
                        {
                            if (tmpl.TryGetProperty("quality_score", out var qsProp))
                            {
                                totalQuality += qsProp.GetDouble();
                                count++;
                            }
                        }
                    }

                    int qualityPct = 85;
                    if (count > 0)
                    {
                        double avg = totalQuality / count;
                        qualityPct = (int)Math.Clamp(Math.Round(avg * 100), 10, 100);
                    }

                    result.Add(new ProfileModel
                    {
                        ProfileId = id,
                        DisplayName = name,
                        Enabled = enabled,
                        QualityPercentage = qualityPct,
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

        public async Task<bool> ToggleProfileAsync(string profileId, bool enabled)
        {
            string path = Path.Combine(_profilesDirectory, $"{profileId}.peek");
            if (!File.Exists(path)) return false;

            try
            {
                byte[] encrypted = await File.ReadAllBytesAsync(path).ConfigureAwait(false);
                byte[] decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
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
                byte[] updatedEncrypted = ProtectedData.Protect(updatedJson, null, DataProtectionScope.CurrentUser);
                await File.WriteAllBytesAsync(path, updatedEncrypted).ConfigureAwait(false);
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
                byte[] encrypted = await File.ReadAllBytesAsync(path).ConfigureAwait(false);
                byte[] decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
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
                byte[] updatedEncrypted = ProtectedData.Protect(updatedJson, null, DataProtectionScope.CurrentUser);
                await File.WriteAllBytesAsync(path, updatedEncrypted).ConfigureAwait(false);
                return true;
            }
            catch
            {
                return false;
            }
        }
    }
}
