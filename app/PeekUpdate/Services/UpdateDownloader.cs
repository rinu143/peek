using System;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public class UpdateDownloader
    {
        private readonly HttpClient _httpClient;

        public UpdateDownloader(HttpClient? httpClient = null)
        {
            _httpClient = httpClient ?? new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
        }

        public async Task<string> DownloadAndVerifyAsync(
            UpdateManifest manifest,
            Action<long, long>? progressCallback = null,
            CancellationToken cancellationToken = default)
        {
            if (manifest == null) throw new ArgumentNullException(nameof(manifest));

            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string updateDir = Path.Combine(localAppData, "Peek", "updates", manifest.Version);
            if (!Directory.Exists(updateDir))
            {
                Directory.CreateDirectory(updateDir);
            }

            string installerFileName = $"Peek-Setup-{manifest.Version}-x64.exe";
            string targetPath = Path.Combine(updateDir, installerFileName);
            string tempPath = targetPath + ".downloading";

            if (File.Exists(tempPath)) File.Delete(tempPath);

            // 1. Download Stream
            using (var response = await _httpClient.GetAsync(manifest.Url, HttpCompletionOption.ResponseHeadersRead, cancellationToken))
            {
                response.EnsureSuccessStatusCode();

                long totalBytes = response.Content.Headers.ContentLength ?? manifest.Size;
                long bytesReadTotal = 0;

                using var contentStream = await response.Content.ReadAsStreamAsync(cancellationToken);
                using var fileStream = new FileStream(tempPath, FileMode.Create, FileAccess.Write, FileShare.None, 81920, true);

                byte[] buffer = new byte[81920];
                int bytesRead;

                while ((bytesRead = await contentStream.ReadAsync(buffer, 0, buffer.Length, cancellationToken)) > 0)
                {
                    await fileStream.WriteAsync(buffer, 0, bytesRead, cancellationToken);
                    bytesReadTotal += bytesRead;
                    progressCallback?.Invoke(bytesReadTotal, totalBytes);
                }
            }

            // 2. Verify Size
            var fi = new FileInfo(tempPath);
            if (manifest.Size > 0 && fi.Length != manifest.Size)
            {
                File.Delete(tempPath);
                throw new InvalidOperationException($"Downloaded file size ({fi.Length} bytes) does not match manifest size ({manifest.Size} bytes). File discarded.");
            }

            // 3. Verify SHA-256
            string computedHash;
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(tempPath))
            {
                byte[] hashBytes = await sha.ComputeHashAsync(stream, cancellationToken);
                computedHash = BitConverter.ToString(hashBytes).Replace("-", "").ToLowerInvariant();
            }

            string cleanExpectedHash = manifest.Sha256.Trim().ToLowerInvariant();
            if (!string.Equals(computedHash, cleanExpectedHash, StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(tempPath);
                throw new InvalidOperationException($"Cryptographic SHA-256 mismatch! Expected: {cleanExpectedHash}, Computed: {computedHash}. Tampered or corrupted download discarded.");
            }

            // Move to final path
            if (File.Exists(targetPath)) File.Delete(targetPath);
            File.Move(tempPath, targetPath);

            return targetPath;
        }
    }
}
