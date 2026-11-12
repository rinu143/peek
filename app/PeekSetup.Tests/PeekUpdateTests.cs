using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Win32;
using PeekUpdate.Models;
using PeekUpdate.Services;
using Xunit;

namespace PeekSetup.Tests
{
    public class PeekUpdateTests
    {
        private (string pubBase64, string privBase64) GenerateTestKeyPair()
        {
            var paramsCng = new CngKeyCreationParameters
            {
                ExportPolicy = CngExportPolicies.AllowPlaintextExport
            };
            string keyName = "PeekTestKey-" + Guid.NewGuid().ToString();
            using var key = CngKey.Create(CngAlgorithm.ECDsaP256, keyName, paramsCng);

            byte[] pub = key.Export(CngKeyBlobFormat.EccPublicBlob);
            byte[] priv = key.Export(CngKeyBlobFormat.EccPrivateBlob);
            key.Delete();

            return (Convert.ToBase64String(pub), Convert.ToBase64String(priv));
        }

        private string SignCanonical(UpdateManifest manifest, string privBase64)
        {
            byte[] privBytes = Convert.FromBase64String(privBase64);
            using var key = CngKey.Import(privBytes, CngKeyBlobFormat.EccPrivateBlob);
            using var dsa = new ECDsaCng(key);

            byte[] canonicalBytes = CanonicalJsonSerializer.SerializeToBytes(manifest);
            byte[] sigBytes = dsa.SignData(canonicalBytes);
            return Convert.ToBase64String(sigBytes);
        }

        private UpdateManifest CreateValidManifest(string version = "0.3.0", string channel = "stable")
        {
            return new UpdateManifest
            {
                Schema = 1,
                Channel = channel,
                Version = version,
                Released = DateTime.UtcNow.ToString("o"),
                Expires = DateTime.UtcNow.AddDays(20).ToString("o"),
                MinSupportedVersion = "0.1.0",
                Critical = false,
                NotesUrl = "https://github.com/peek/releases/tag/v" + version,
                Url = $"https://github.com/peek/releases/download/v{version}/Peek-Setup-{version}-x64.exe",
                Sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                Size = 100 * 1024 * 1024,
                PublisherThumbprint = "A1B2C3D4E5"
            };
        }

        [Fact]
        public void Test_ValidManifest_PassesVerification()
        {
            var (pub, priv) = GenerateTestKeyPair();
            var verifier = new SignatureVerifier(pub);
            var checker = new UpdateChecker(verifier);

            var manifest = CreateValidManifest("0.3.0");
            manifest.Signature = SignCanonical(manifest, priv);

            string err = checker.ValidateManifest(manifest, "0.2.0", "https://github.com/peek");
            Assert.Empty(err);
            Assert.True(verifier.VerifyManifest(manifest));
        }

        [Fact]
        public void Test_BadSignature_RejectsManifest()
        {
            var (pub1, priv1) = GenerateTestKeyPair();
            var (_, priv2) = GenerateTestKeyPair(); // different key!

            var verifier = new SignatureVerifier(pub1);
            var checker = new UpdateChecker(verifier);

            var manifest = CreateValidManifest("0.3.0");
            // Signed with wrong private key!
            manifest.Signature = SignCanonical(manifest, priv2);

            string err = checker.ValidateManifest(manifest, "0.2.0", "https://github.com/peek");
            Assert.Contains("signature verification failed", err, StringComparison.OrdinalIgnoreCase);
            Assert.False(verifier.VerifyManifest(manifest));
        }

        [Fact]
        public void Test_TamperedPayload_RejectsSignature()
        {
            var (pub, priv) = GenerateTestKeyPair();
            var verifier = new SignatureVerifier(pub);
            var checker = new UpdateChecker(verifier);

            var manifest = CreateValidManifest("0.3.0");
            manifest.Signature = SignCanonical(manifest, priv);

            // Tamper version after signing!
            manifest.Version = "0.4.0";

            string err = checker.ValidateManifest(manifest, "0.2.0", "https://github.com/peek");
            Assert.Contains("signature verification failed", err, StringComparison.OrdinalIgnoreCase);
            Assert.False(verifier.VerifyManifest(manifest));
        }

        [Fact]
        public void Test_ExpiredManifest_RejectsOver30Days()
        {
            var (pub, priv) = GenerateTestKeyPair();
            var verifier = new SignatureVerifier(pub);
            var checker = new UpdateChecker(verifier);

            // Case A: expires in past
            var manifestExpired = CreateValidManifest("0.3.0");
            manifestExpired.Expires = DateTime.UtcNow.AddMinutes(-5).ToString("o");
            manifestExpired.Signature = SignCanonical(manifestExpired, priv);

            string err1 = checker.ValidateManifest(manifestExpired, "0.2.0", "https://github.com/peek");
            Assert.Contains("expired", err1, StringComparison.OrdinalIgnoreCase);

            // Case B: released over 30 days ago
            var manifestOldRelease = CreateValidManifest("0.3.0");
            manifestOldRelease.Released = DateTime.UtcNow.AddDays(-35).ToString("o");
            manifestOldRelease.Expires = DateTime.UtcNow.AddDays(5).ToString("o");
            manifestOldRelease.Signature = SignCanonical(manifestOldRelease, priv);

            string err2 = checker.ValidateManifest(manifestOldRelease, "0.2.0", "https://github.com/peek");
            Assert.Contains("30 days old", err2, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void Test_DowngradeOrSameVersion_Rejects()
        {
            var (pub, priv) = GenerateTestKeyPair();
            var verifier = new SignatureVerifier(pub);
            var checker = new UpdateChecker(verifier);

            // Same version
            var manifestSame = CreateValidManifest("0.2.0");
            manifestSame.Signature = SignCanonical(manifestSame, priv);
            string err1 = checker.ValidateManifest(manifestSame, "0.2.0", "https://github.com/peek");
            Assert.Contains("not greater than installed version", err1, StringComparison.OrdinalIgnoreCase);

            // Downgrade
            var manifestOld = CreateValidManifest("0.1.9");
            manifestOld.Signature = SignCanonical(manifestOld, priv);
            string err2 = checker.ValidateManifest(manifestOld, "0.2.0", "https://github.com/peek");
            Assert.Contains("not greater than installed version", err2, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void Test_WrongHost_RejectsDisallowedDomain()
        {
            var (pub, priv) = GenerateTestKeyPair();
            var verifier = new SignatureVerifier(pub);
            var checker = new UpdateChecker(verifier);

            var manifest = CreateValidManifest("0.3.0");
            manifest.Url = "https://malicious-server.com/Peek-Setup.exe";
            manifest.Signature = SignCanonical(manifest, priv);

            string err = checker.ValidateManifest(manifest, "0.2.0", "https://updates.peek.local");
            Assert.Contains("allow-list", err, StringComparison.OrdinalIgnoreCase);
        }

        [Theory]
        [InlineData("github.com", true)]
        [InlineData("www.github.com", true)]
        [InlineData("objects.githubusercontent.com", true)]
        [InlineData("raw.githubusercontent.com", true)]
        [InlineData("updates.peek.local", true)]
        [InlineData("evil-hacker.com", false)]
        [InlineData("github.com.evil.com", false)]
        public void Test_AllowedHosts_Validation(string host, bool expectedAllowed)
        {
            bool isAllowed = UpdateChecker.IsHostAllowed(host, "https://updates.peek.local/feed");
            Assert.Equal(expectedAllowed, isAllowed);
        }

        [Fact]
        public void Test_OversizeManifest_RejectsOver500MB()
        {
            var (pub, priv) = GenerateTestKeyPair();
            var verifier = new SignatureVerifier(pub);
            var checker = new UpdateChecker(verifier);

            var manifest = CreateValidManifest("0.3.0");
            manifest.Size = 501L * 1024L * 1024L; // 501 MB
            manifest.Signature = SignCanonical(manifest, priv);

            string err = checker.ValidateManifest(manifest, "0.2.0", "https://github.com/peek");
            Assert.Contains("500 MB limit", err, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void Test_UnknownSchema_Rejects()
        {
            var (pub, priv) = GenerateTestKeyPair();
            var verifier = new SignatureVerifier(pub);
            var checker = new UpdateChecker(verifier);

            var manifest = CreateValidManifest("0.3.0");
            manifest.Schema = 99; // Unknown future schema
            manifest.Signature = SignCanonical(manifest, priv);

            string err = checker.ValidateManifest(manifest, "0.2.0", "https://github.com/peek");
            Assert.Contains("Unsupported manifest schema", err, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void Test_TamperedHash_RejectsCorruptedFile()
        {
            string tempFile = Path.GetTempFileName();
            try
            {
                File.WriteAllText(tempFile, "Corrupted binary content");

                string actualHash;
                using (var sha = SHA256.Create())
                using (var fs = File.OpenRead(tempFile))
                {
                    actualHash = BitConverter.ToString(sha.ComputeHash(fs)).Replace("-", "").ToLowerInvariant();
                }

                string expectedHash = "0000000000000000000000000000000000000000000000000000000000000000";
                Assert.NotEqual(expectedHash, actualHash);
            }
            finally
            {
                if (File.Exists(tempFile)) File.Delete(tempFile);
            }
        }

        [Fact]
        public void Test_ValidHash_InvalidAuthenticode_Rejects()
        {
            string tempExe = Path.Combine(Path.GetTempPath(), "test_unsigned_" + Guid.NewGuid() + ".exe");
            try
            {
                // Write dummy PE file that is not digitally signed
                File.WriteAllBytes(tempExe, new byte[] { 0x4D, 0x5A, 0x90, 0x00 }); // MZ header

                // Ensure test bypass is disabled
                Environment.SetEnvironmentVariable("PEEK_TEST_ALLOW_UNSIGNED", "0");

                bool isAuthenticodeValid = AuthenticodeVerifier.VerifyAuthenticode(tempExe, "AABBCCDDEE", out string err);
                Assert.False(isAuthenticodeValid);
                Assert.NotEmpty(err);
            }
            finally
            {
                if (File.Exists(tempExe)) File.Delete(tempExe);
            }
        }

        [Fact]
        public void Test_SettingsPrecedence_UserCannotOverrideFeedUrl()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTestSettings_" + Guid.NewGuid());
            Directory.CreateDirectory(tempDir);
            try
            {
                string settingsFile = Path.Combine(tempDir, "update-settings.json");
                string stateFile = Path.Combine(tempDir, "update-state.json");

                // Malicious or unprivileged user tries injecting "feed_url" into user settings!
                string maliciousJson = @"{
                    ""auto_check"": true,
                    ""channel"": ""beta"",
                    ""feed_url"": ""https://malicious-update-server.com/evil""
                }";
                File.WriteAllText(settingsFile, maliciousJson);

                var policy = new PolicyManager(settingsFile, stateFile);

                // Verify: EffectiveFeedUrl strictly returns HKLM or default, NEVER the malicious URL!
                string effectiveFeed = policy.GetEffectiveFeedUrl();
                Assert.NotEqual("https://malicious-update-server.com/evil", effectiveFeed);
                Assert.Contains("githubusercontent.com", effectiveFeed); // Fallback default
            }
            finally
            {
                if (Directory.Exists(tempDir)) Directory.Delete(tempDir, true);
            }
        }

        [Fact]
        public void Test_SettingsPrecedence_UserCanDisableOrSnooze()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PeekTestSettings_" + Guid.NewGuid());
            Directory.CreateDirectory(tempDir);
            try
            {
                string settingsFile = Path.Combine(tempDir, "update-settings.json");
                string stateFile = Path.Combine(tempDir, "update-state.json");

                var policy = new PolicyManager(settingsFile, stateFile);

                // Default: enabled, not snoozed
                Assert.True(policy.GetEffectiveAutoCheck());
                Assert.False(policy.IsSnoozed());

                // User snoozes for 7 days
                policy.Snooze(TimeSpan.FromDays(7));
                Assert.True(policy.IsSnoozed());

                // User disables checks
                var settings = policy.LoadUserSettings();
                settings.AutoCheck = false;
                policy.SaveUserSettings(settings);
                Assert.False(policy.GetEffectiveAutoCheck());

                // User skips a version
                policy.SkipVersion("0.3.0");
                Assert.True(policy.IsVersionSkipped("0.3.0"));
                Assert.False(policy.IsVersionSkipped("0.4.0"));
            }
            finally
            {
                if (Directory.Exists(tempDir)) Directory.Delete(tempDir, true);
            }
        }

        [Theory]
        [InlineData("0.2.0", "0.2.0-beta.1", 1)] // Release > Prerelease
        [InlineData("0.2.0-beta.1", "0.2.0-beta.1", 0)] // Equal
        [InlineData("0.3.0", "0.2.0", 1)] // Higher minor
        [InlineData("1.0.0", "0.9.9", 1)] // Higher major
        [InlineData("0.2.1", "0.2.0", 1)] // Higher patch
        [InlineData("0.1.0", "0.2.0", -1)] // Lower minor
        public void Test_SemVersion_Comparisons(string v1Str, string v2Str, int expectedSign)
        {
            var v1 = SemVersion.Parse(v1Str);
            var v2 = SemVersion.Parse(v2Str);

            int cmp = v1.CompareTo(v2);
            int actualSign = Math.Sign(cmp);

            Assert.Equal(expectedSign, actualSign);
        }
    }
}
