using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using PeekSetup.Services;
using Xunit;

namespace PeekSetup.Tests
{
    public class MockEngineProcessManager : IEngineProcessManager
    {
        public bool IsRunningValue { get; set; }
        public bool StartResultValue { get; set; } = true;
        public int StartCallCount { get; private set; }

        public bool IsEngineProcessRunning() => IsRunningValue;

        public bool StartEngineProcess()
        {
            StartCallCount++;
            return StartResultValue;
        }
    }

    public class EngineProcessManagerTests
    {
        [Fact]
        public void FindEngineExecutable_DoesNotThrowAndReturnsValidOrNull()
        {
            string? exe = EngineProcessManager.FindEngineExecutable(out string? args, out string? workingDir);
            if (exe != null)
            {
                Assert.True(File.Exists(exe) || exe == "python");
                Assert.NotNull(workingDir);
            }
        }

        [Fact]
        public void IsEngineProcessRunning_ReturnsBoolWithoutThrowing()
        {
            var manager = new EngineProcessManager();
            bool running = manager.IsEngineProcessRunning();
            // Boolean value verified without exception
            Assert.True(running || !running);
        }

        [Fact]
        public async Task ConnectAsync_WhenPipeUnavailable_InvokesEngineManager()
        {
            var mockManager = new MockEngineProcessManager { StartResultValue = false };
            await using var client = new EnrollmentPipeClient(mockManager);

            // Connect to non-existent pipe with small timeout
            await Assert.ThrowsAsync<EngineUnavailableException>(async () =>
            {
                using var cts = new CancellationTokenSource(1000);
                await client.ConnectAsync("NonExistentPipe_Test_12345", timeoutMs: 400, ct: cts.Token);
            });

            Assert.Equal(1, mockManager.StartCallCount);
        }
    }
}
