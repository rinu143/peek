using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public class UpdateCheckResult
    {
        public bool Success { get; set; }
        public bool UpdateAvailable { get; set; }
        public bool NotModified { get; set; }
        public bool IsOffline { get; set; }
        public string? ErrorMessage { get; set; }
        public UpdateManifest? Manifest { get; set; }
        public string InstalledVersion { get; set; } = "0.0.0";
        public string Channel { get; set; } = "stable";
    }

    public class UpdateChecker
    {
        public const long MaxAllowedSizeBytes = 500L * 1024L * 1024L; // 500 MB
        private static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(15);

        private readonly HttpClient _httpClient;
        private readonly SignatureVerifier _signatureVerifier;
        private readonly PolicyManager _policyManager;

        public UpdateChecker(SignatureVerifier? signatureVerifier = null, PolicyManager? policyManager = null, HttpClient? httpClient = null)
        {
            _signatureVerifier = signatureVerifier ?? new SignatureVerifier();
            _policyManager = policyManager ?? new PolicyManager();

            if (httpClient != null)
            {
                _httpClient = httpClient;
            }
            else
            {
                var handler = new HttpClientHandler
                {
                    AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate
                };
                _httpClient = new HttpClient(handler)
                {
                    Timeout = RequestTimeout
                };
            }

            string appVersion = GetAppVersion();
            _httpClient.DefaultRequestHeaders.UserAgent.Clear();
            _httpClient.DefaultRequestHeaders.UserAgent.ParseAdd($"PeekUpdate/{appVersion}");
        }

        public static string GetAppVersion()
        {
            try
            {
                using var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Peek", false);
                if (key != null)
                {
                    object? ver = key.GetValue("DisplayVersion");
                    if (ver is string verStr && !string.IsNullOrWhiteSpace(verStr))
                    {
                        return verStr.Trim();
                    }
                }
            }
            catch { }

            var asmVer = Assembly.GetExecutingAssembly().GetName().Version;
            return asmVer != null ? $"{asmVer.Major}.{asmVer.Minor}.{asmVer.Build}" : "0.2.0";
        }

        public async Task<UpdateCheckResult> CheckForUpdatesAsync(bool isInteractive = false, CancellationToken cancellationToken = default)
        {
            var result = new UpdateCheckResult();
            string installedVerStr = GetAppVersion();
            result.InstalledVersion = installedVerStr;

            string channel = _policyManager.GetEffectiveChannel();
            result.Channel = channel;

            // 1. Check policy & settings (unless interactive --check --now)
            if (!isInteractive)
            {
                if (!_policyManager.GetEffectiveAutoCheck())
                {
                    result.Success = true;
                    result.ErrorMessage = "Automatic update checking is disabled by policy or user setting.";
                    return result;
                }

                if (_policyManager.IsSnoozed())
                {
                    result.Success = true;
                    result.ErrorMessage = "Update checking is currently snoozed.";
                    return result;
                }
            }

            // 2. Resolve manifest URL
            string feedBaseUrl = _policyManager.GetEffectiveFeedUrl();
            string manifestUrl = $"{feedBaseUrl.TrimEnd('/')}/{channel}.json";

            // 3. Network Fetch with ETag & Retry Backoff
            var userSettings = _policyManager.LoadUserSettings();
            string? currentEtag = userSettings.LastETag;

            HttpResponseMessage? response = null;
            string? responseContent = null;
            int maxRetries = 2;

            for (int attempt = 0; attempt <= maxRetries; attempt++)
            {
                try
                {
                    using var request = new HttpRequestMessage(HttpMethod.Get, manifestUrl);
                    if (!string.IsNullOrWhiteSpace(currentEtag) && !isInteractive)
                    {
                        request.Headers.IfNoneMatch.Add(new EntityTagHeaderValue(currentEtag));
                    }

                    response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseContentRead, cancellationToken);

                    if (response.StatusCode == HttpStatusCode.NotModified)
                    {
                        result.Success = true;
                        result.NotModified = true;
                        return result;
                    }

                    response.EnsureSuccessStatusCode();
                    responseContent = await response.Content.ReadAsStringAsync(cancellationToken);
                    break;
                }
                catch (HttpRequestException ex)
                {
                    if (attempt == maxRetries)
                    {
                        result.IsOffline = true;
                        result.ErrorMessage = $"Network request failed: {ex.Message}";
                        return result;
                    }
                    await Task.Delay(500 * (attempt + 1), cancellationToken);
                }
                catch (TaskCanceledException)
                {
                    result.IsOffline = true;
                    result.ErrorMessage = "Network request timed out (15s limit reached).";
                    return result;
                }
                catch (Exception ex)
                {
                    result.ErrorMessage = $"Unexpected network error: {ex.Message}";
                    return result;
                }
            }

            if (string.IsNullOrWhiteSpace(responseContent))
            {
                result.ErrorMessage = "Empty response received from update feed.";
                return result;
            }

            // Store new ETag if present
            if (response?.Headers.ETag != null)
            {
                userSettings.LastETag = response.Headers.ETag.Tag;
                _policyManager.SaveUserSettings(userSettings);
            }

            // 4. Parse & Validate Manifest
            UpdateManifest? manifest;
            try
            {
                manifest = JsonSerializer.Deserialize<UpdateManifest>(responseContent);
            }
            catch (Exception ex)
            {
                result.ErrorMessage = $"Failed to parse manifest JSON: {ex.Message}";
                return result;
            }

            if (manifest == null)
            {
                result.ErrorMessage = "Manifest deserialization produced null object.";
                return result;
            }

            result.Manifest = manifest;

            // 5. Strict Verification Pipeline
            string validationError = ValidateManifest(manifest, installedVerStr, feedBaseUrl);
            if (!string.IsNullOrEmpty(validationError))
            {
                result.ErrorMessage = validationError;
                return result;
            }

            // Check if user skipped this specific version
            if (!isInteractive && _policyManager.IsVersionSkipped(manifest.Version))
            {
                result.Success = true;
                result.ErrorMessage = $"Version {manifest.Version} is skipped by user preference.";
                return result;
            }

            result.Success = true;
            result.UpdateAvailable = true;
            return result;
        }

        public string ValidateManifest(UpdateManifest manifest, string installedVerStr, string feedBaseUrl)
        {
            // Gate 1: Unknown schema
            if (manifest.Schema != 1)
            {
                return $"Unsupported manifest schema version: {manifest.Schema}. Expected schema version 1.";
            }

            // Gate 2: ECDSA Signature Verification
            if (!_signatureVerifier.VerifyManifest(manifest))
            {
                return "Manifest ECDSA P-256 signature verification failed. Untrusted or tampered update manifest.";
            }

            // Gate 3: Expiration Check (Reject if expired or older than 30 days)
            if (string.IsNullOrWhiteSpace(manifest.Expires) || !DateTime.TryParse(manifest.Expires, out var expiresDate))
            {
                return "Manifest missing valid 'expires' timestamp.";
            }
            if (expiresDate.ToUniversalTime() < DateTime.UtcNow)
            {
                return $"Manifest expired on {manifest.Expires}. Updates must be verified within valid window.";
            }

            if (!string.IsNullOrWhiteSpace(manifest.Released) && DateTime.TryParse(manifest.Released, out var releaseDate))
            {
                if (DateTime.UtcNow - releaseDate.ToUniversalTime() > TimeSpan.FromDays(30))
                {
                    return $"Manifest release date {manifest.Released} is over 30 days old. Expired.";
                }
            }

            // Gate 4: Version Comparison (Must be strictly greater than installed version)
            if (!SemVersion.TryParse(manifest.Version, out var manifestVer) || manifestVer == null)
            {
                return $"Manifest specifies invalid SemVer: '{manifest.Version}'";
            }
            if (SemVersion.TryParse(installedVerStr, out var installedVer) && installedVer != null)
            {
                if (manifestVer <= installedVer)
                {
                    return $"Manifest version {manifest.Version} is not greater than installed version {installedVerStr}. Downgrade/reinstall rejected.";
                }
            }

            // Gate 5: URL Host Allow-list
            if (!Uri.TryCreate(manifest.Url, UriKind.Absolute, out var updateUri) || updateUri.Scheme != Uri.UriSchemeHttps)
            {
                return $"Manifest URL is invalid or not HTTPS: '{manifest.Url}'";
            }

            if (!IsHostAllowed(updateUri.Host, feedBaseUrl))
            {
                return $"Manifest download host '{updateUri.Host}' is not on the trusted allow-list.";
            }

            // Gate 6: Size Restrictions (Max 500 MB, positive size)
            if (manifest.Size <= 0 || manifest.Size > MaxAllowedSizeBytes)
            {
                return $"Manifest download size ({manifest.Size} bytes) is invalid or exceeds 500 MB limit.";
            }

            // Gate 7: Cryptographic Hash
            if (string.IsNullOrWhiteSpace(manifest.Sha256) || manifest.Sha256.Length != 64)
            {
                return $"Manifest specifies invalid SHA-256 hash: '{manifest.Sha256}'";
            }

            return string.Empty;
        }

        public static bool IsHostAllowed(string host, string configuredFeedUrl)
        {
            if (string.IsNullOrWhiteSpace(host)) return false;

            string cleanHost = host.Trim().ToLowerInvariant();

            // 1. Configured feed host
            if (Uri.TryCreate(configuredFeedUrl, UriKind.Absolute, out var feedUri))
            {
                if (string.Equals(cleanHost, feedUri.Host.ToLowerInvariant(), StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            // 2. Official GitHub Release hosts
            if (cleanHost == "github.com" ||
                cleanHost == "www.github.com" ||
                cleanHost == "objects.githubusercontent.com" ||
                cleanHost == "raw.githubusercontent.com")
            {
                return true;
            }

            return false;
        }
    }
}
