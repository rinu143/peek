using System;
using System.IO;
using System.Text.Json;
using Microsoft.Win32;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public class PolicyManager
    {
        private const string HklmUpdatesKey = @"SOFTWARE\Peek\Updates";
        public const string DefaultFeedUrl = "https://raw.githubusercontent.com/peek/updates/main";

        private readonly string _userSettingsPath;
        private readonly string _userStatePath;

        public PolicyManager(string? customSettingsPath = null, string? customStatePath = null)
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string peekDir = Path.Combine(localAppData, "Peek");

            _userSettingsPath = customSettingsPath ?? Path.Combine(peekDir, "update-settings.json");
            _userStatePath = customStatePath ?? Path.Combine(peekDir, "update-state.json");
        }

        public string UserSettingsPath => _userSettingsPath;
        public string UserStatePath => _userStatePath;

        public UpdateSettings LoadUserSettings()
        {
            try
            {
                if (File.Exists(_userSettingsPath))
                {
                    string json = File.ReadAllText(_userSettingsPath);
                    var settings = JsonSerializer.Deserialize<UpdateSettings>(json);
                    if (settings != null) return settings;
                }
            }
            catch
            {
                // Fallback to default
            }

            return new UpdateSettings();
        }

        public void SaveUserSettings(UpdateSettings settings)
        {
            try
            {
                string? dir = Path.GetDirectoryName(_userSettingsPath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                string json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(_userSettingsPath, json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to save user settings: {ex.Message}");
            }
        }

        public UpdateState LoadUserState()
        {
            try
            {
                if (File.Exists(_userStatePath))
                {
                    string json = File.ReadAllText(_userStatePath);
                    var state = JsonSerializer.Deserialize<UpdateState>(json);
                    if (state != null) return state;
                }
            }
            catch
            {
                // Fallback
            }

            return new UpdateState();
        }

        public void SaveUserState(UpdateState state)
        {
            try
            {
                string? dir = Path.GetDirectoryName(_userStatePath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                string json = JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(_userStatePath, json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to save user state: {ex.Message}");
            }
        }

        public int? GetHklmAutoCheck()
        {
            try
            {
                using var key = Registry.LocalMachine.OpenSubKey(HklmUpdatesKey, false);
                if (key != null)
                {
                    object? val = key.GetValue("AutoCheck");
                    if (val is int intVal) return intVal;
                }
            }
            catch { }
            return null;
        }

        public string? GetHklmChannel()
        {
            try
            {
                using var key = Registry.LocalMachine.OpenSubKey(HklmUpdatesKey, false);
                if (key != null)
                {
                    object? val = key.GetValue("Channel");
                    if (val is string strVal && !string.IsNullOrWhiteSpace(strVal))
                    {
                        return strVal.Trim();
                    }
                }
            }
            catch { }
            return null;
        }

        public string GetHklmFeedUrl()
        {
            try
            {
                using var key = Registry.LocalMachine.OpenSubKey(HklmUpdatesKey, false);
                if (key != null)
                {
                    object? val = key.GetValue("FeedUrl");
                    if (val is string strVal && !string.IsNullOrWhiteSpace(strVal))
                    {
                        return strVal.Trim().TrimEnd('/');
                    }
                }
            }
            catch { }

            return DefaultFeedUrl;
        }

        /// <summary>
        /// Precedence:
        /// 1. If HKLM AutoCheck == 0, checks are completely disabled by admin policy.
        /// 2. If user settings AutoCheck == false, checks are disabled by user.
        /// 3. Otherwise true.
        /// </summary>
        public bool GetEffectiveAutoCheck()
        {
            int? policyAutoCheck = GetHklmAutoCheck();
            if (policyAutoCheck.HasValue && policyAutoCheck.Value == 0)
            {
                return false;
            }

            var userSettings = LoadUserSettings();
            if (userSettings.AutoCheck.HasValue && !userSettings.AutoCheck.Value)
            {
                return false;
            }

            return true;
        }

        /// <summary>
        /// Precedence:
        /// 1. HKLM Channel if specified.
        /// 2. User setting Channel if specified ("stable" or "beta").
        /// 3. Default "stable".
        /// </summary>
        public string GetEffectiveChannel()
        {
            string? policyChannel = GetHklmChannel();
            if (!string.IsNullOrWhiteSpace(policyChannel))
            {
                return policyChannel.ToLowerInvariant() == "beta" ? "beta" : "stable";
            }

            var userSettings = LoadUserSettings();
            if (!string.IsNullOrWhiteSpace(userSettings.Channel))
            {
                return userSettings.Channel.ToLowerInvariant() == "beta" ? "beta" : "stable";
            }

            return "stable";
        }

        /// <summary>
        /// FeedUrl is NEVER read from user settings; strictly from HKLM or default.
        /// </summary>
        public string GetEffectiveFeedUrl()
        {
            return GetHklmFeedUrl();
        }

        public bool IsSnoozed()
        {
            var userSettings = LoadUserSettings();
            if (string.IsNullOrWhiteSpace(userSettings.SnoozeUntil)) return false;

            if (DateTime.TryParse(userSettings.SnoozeUntil, out var snoozeDate))
            {
                return snoozeDate.ToUniversalTime() > DateTime.UtcNow;
            }

            return false;
        }

        public bool IsVersionSkipped(string version)
        {
            if (string.IsNullOrWhiteSpace(version)) return false;
            var userSettings = LoadUserSettings();
            return string.Equals(userSettings.SkippedVersion, version.Trim(), StringComparison.OrdinalIgnoreCase);
        }

        public void Snooze(TimeSpan duration)
        {
            var userSettings = LoadUserSettings();
            userSettings.SnoozeUntil = DateTime.UtcNow.Add(duration).ToString("o");
            SaveUserSettings(userSettings);
        }

        public void SkipVersion(string version)
        {
            var userSettings = LoadUserSettings();
            userSettings.SkippedVersion = version.Trim();
            SaveUserSettings(userSettings);
        }
    }
}
