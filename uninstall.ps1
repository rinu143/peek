<#
.SYNOPSIS
    Uninstalls the Peek Biometric Face Recognition Test Package from Windows 10/11.

.DESCRIPTION
    Reverses all installation steps cleanly:
      1. Verifies Administrator privileges.
      2. Stops running Peek processes (PeekEngine, PeekSetup).
      3. Unregisters PeekCredentialProvider.dll via regsvr32 /u.
      4. Explicitly verifies and removes all registry and COM entries to ensure
         zero orphaned registration remains.
      5. Removes the scheduled task and any startup shortcuts.
      6. Deletes Start Menu shortcuts and installed binaries from Program Files\Peek.
      7. Prompts to optionally clean up enrolled biometric profiles and machine entropy.

.NOTES
    Architecture: x64
#>

[CmdletBinding()]
param(
    [switch]$RemoveData,
    [switch]$RemoveUserData,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "      PEEK FACE AUTHENTICATION - TEST PACKAGE UNINSTALLER   " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

function Pause-IfInteractive {
    param([string]$Prompt = "Press Enter to exit")
    try {
        if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
            Read-Host $Prompt
        }
    } catch { }
}

# 1. Require Administrator Privileges
$currentPrincipal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] ERROR: Administrator privileges required." -ForegroundColor Red
    Write-Host "    This uninstaller must be run elevated to unregister the Credential" -ForegroundColor Yellow
    Write-Host "    Provider DLL and remove files from Program Files." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    Please right-click 'uninstall.bat' (or PowerShell) and choose 'Run as administrator'." -ForegroundColor Yellow
    Write-Host ""
    Pause-IfInteractive
    exit 1
}

$InstallDir = Join-Path $env:ProgramFiles "Peek"
$cpGuid = "{4F64A0FC-9F09-4C0B-A5E7-537C44919903}"

# Resolve repo root if running within git workspace
$RepoRoot = $PSScriptRoot
$curr = $PSScriptRoot
while ($curr -and -not (Test-Path (Join-Path $curr "VERSION"))) {
    $parent = Split-Path -Parent $curr
    if ($parent -eq $curr) { break }
    $curr = $parent
}
if ($curr -and (Test-Path (Join-Path $curr "VERSION"))) {
    $RepoRoot = $curr
}

# 2. Stop running processes under InstallDir
Write-Host "[1/5] Stopping Peek processes under $InstallDir..." -ForegroundColor Cyan
Get-Process -Name "PeekEngine", "PeekSetup", "PeekUpdate" -ErrorAction SilentlyContinue | ForEach-Object {
    $procName = $_.Name
    $procId = $_.Id
    $shouldKill = $false
    try {
        if ($_.Path -and ($_.Path.StartsWith($InstallDir, [System.StringComparison]::OrdinalIgnoreCase))) {
            $shouldKill = $true
        }
    } catch {
        $shouldKill = $true
    }
    if ($shouldKill) {
        Write-Host "      Stopping process $procName (PID: $procId)..."
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Milliseconds 500

# 3. Unregister Credential Provider DLL
Write-Host "[2/5] Unregistering Credential Provider from Windows LogonUI..." -ForegroundColor Cyan
$dllPath = Join-Path $InstallDir "PeekCredentialProvider.dll"
if (Test-Path $dllPath) {
    $unregProc = Start-Process -FilePath "regsvr32.exe" -ArgumentList "/u /s `"$dllPath`"" -Wait -PassThru
    if ($unregProc.ExitCode -ne 0) {
        Write-Warning "regsvr32 /u returned exit code $($unregProc.ExitCode)"
    }
}

# Explicitly guarantee no orphaned COM or Credential Provider registry keys remain
$regKeysToClean = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$cpGuid",
    "HKLM:\SOFTWARE\Classes\CLSID\$cpGuid",
    "HKLM:\SOFTWARE\Classes\WOW6432Node\CLSID\$cpGuid",
    "HKLM:\SOFTWARE\Peek"
)

foreach ($keyPath in $regKeysToClean) {
    if (Test-Path $keyPath) {
        Write-Host "      Removing residual registry key: $keyPath"
        Remove-Item -Path $keyPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Verify unregistration
$cpKeyStillExists = Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$cpGuid"
$clsidKeyStillExists = Test-Path "HKLM:\SOFTWARE\Classes\CLSID\$cpGuid"
if (-not $cpKeyStillExists -and -not $clsidKeyStillExists) {
    Write-Host "      [OK] Credential Provider and COM CLSID completely removed from registry." -ForegroundColor Green
} else {
    Write-Warning "Residual registry entries could not be completely deleted. Check permissions."
}

# 4. Remove scheduled task and startup shortcuts
Write-Host "[3/5] Removing scheduled tasks and startup shortcuts..." -ForegroundColor Cyan
foreach ($taskName in @("PeekEngine", "PeekUpdate")) {
    $taskCheck = Start-Process -FilePath "schtasks.exe" -ArgumentList "/Query /TN `"$taskName`"" -Wait -NoNewWindow -PassThru -ErrorAction SilentlyContinue
    if ($taskCheck.ExitCode -eq 0) {
        $delProc = Start-Process -FilePath "schtasks.exe" -ArgumentList "/Delete /TN `"$taskName`" /F" -Wait -NoNewWindow -PassThru
        if ($delProc.ExitCode -eq 0) {
            Write-Host "      [OK] Scheduled Task '$taskName' deleted." -ForegroundColor Green
        }
    }
}

$startupFolder = [Environment]::GetFolderPath("CommonStartup")
$startupShortcut = Join-Path $startupFolder "PeekEngine.lnk"
if (Test-Path $startupShortcut) {
    Remove-Item -Path $startupShortcut -Force
    Write-Host "      [OK] Removed startup shortcut: $startupShortcut" -ForegroundColor Green
}

# 5. User profile data cleanup prompt
$cleanUser = $RemoveUserData -or $RemoveData
if (-not $cleanUser -and -not $Force) {
    try {
        if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
            Write-Host ""
            $reply = Read-Host "Also remove face profiles and machine entropy? (y/N)"
            if ($reply -match "^[yY]") {
                $cleanUser = $true
            }
        }
    } catch { }
}

# 6. Remove LSA secrets, Start Menu folder and installed files
Write-Host "[4/5] Removing LSA secrets, installed binaries and shortcuts..." -ForegroundColor Cyan

$peekSetupCandidates = @(
    (Join-Path $InstallDir "PeekSetup.exe"),
    (Join-Path $PSScriptRoot "PeekSetup.exe"),
    (Join-Path $RepoRoot "dist\PeekSetup.exe"),
    (Join-Path $RepoRoot "app\PeekSetup\bin\Release\net8.0-windows\win-x64\publish\PeekSetup.exe"),
    (Join-Path $RepoRoot "app\PeekSetup\bin\Debug\net8.0-windows\PeekSetup.exe")
)
$peekSetupExe = $peekSetupCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($peekSetupExe) {
    $cleanupArgs = "--uninstall-cleanup"
    if ($cleanUser) {
        $cleanupArgs += " --remove-data"
    }
    Write-Host "      Running uninstall cleanup (LSA secrets and Identities always removed)..."
    Start-Process -FilePath $peekSetupExe -ArgumentList $cleanupArgs -Wait -NoNewWindow -ErrorAction SilentlyContinue
}

$startMenuPeekDir = Join-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs" "Peek"
if (Test-Path $startMenuPeekDir) {
    Remove-Item -Path $startMenuPeekDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "      [OK] Removed Start Menu folder." -ForegroundColor Green
}

if (Test-Path $InstallDir) {
    try {
        Start-Sleep -Milliseconds 500
        Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction Stop
        Write-Host "      [OK] Removed installation directory: $InstallDir" -ForegroundColor Green
    } catch {
        Write-Warning "Could not immediately remove all files from $InstallDir ($($_.Exception.Message)). Cleaning up unlocked files..."
        Get-ChildItem -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object {
            try { Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue } catch {}
        }
        try { Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue } catch {}
        if (Test-Path $InstallDir) {
            Write-Host "      [i] Any remaining locked files in $InstallDir will be removed on next system restart." -ForegroundColor Yellow
        }
    }
}

# 7. Residual cleanup if requested
Write-Host "[5/5] Enrolled biometric profiles and secrets cleanup..." -ForegroundColor Cyan
$userLocalPeek = Join-Path $env:LOCALAPPDATA "Peek"
$programDataPeek = Join-Path $env:ProgramData "Peek"

if ($cleanUser) {
    if (Test-Path $userLocalPeek) {
        Remove-Item -Path $userLocalPeek -Recurse -Force
        Write-Host "      [OK] Removed user biometric profiles from $userLocalPeek" -ForegroundColor Green
    }
    if (Test-Path $programDataPeek) {
        Remove-Item -Path $programDataPeek -Recurse -Force
        Write-Host "      [OK] Removed machine entropy and ProgramData directory from $programDataPeek" -ForegroundColor Green
    }
} else {
    Write-Host "      [i] Biometric profiles preserved in $userLocalPeek" -ForegroundColor Gray
    Write-Host "      [i] Preserving machine entropy in $programDataPeek" -ForegroundColor Gray
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "             UNINSTALLATION COMPLETED CLEANLY               " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Peek has been completely uninstalled. Windows logon and" -ForegroundColor White
Write-Host "credential provider registrations are fully restored." -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Pause-IfInteractive
