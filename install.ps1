<#
.SYNOPSIS
    Installs the Peek Biometric Face Recognition Test Package on Windows 10/11.

.DESCRIPTION
    This script is explicitly a TEST-GRADE installer designed for evaluation.
    It performs the following steps:
      1. Verifies Administrator privileges (mandatory).
      2. Terminates any running PeekEngine or PeekSetup processes.
      3. Copies PeekEngine.exe, PeekSetup.exe, and PeekCredentialProvider.dll to "$env:ProgramFiles\Peek".
      4. Registers PeekCredentialProvider.dll with Windows LogonUI via regsvr32.
      5. Configures a test-grade Scheduled Task to launch PeekEngine.exe at logon with highest privileges.
         (NOTE: In a production release, a native Windows Service would be used instead).
      6. Creates Start Menu shortcuts for convenient access.
      7. Launches PeekEngine.exe so testing can begin immediately without re-logging.

.NOTES
    Architecture: x64
    OS: Windows 10 / Windows 11
    Test Build: Unsigned test packaging.
#>

[CmdletBinding()]
param(
    [switch]$NoStartService
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       PEEK FACE AUTHENTICATION - TEST PACKAGE INSTALLER    " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Require Administrator Privileges
$currentPrincipal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] ERROR: Administrator privileges required." -ForegroundColor Red
    Write-Host "    This installer must be run elevated to copy binaries to Program Files" -ForegroundColor Yellow
    Write-Host "    and register the Windows Credential Provider DLL." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    Please right-click 'install.bat' (or PowerShell) and choose 'Run as administrator'." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

$SourceDir = $PSScriptRoot
$InstallDir = Join-Path $env:ProgramFiles "Peek"

Write-Host "[*] Source Directory : $SourceDir"
Write-Host "[*] Target Directory : $InstallDir"
Write-Host ""

# 2. Resolve source files (supports both extracted zip layout and repo build layout)
$sourceMap = @{
    "PeekEngine.exe"             = $null
    "PeekSetup.exe"              = $null
    "PeekCredentialProvider.dll" = $null
}

$candidateDirs = @(
    $SourceDir,
    (Join-Path $SourceDir "dist"),
    (Join-Path $SourceDir "app\PeekSetup\bin\Release\net8.0-windows\win-x64\publish"),
    (Join-Path $SourceDir "credential_provider\build\Release"),
    (Join-Path $SourceDir "..\dist"),
    (Join-Path $SourceDir "..\app\PeekSetup\bin\Release\net8.0-windows\win-x64\publish"),
    (Join-Path $SourceDir "..\credential_provider\build\Release")
)

foreach ($key in @($sourceMap.Keys)) {
    foreach ($cand in $candidateDirs) {
        $testPath = Join-Path $cand $key
        if (Test-Path $testPath) {
            $sourceMap[$key] = (Resolve-Path $testPath).Path
            break
        }
    }
}

$missing = @($sourceMap.Keys | Where-Object { [string]::IsNullOrEmpty($sourceMap[$_]) })
if ($missing.Count -gt 0) {
    Write-Host "[!] ERROR: Missing required build artifacts:" -ForegroundColor Red
    foreach ($m in $missing) {
        Write-Host "    - $m" -ForegroundColor Red
    }
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# 3. Stop running instances of Peek processes
Write-Host "[1/5] Stopping any running Peek processes..." -ForegroundColor Cyan
Get-Process -Name "PeekEngine" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "      Stopping existing PeekEngine process (PID: $($_.Id))..."
    Stop-Process -Id $_.Id -Force
}
Get-Process -Name "PeekSetup" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "      Stopping existing PeekSetup process (PID: $($_.Id))..."
    Stop-Process -Id $_.Id -Force
}
Start-Sleep -Milliseconds 500

# 4. Create destination directory and copy files
Write-Host "[2/5] Copying binaries to $InstallDir..." -ForegroundColor Cyan
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

foreach ($file in $sourceMap.Keys) {
    $src = $sourceMap[$file]
    $dst = Join-Path $InstallDir $file
    Write-Host "      Copying $file (from $src)..."
    Copy-Item -Path $src -Destination $dst -Force
}

# 5. Register Credential Provider DLL
Write-Host "[3/5] Registering Credential Provider with Windows LogonUI..." -ForegroundColor Cyan
$dllPath = Join-Path $InstallDir "PeekCredentialProvider.dll"
$regProcess = Start-Process -FilePath "regsvr32.exe" -ArgumentList "/s `"$dllPath`"" -Wait -PassThru
if ($regProcess.ExitCode -ne 0) {
    Write-Warning "regsvr32 returned exit code $($regProcess.ExitCode)"
}

$cpRegKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}"
if (Test-Path $cpRegKey) {
    Write-Host "      [OK] Credential Provider successfully registered in Windows registry." -ForegroundColor Green
} else {
    Write-Host "      [!] WARNING: Credential Provider registry key not detected at $cpRegKey" -ForegroundColor Yellow
}

# 6. Configure test-grade logon auto-start
Write-Host "[4/5] Configuring test-grade logon startup task..." -ForegroundColor Cyan
# -----------------------------------------------------------------------------
# TEST-GRADE SCHEDULED TASK NOTE:
# This logon scheduled task is explicitly test-grade packaging to simplify testing.
# In a real production release:
#   - PeekEngine would be implemented as a proper native Windows Service
#     (running under a dedicated service account or SYSTEM),
#   - Camera acquisition would use session-isolated brokers or Windows Hello drivers,
#   - Installation and lifecycle management would be handled by an MSI/MSIX package.
# -----------------------------------------------------------------------------
$taskName = "PeekEngine"
$engineExe = Join-Path $InstallDir "PeekEngine.exe"

$schtasksArgs = @(
    "/Create",
    "/TN", $taskName,
    "/TR", "`"$engineExe`"",
    "/SC", "ONLOGON",
    "/RL", "HIGHEST",
    "/F"
)
$schtasksProc = Start-Process -FilePath "schtasks.exe" -ArgumentList $schtasksArgs -Wait -NoNewWindow -PassThru
if ($schtasksProc.ExitCode -eq 0) {
    Write-Host "      [OK] Scheduled Task '$taskName' created (starts at logon)." -ForegroundColor Green
} else {
    Write-Warning "schtasks returned exit code $($schtasksProc.ExitCode). Creating Startup folder shortcut as fallback..."
    $startupFolder = [Environment]::GetFolderPath("CommonStartup")
    $shortcutPath = Join-Path $startupFolder "PeekEngine.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $engineExe
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.Description = "Peek Face Recognition Engine (Logon Startup)"
    $shortcut.Save()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null
    Write-Host "      [OK] Created startup shortcut in $shortcutPath" -ForegroundColor Green
}

# Create Start Menu shortcut for PeekSetup
$startMenuPeekDir = Join-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs" "Peek"
if (-not (Test-Path $startMenuPeekDir)) {
    New-Item -ItemType Directory -Path $startMenuPeekDir -Force | Out-Null
}
$setupExe = Join-Path $InstallDir "PeekSetup.exe"
$setupShortcutPath = Join-Path $startMenuPeekDir "Peek Setup.lnk"
$wsh = New-Object -ComObject WScript.Shell
$setupShortcut = $wsh.CreateShortcut($setupShortcutPath)
$setupShortcut.TargetPath = $setupExe
$setupShortcut.WorkingDirectory = $InstallDir
$setupShortcut.Description = "Peek Biometric Enrollment & Setup"
$setupShortcut.Save()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($setupShortcut) | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null
Write-Host "      [OK] Created Start Menu shortcut for Peek Setup." -ForegroundColor Green

# 7. Start engine service now
if (-not $NoStartService) {
    Write-Host "[5/5] Starting PeekEngine service in background..." -ForegroundColor Cyan
    Start-Process -FilePath $engineExe -WorkingDirectory $InstallDir
    Start-Sleep -Seconds 1
    $runningEngine = Get-Process -Name "PeekEngine" -ErrorAction SilentlyContinue
    if ($runningEngine) {
        Write-Host "      [OK] PeekEngine is active and listening (PID: $($runningEngine.Id))." -ForegroundColor Green
    } else {
        Write-Host "      [!] Notice: PeekEngine did not stay in foreground. Check Event Viewer or run directly." -ForegroundColor Yellow
    }
} else {
    Write-Host "[5/5] Skipped starting service (-NoStartService specified)." -ForegroundColor Cyan
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "             INSTALLATION COMPLETE (TEST BUILD)             " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "HOW TO TRY PEEK:" -ForegroundColor White
Write-Host "  1. Launch Peek Setup:" -ForegroundColor White
Write-Host "     - From Start Menu: 'Peek Setup', or" -ForegroundColor Gray
Write-Host "     - Run: `"$InstallDir\PeekSetup.exe`"" -ForegroundColor Gray
Write-Host "  2. Complete face enrollment (5 guided poses)." -ForegroundColor White
Write-Host "  3. Link your Windows password (optional; stores DPAPI-wrapped secret" -ForegroundColor White
Write-Host "     for automatic workstation unlock)." -ForegroundColor Gray
Write-Host "  4. Lock your PC: Press Windows + L." -ForegroundColor White
Write-Host "  5. Look at the camera on the lock screen to unlock via Peek!" -ForegroundColor White
Write-Host ""
Write-Host "NOTE ON FAIL-OPEN SECURITY:" -ForegroundColor Yellow
Write-Host "  If the camera is disconnected or face is not recognized, you can always" -ForegroundColor Yellow
Write-Host "  click your regular password/PIN tile to sign in normally." -ForegroundColor Yellow
Write-Host ""
Write-Host "HOW TO UNINSTALL:" -ForegroundColor White
Write-Host "  Run 'uninstall.bat' (as Administrator) to fully remove Peek." -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to exit"
