using System;
using System.Diagnostics;
using System.IO;
using System.Text;

namespace PeekSetup.Services
{
    /// <summary>
    /// Provides machine-bound entropy for DPAPI profile protection.
    /// In PeekSetup, entropy access is read-only.
    /// Reads from %ProgramData%\Peek\machine_entropy.bin or PEEK_MACHINE_ENTROPY test override.
    /// </summary>
    public static class MachineEntropyProvider
    {
        public static byte[] GetMachineEntropy()
        {
            string? envEntropy = Environment.GetEnvironmentVariable("PEEK_MACHINE_ENTROPY");
            if (!string.IsNullOrWhiteSpace(envEntropy))
            {
                return Encoding.UTF8.GetBytes(envEntropy.Trim());
            }

            string programData = Environment.GetEnvironmentVariable("ProgramData") ?? @"C:\ProgramData";
            string entropyPath = Path.Combine(programData, "Peek", "machine_entropy.bin");

            if (!File.Exists(entropyPath))
            {
                string msg = $"Machine entropy file is missing at '{entropyPath}'. Biometric profile operations cannot proceed.";
                Trace.TraceError(msg);
                throw new FileNotFoundException(msg, entropyPath);
            }

            try
            {
                string text = File.ReadAllText(entropyPath, Encoding.UTF8).Trim();
                if (string.IsNullOrWhiteSpace(text))
                {
                    string msg = $"Machine entropy file at '{entropyPath}' is empty. Biometric profile operations cannot proceed.";
                    Trace.TraceError(msg);
                    throw new InvalidOperationException(msg);
                }
                return Encoding.UTF8.GetBytes(text);
            }
            catch (Exception ex) when (ex is not FileNotFoundException && ex is not InvalidOperationException)
            {
                string msg = $"Could not read machine entropy file at '{entropyPath}': {ex.Message}";
                Trace.TraceError(msg);
                throw new InvalidOperationException(msg, ex);
            }
        }
    }
}
