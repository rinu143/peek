using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using PeekSetup.Services;
using Xunit;

namespace PeekSetup.Tests
{
    public class MachineEntropyProviderTests
    {
        [Fact]
        public void GetMachineEntropy_UsesEnvironmentVariableOverride_WhenPresent()
        {
            string prev = Environment.GetEnvironmentVariable("PEEK_MACHINE_ENTROPY") ?? string.Empty;
            try
            {
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", "  custom-test-override-guid  ");
                byte[] entropy = MachineEntropyProvider.GetMachineEntropy();
                Assert.Equal(Encoding.UTF8.GetBytes("custom-test-override-guid"), entropy);
            }
            finally
            {
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", string.IsNullOrEmpty(prev) ? null : prev);
            }
        }

        [Fact]
        public void GetMachineEntropy_ThrowsFileNotFoundException_AndLogsTraceError_WhenFileMissing()
        {
            string prev = Environment.GetEnvironmentVariable("PEEK_MACHINE_ENTROPY") ?? string.Empty;
            string prevProgramData = Environment.GetEnvironmentVariable("ProgramData") ?? string.Empty;
            string tempProgramData = Path.Combine(Path.GetTempPath(), "PeekEmptyProgramData_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempProgramData);

            var traceListener = new TestTraceListener();
            Trace.Listeners.Add(traceListener);

            try
            {
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", null);
                Environment.SetEnvironmentVariable("ProgramData", tempProgramData);

                var ex = Assert.Throws<FileNotFoundException>(() => MachineEntropyProvider.GetMachineEntropy());
                Assert.Contains("Machine entropy file is missing", ex.Message);
                Assert.Contains(tempProgramData, ex.Message);

                // Verify log line was produced
                Assert.Contains(traceListener.Messages, m => m.Contains("Machine entropy file is missing"));
            }
            finally
            {
                Trace.Listeners.Remove(traceListener);
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", string.IsNullOrEmpty(prev) ? null : prev);
                Environment.SetEnvironmentVariable("ProgramData", string.IsNullOrEmpty(prevProgramData) ? null : prevProgramData);
                if (Directory.Exists(tempProgramData))
                {
                    Directory.Delete(tempProgramData, true);
                }
            }
        }

        [Fact]
        public void GetMachineEntropy_ReadsAndTrimsEntropyFile_WhenPresent()
        {
            string prev = Environment.GetEnvironmentVariable("PEEK_MACHINE_ENTROPY") ?? string.Empty;
            string prevProgramData = Environment.GetEnvironmentVariable("ProgramData") ?? string.Empty;
            string tempProgramData = Path.Combine(Path.GetTempPath(), "PeekValidProgramData_" + Guid.NewGuid().ToString("N"));
            string peekDir = Path.Combine(tempProgramData, "Peek");
            Directory.CreateDirectory(peekDir);
            string entropyFile = Path.Combine(peekDir, "machine_entropy.bin");
            File.WriteAllText(entropyFile, "  f81d4fae-7dec-11d0-a765-00a0c91e6bf6  \r\n", new UTF8Encoding(false));

            try
            {
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", null);
                Environment.SetEnvironmentVariable("ProgramData", tempProgramData);

                byte[] entropy = MachineEntropyProvider.GetMachineEntropy();
                Assert.Equal(Encoding.UTF8.GetBytes("f81d4fae-7dec-11d0-a765-00a0c91e6bf6"), entropy);
            }
            finally
            {
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", string.IsNullOrEmpty(prev) ? null : prev);
                Environment.SetEnvironmentVariable("ProgramData", string.IsNullOrEmpty(prevProgramData) ? null : prevProgramData);
                if (Directory.Exists(tempProgramData))
                {
                    Directory.Delete(tempProgramData, true);
                }
            }
        }

        [Fact]
        public void GetMachineEntropy_ThrowsInvalidOperationException_WhenFileIsEmpty()
        {
            string prev = Environment.GetEnvironmentVariable("PEEK_MACHINE_ENTROPY") ?? string.Empty;
            string prevProgramData = Environment.GetEnvironmentVariable("ProgramData") ?? string.Empty;
            string tempProgramData = Path.Combine(Path.GetTempPath(), "PeekEmptyFileProgramData_" + Guid.NewGuid().ToString("N"));
            string peekDir = Path.Combine(tempProgramData, "Peek");
            Directory.CreateDirectory(peekDir);
            string entropyFile = Path.Combine(peekDir, "machine_entropy.bin");
            File.WriteAllText(entropyFile, "    \r\n", new UTF8Encoding(false));

            var traceListener = new TestTraceListener();
            Trace.Listeners.Add(traceListener);

            try
            {
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", null);
                Environment.SetEnvironmentVariable("ProgramData", tempProgramData);

                var ex = Assert.Throws<InvalidOperationException>(() => MachineEntropyProvider.GetMachineEntropy());
                Assert.Contains("is empty", ex.Message);
                Assert.Contains(traceListener.Messages, m => m.Contains("is empty"));
            }
            finally
            {
                Trace.Listeners.Remove(traceListener);
                Environment.SetEnvironmentVariable("PEEK_MACHINE_ENTROPY", string.IsNullOrEmpty(prev) ? null : prev);
                Environment.SetEnvironmentVariable("ProgramData", string.IsNullOrEmpty(prevProgramData) ? null : prevProgramData);
                if (Directory.Exists(tempProgramData))
                {
                    Directory.Delete(tempProgramData, true);
                }
            }
        }

        private class TestTraceListener : TraceListener
        {
            public System.Collections.Generic.List<string> Messages { get; } = new();

            public override void Write(string? message)
            {
                if (message != null) Messages.Add(message);
            }

            public override void WriteLine(string? message)
            {
                if (message != null) Messages.Add(message);
            }

            public override void TraceEvent(TraceEventCache? eventCache, string source, TraceEventType eventType, int id, string? message)
            {
                if (message != null) Messages.Add(message);
            }
        }
    }
}
