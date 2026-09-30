using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using PeekSetup.Services;
using Xunit;

namespace PeekSetup.Tests
{
    public class PipeServerIntegrationTests
    {
        [Fact]
        public async Task LiveNamedPipe_ClientServerRoundtrip_StreamsFramesAndCompletes()
        {
            // Find python executable and repo root
            string repoRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", ".."));
            string scriptPath = Path.Combine(repoRoot, "app", "PeekSetup.Tests", "run_test_enrollment_server.py");

            var psi = new ProcessStartInfo
            {
                FileName = "python",
                Arguments = $"\"{scriptPath}\"",
                WorkingDirectory = repoRoot,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };

            using var process = Process.Start(psi);
            Assert.NotNull(process);

            var tcsReady = new TaskCompletionSource<bool>();
            process.OutputDataReceived += (_, e) =>
            {
                if (e.Data?.Contains("READY") == true)
                {
                    tcsReady.TrySetResult(true);
                }
            };
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            try
            {
                // Wait for python server to signal READY (up to 5s)
                var completedTask = await Task.WhenAny(tcsReady.Task, Task.Delay(5000));
                Assert.True(completedTask == tcsReady.Task, "Python test pipe server did not become ready in time.");

                var client = new EnrollmentPipeClient();
                bool receivedPreviewFrame = false;
                var tcsEnrollComplete = new TaskCompletionSource<string>();

                client.PreviewFrameReceived += (_, e) =>
                {
                    if (e.PreviewImage != null && !string.IsNullOrEmpty(e.TargetPose))
                    {
                        receivedPreviewFrame = true;
                    }
                };

                client.EnrollmentCompleted += (_, e) =>
                {
                    tcsEnrollComplete.TrySetResult(e.ProfileId);
                };

                // Connect to the test pipe
                await client.ConnectAsync("PeekEnrollmentTest", timeoutMs: 3000);
                Assert.True(client.IsConnected);

                // Start enrollment
                await client.StartEnrollmentAsync("Test CSharp User");

                // Wait for completion (MockRunner finishes after 3 frames)
                var enrollCompleted = await Task.WhenAny(tcsEnrollComplete.Task, Task.Delay(5000));
                Assert.True(enrollCompleted == tcsEnrollComplete.Task, "Enrollment did not complete across the live pipe in time.");

                string profileId = await tcsEnrollComplete.Task;
                Assert.False(string.IsNullOrEmpty(profileId));
                Assert.True(receivedPreviewFrame, "Should have received at least one PREVIEW_FRAME over named pipe");

                await client.DisconnectAsync();
            }
            finally
            {
                if (!process.HasExited)
                {
                    process.Kill(true);
                    await process.WaitForExitAsync();
                }
            }
        }
    }
}
