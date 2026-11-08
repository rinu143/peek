using System;
using System.Diagnostics;
using System.IO;

namespace PeekSetup.Services
{
    public interface IEngineProcessManager
    {
        bool IsEngineProcessRunning();
        bool StartEngineProcess();
    }

    public class EngineProcessManager : IEngineProcessManager
    {
        public bool IsEngineProcessRunning()
        {
            try
            {
                var processes = Process.GetProcessesByName("PeekEngine");
                return processes.Length > 0;
            }
            catch
            {
                return false;
            }
        }

        public bool StartEngineProcess()
        {
            if (IsEngineProcessRunning())
            {
                return true;
            }

            string? exe = FindEngineExecutable(out string? args, out string? workingDir);
            if (string.IsNullOrEmpty(exe))
            {
                return false;
            }

            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = exe,
                    Arguments = args ?? string.Empty,
                    WorkingDirectory = workingDir ?? string.Empty,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    UseShellExecute = false
                };

                Process.Start(psi);
                return true;
            }
            catch
            {
                return false;
            }
        }

        public static string? FindEngineExecutable(out string? arguments, out string? workingDirectory)
        {
            arguments = null;
            workingDirectory = null;

            string baseDir = AppDomain.CurrentDomain.BaseDirectory;

            // 1. Same directory as PeekSetup (e.g. Program Files or publish folder)
            string localExe = Path.Combine(baseDir, "PeekEngine.exe");
            if (File.Exists(localExe))
            {
                workingDirectory = baseDir;
                return localExe;
            }

            // 2. Program Files installation directory
            string progFiles = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Peek", "PeekEngine.exe");
            if (File.Exists(progFiles))
            {
                workingDirectory = Path.GetDirectoryName(progFiles);
                return progFiles;
            }

            // 3. Search relative build / packaging paths from baseDir
            string[] relativeExePaths = new[]
            {
                Path.Combine(baseDir, @"..\..\..\..\scripts\packaging\PeekEngine.exe"),
                Path.Combine(baseDir, @"..\..\..\..\dist\PeekEngine.exe"),
                Path.Combine(baseDir, @"..\..\..\scripts\packaging\PeekEngine.exe"),
                Path.Combine(baseDir, @"..\..\..\dist\PeekEngine.exe"),
                Path.Combine(baseDir, @"..\..\scripts\packaging\PeekEngine.exe"),
                Path.Combine(baseDir, @"..\scripts\packaging\PeekEngine.exe"),
                Path.Combine(Directory.GetCurrentDirectory(), @"scripts\packaging\PeekEngine.exe"),
                Path.Combine(Directory.GetCurrentDirectory(), @"dist\PeekEngine.exe")
            };

            foreach (var rel in relativeExePaths)
            {
                try
                {
                    string full = Path.GetFullPath(rel);
                    if (File.Exists(full))
                    {
                        workingDirectory = Path.GetDirectoryName(full);
                        return full;
                    }
                }
                catch { }
            }

            // 4. Fallback to python script if running from source in development
            string[] relativeScriptPaths = new[]
            {
                Path.Combine(baseDir, @"..\..\..\..\scripts\run_engine_service.py"),
                Path.Combine(baseDir, @"..\..\..\scripts\run_engine_service.py"),
                Path.Combine(baseDir, @"..\..\scripts\run_engine_service.py"),
                Path.Combine(baseDir, @"..\scripts\run_engine_service.py"),
                Path.Combine(Directory.GetCurrentDirectory(), @"scripts\run_engine_service.py")
            };

            foreach (var scriptRel in relativeScriptPaths)
            {
                try
                {
                    string fullScript = Path.GetFullPath(scriptRel);
                    if (File.Exists(fullScript))
                    {
                        string? parentDir = Path.GetDirectoryName(fullScript);
                        string repoRoot = parentDir != null ? Path.GetDirectoryName(parentDir) ?? baseDir : baseDir;
                        workingDirectory = repoRoot;
                        arguments = $"\"{fullScript}\"";
                        return "python";
                    }
                }
                catch { }
            }

            return null;
        }
    }
}
