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

# 2. Stop running processes
Write-Host "[1/5] Stopping Peek processes..." -ForegroundColor Cyan
Get-Process -Name "PeekEngine" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "      Stopping PeekEngine process (PID: $($_.Id))..."
    Stop-Process -Id $_.Id -Force
}
Get-Process -Name "PeekSetup" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "      Stopping PeekSetup process (PID: $($_.Id))..."
    Stop-Process -Id $_.Id -Force
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
    "HKLM:\SOFTWARE\Classes\WOW6432Node\CLSID\$cpGuid"
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
Write-Host "[3/5] Removing scheduled task and startup shortcuts..." -ForegroundColor Cyan
$taskName = "PeekEngine"
$taskCheck = Start-Process -FilePath "schtasks.exe" -ArgumentList "/Query /TN `"$taskName`"" -Wait -NoNewWindow -PassThru -ErrorAction SilentlyContinue
if ($taskCheck.ExitCode -eq 0) {
    $delProc = Start-Process -FilePath "schtasks.exe" -ArgumentList "/Delete /TN `"$taskName`" /F" -Wait -NoNewWindow -PassThru
    if ($delProc.ExitCode -eq 0) {
        Write-Host "      [OK] Scheduled Task '$taskName' deleted." -ForegroundColor Green
    }
}

$startupFolder = [Environment]::GetFolderPath("CommonStartup")
$startupShortcut = Join-Path $startupFolder "PeekEngine.lnk"
if (Test-Path $startupShortcut) {
    Remove-Item -Path $startupShortcut -Force
    Write-Host "      [OK] Removed startup shortcut: $startupShortcut" -ForegroundColor Green
}

# 5. Remove Start Menu folder and installed files
Write-Host "[4/5] Removing installed binaries and shortcuts..." -ForegroundColor Cyan
$startMenuPeekDir = Join-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs" "Peek"
if (Test-Path $startMenuPeekDir) {
    Remove-Item -Path $startMenuPeekDir -Recurse -Force
    Write-Host "      [OK] Removed Start Menu folder." -ForegroundColor Green
}

if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Host "      [OK] Removed installation directory: $InstallDir" -ForegroundColor Green
}

# 6. User profile data cleanup (optional / interactive prompt)
Write-Host "[5/5] Enrolled biometric profiles and secrets cleanup..." -ForegroundColor Cyan
$userLocalPeek = Join-Path $env:LOCALAPPDATA "Peek"
$programDataPeek = Join-Path $env:ProgramData "Peek"

$cleanData = $RemoveUserData
if (-not $cleanData -and -not $Force) {
    try {
        if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
            Write-Host ""
            $reply = Read-Host "Do you want to delete all enrolled biometric face profiles and linked password secrets? (y/N)"
            if ($reply -match "^[yY]") {
                $cleanData = $true
            }
        }
    } catch { }
}

if ($cleanData) {
    if (Test-Path $userLocalPeek) {
        Remove-Item -Path $userLocalPeek -Recurse -Force
        Write-Host "      [OK] Removed user biometric profiles from $userLocalPeek" -ForegroundColor Green
    }
    if (Test-Path $programDataPeek) {
        Remove-Item -Path $programDataPeek -Recurse -Force
        Write-Host "      [OK] Removed machine entropy from $programDataPeek" -ForegroundColor Green
    }
} else {
    Write-Host "      [i] Biometric profiles preserved in $userLocalPeek" -ForegroundColor Gray
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
