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
      5. Configures a test-grade Scheduled Task to launch PeekEngine.exe at logon with normal (limited) privileges.
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
    Write-Host "    This installer must be run elevated to copy binaries to Program Files" -ForegroundColor Yellow
    Write-Host "    and register the Windows Credential Provider DLL." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    Please right-click 'install.bat' (or PowerShell) and choose 'Run as administrator'." -ForegroundColor Yellow
    Write-Host ""
    Pause-IfInteractive
    exit 1
}

$SourceDir = $PSScriptRoot
$InstallDir = Join-Path $env:ProgramFiles "Peek"

# Resolve repo root if running from within repository
$RepoRoot = $SourceDir
$curr = $SourceDir
while ($curr -and -not (Test-Path (Join-Path $curr "VERSION"))) {
    $parent = Split-Path -Parent $curr
    if ($parent -eq $curr) { break }
    $curr = $parent
}
if ($curr -and (Test-Path (Join-Path $curr "VERSION"))) {
    $RepoRoot = $curr
}

Write-Host "[*] Source Directory : $SourceDir"
Write-Host "[*] Target Directory : $InstallDir"
Write-Host ""

# 2. Resolve source files (supports extracted zip layout, scripts/packaging, and repo build layout)
$sourceMap = @{
    "PeekSetup.exe"              = $null
    "PeekCredentialProvider.dll" = $null
    "PeekUpdate.exe"             = $null
}

$candidateDirs = @(
    $SourceDir,
    (Join-Path $SourceDir "dist"),
    (Join-Path $SourceDir "engine"),
    (Join-Path $RepoRoot "dist"),
    (Join-Path $RepoRoot "build\Release"),
    (Join-Path $RepoRoot "credential_provider\build\Release"),
    (Join-Path $RepoRoot "credential_provider\x64\Release"),
    (Join-Path $RepoRoot "app\PeekSetup\bin\Release\net8.0-windows\win-x64\publish"),
    (Join-Path $RepoRoot "app\PeekSetup\bin\Release\net8.0-windows\publish"),
    (Join-Path $RepoRoot "app\PeekSetup\bin\Debug\net8.0-windows"),
    (Join-Path $RepoRoot "app\PeekUpdate\bin\Release\net8.0-windows\win-x64\publish"),
    (Join-Path $RepoRoot "app\PeekUpdate\bin\Release\net8.0\win-x64\publish"),
    (Join-Path $RepoRoot "app\PeekUpdate\bin\Debug\net8.0-windows\win-x64"),
    (Join-Path $RepoRoot "scripts\packaging")
)

foreach ($key in @($sourceMap.Keys)) {
    $found = @()
    foreach ($cand in $candidateDirs) {
        $testPath = Join-Path $cand $key
        if (Test-Path $testPath) {
            $found += Get-Item $testPath
        }
    }
    if ($found.Count -gt 0) {
        $newest = $found | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $sourceMap[$key] = $newest.FullName
    }
}

# Resolve PeekEngine (either one-DIR directory or single-file exe)
$engineDirCandidates = @(
    (Join-Path $SourceDir "engine"),
    (Join-Path $SourceDir "PeekEngine"),
    (Join-Path $SourceDir "dist\PeekEngine"),
    (Join-Path $RepoRoot "dist\PeekEngine")
)
$resolvedEngineDir = $engineDirCandidates | Where-Object { Test-Path (Join-Path $_ "PeekEngine.exe") } | Select-Object -First 1

$engineExeCandidates = @(
    (Join-Path $SourceDir "PeekEngine.exe"),
    (Join-Path $SourceDir "engine\PeekEngine.exe"),
    (Join-Path $SourceDir "dist\PeekEngine.exe"),
    (Join-Path $RepoRoot "dist\PeekEngine.exe"),
    (Join-Path $RepoRoot "scripts\packaging\PeekEngine.exe")
)
$resolvedEngineExe = $engineExeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $resolvedEngineDir -and -not $resolvedEngineExe) {
    Write-Host "[!] ERROR: Missing PeekEngine build artifact (checked dist\PeekEngine and PeekEngine.exe)" -ForegroundColor Red
    Pause-IfInteractive
    exit 1
}

# Mandatory artifacts: PeekSetup.exe and PeekCredentialProvider.dll (PeekUpdate is optional for test package)
$requiredArtifacts = @("PeekSetup.exe", "PeekCredentialProvider.dll")
$missing = @($requiredArtifacts | Where-Object { [string]::IsNullOrEmpty($sourceMap[$_]) })
if ($missing.Count -gt 0) {
    Write-Host "[!] ERROR: Missing required build artifacts:" -ForegroundColor Red
    foreach ($m in $missing) {
        Write-Host "    - $m" -ForegroundColor Red
    }
    Write-Host ""
    Pause-IfInteractive
    exit 1
}

if (-not $sourceMap["PeekUpdate.exe"]) {
    Write-Host "      [i] PeekUpdate.exe not present in build; update service will not be configured." -ForegroundColor Gray
}

# 3. Stop running instances of Peek processes
Write-Host "[1/5] Stopping any running Peek processes..." -ForegroundColor Cyan
Get-Process -Name "PeekEngine", "PeekSetup", "PeekUpdate" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        Write-Host "      Stopping existing $($_.Name) process (PID: $($_.Id))..."
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    } catch {}
}
Start-Sleep -Milliseconds 500

# 4. Create destination directory and copy files
Write-Host "[2/5] Copying binaries to $InstallDir..." -ForegroundColor Cyan
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

# Copy engine files into $InstallDir\engine
$targetEngineDir = Join-Path $InstallDir "engine"
if (-not (Test-Path $targetEngineDir)) {
    New-Item -ItemType Directory -Path $targetEngineDir -Force | Out-Null
}
if ($resolvedEngineDir) {
    Write-Host "      Copying engine files from $resolvedEngineDir..."
    Copy-Item -Path "$resolvedEngineDir\*" -Destination $targetEngineDir -Recurse -Force
} elseif ($resolvedEngineExe) {
    Write-Host "      Copying $resolvedEngineExe to $targetEngineDir..."
    Copy-Item -Path $resolvedEngineExe -Destination (Join-Path $targetEngineDir "PeekEngine.exe") -Force
}

foreach ($file in $sourceMap.Keys) {
    $src = $sourceMap[$file]
    if (-not $src) { continue }
    $dst = Join-Path $InstallDir $file
    Write-Host "      Copying $file (from $src)..."
    Copy-Item -Path $src -Destination $dst -Force
}

# 5. Register Credential Provider DLL
Write-Host "[3/5] Registering Credential Provider with Windows LogonUI..." -ForegroundColor Cyan
$dllPath = Join-Path $InstallDir "PeekCredentialProvider.dll"
$regProcess = Start-Process -FilePath "regsvr32.exe" -ArgumentList "/s `"$dllPath`"" -Wait -PassThru
if ($regProcess.ExitCode -ne 0) {
    Write-Host "[!] ERROR: Failed to register Peek Credential Provider DLL (regsvr32 returned exit code $($regProcess.ExitCode))." -ForegroundColor Red
    Pause-IfInteractive
    exit 1
}

$cpRegKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}"
if (Test-Path $cpRegKey) {
    Write-Host "      [OK] Credential Provider successfully registered in Windows registry." -ForegroundColor Green
} else {
    Write-Host "[!] ERROR: Credential Provider registry key not detected at $cpRegKey" -ForegroundColor Red
    Pause-IfInteractive
    exit 1
}

# Configure admin-only Peek registry settings
$peekRegKey = "HKLM:\SOFTWARE\Peek"
if (-not (Test-Path $peekRegKey)) {
    New-Item -Path $peekRegKey -Force | Out-Null
}
$engineExePath = if (Test-Path (Join-Path $targetEngineDir "PeekEngine.exe")) {
    Join-Path $targetEngineDir "PeekEngine.exe"
} else {
    Join-Path $InstallDir "PeekEngine.exe"
}
Set-ItemProperty -Path $peekRegKey -Name "InstallPath" -Value $engineExePath -Type String -Force
Set-ItemProperty -Path $peekRegKey -Name "LogLevel" -Value 1 -Type DWord -Force # 1 = ERROR, 0 = OFF, 2 = INFO, 3 = DEBUG
Remove-ItemProperty -Path $peekRegKey -Name "AutoSelect" -ErrorAction SilentlyContinue

$systemSid = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null)
$adminSid = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
$usersSid = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinUsersSid, $null)

try {
    # HKLM:\SOFTWARE\Peek must be Administrators/SYSTEM write only. Users ReadKey only.
    $regKeyObj = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey("SOFTWARE\Peek", [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, [System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadKey)
    if ($regKeyObj) {
        $regAcl = New-Object System.Security.AccessControl.RegistrySecurity
        $regAcl.SetAccessRuleProtection($true, $false)
        $regAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($systemSid, "FullControl", "ContainerInherit", "None", "Allow")))
        $regAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($adminSid, "FullControl", "ContainerInherit", "None", "Allow")))
        $regAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($usersSid, [System.Security.AccessControl.RegistryRights]::ReadKey, "ContainerInherit", "None", "Allow")))
        $regKeyObj.SetAccessControl($regAcl)
        $regKeyObj.Close()
    }
} catch {
    Write-Warning "Could not secure $peekRegKey ACL: $_"
}
Write-Host "      [OK] Configured HKLM:\SOFTWARE\Peek (Admin/SYSTEM write-only, InstallPath: $engineExePath, LogLevel: 1)." -ForegroundColor Green

# HKLM:\SOFTWARE\Peek\Prefs: Users may write (SetValue and ReadKey only)
$prefsRegKey = "HKLM:\SOFTWARE\Peek\Prefs"
if (-not (Test-Path $prefsRegKey)) {
    New-Item -Path $prefsRegKey -Force | Out-Null
}
try {
    $prefsKeyObj = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey("SOFTWARE\Peek\Prefs", [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, [System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadKey)
    if ($prefsKeyObj) {
        $prefsAcl = New-Object System.Security.AccessControl.RegistrySecurity
        $prefsAcl.SetAccessRuleProtection($true, $false)
        $prefsAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($systemSid, "FullControl", "ContainerInherit", "None", "Allow")))
        $prefsAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($adminSid, "FullControl", "ContainerInherit", "None", "Allow")))
        $prefsAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule(
            $usersSid,
            [System.Security.AccessControl.RegistryRights]::SetValue -bor [System.Security.AccessControl.RegistryRights]::ReadKey,
            "ContainerInherit",
            "None",
            "Allow"
        )))
        $prefsKeyObj.SetAccessControl($prefsAcl)
        $prefsKeyObj.Close()
    }
} catch {
    Write-Warning "Could not configure $prefsRegKey ACL: $_"
}
Set-ItemProperty -Path $prefsRegKey -Name "AutoSelect" -Value 1 -Type DWord -Force
Set-ItemProperty -Path $prefsRegKey -Name "ColdBootUnlock" -Value 1 -Type DWord -Force
Write-Host "      [OK] Configured HKLM:\SOFTWARE\Peek\Prefs (Users SetValue, AutoSelect: 1, ColdBootUnlock: 1)." -ForegroundColor Green

$updatesRegKey = "HKLM:\SOFTWARE\Peek\Updates"
if (-not (Test-Path $updatesRegKey)) {
    New-Item -Path $updatesRegKey -Force | Out-Null
}
if (-not (Get-ItemProperty -Path $updatesRegKey -Name "AutoCheck" -ErrorAction SilentlyContinue)) {
    Set-ItemProperty -Path $updatesRegKey -Name "AutoCheck" -Value 1 -Type DWord -Force
}
if (-not (Get-ItemProperty -Path $updatesRegKey -Name "Channel" -ErrorAction SilentlyContinue)) {
    Set-ItemProperty -Path $updatesRegKey -Name "Channel" -Value "stable" -Type String -Force
}
if (-not (Get-ItemProperty -Path $updatesRegKey -Name "FeedUrl" -ErrorAction SilentlyContinue)) {
    Set-ItemProperty -Path $updatesRegKey -Name "FeedUrl" -Value "https://rinu143.github.io/peek" -Type String -Force
}
Write-Host "      [OK] Configured HKLM:\SOFTWARE\Peek\Updates (AutoCheck, Channel, FeedUrl)." -ForegroundColor Green

# Configure secure Peek ProgramData and log directory ACL (SYSTEM/Administrators FullControl, Users ReadAndExecute)
$PeekDataDir = Join-Path $env:ProgramData "Peek"
if (-not (Test-Path $PeekDataDir)) {
    New-Item -ItemType Directory -Path $PeekDataDir -Force | Out-Null
}
$LogDir = Join-Path $PeekDataDir "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
try {
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null)
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
    $usersSid = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinUsersSid, $null)

    # 1. Peek ProgramData folder ACL: inheritance removed, explicit ACEs (SYSTEM FullControl, Admins FullControl, Users ReadAndExecute)
    $peekDirAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $peekDirAcl.SetAccessRuleProtection($true, $false)
    $peekDirAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $peekDirAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $peekDirAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usersSid, "ReadAndExecute", "ContainerInherit,ObjectInherit", "None", "Allow")))
    Set-Acl -Path $PeekDataDir -AclObject $peekDirAcl
    Write-Host "      [OK] Secured Peek ProgramData directory ACL at $PeekDataDir (SYSTEM/Admin Full, Users Read)." -ForegroundColor Green

    # Resolve virtual service account NT SERVICE\PeekEngine
    $svcAccount = $null
    try {
        $svcAccount = New-Object System.Security.Principal.NTAccount("NT SERVICE\PeekEngine")
    } catch { }

    # 1b. Create machine_entropy.bin if it does not exist, and apply protected ACL (SYSTEM/Admin/PeekEngine FullControl only, NO Users ACE)
    $entropyFilePath = Join-Path $PeekDataDir "machine_entropy.bin"
    if (-not (Test-Path $entropyFilePath)) {
        $entropyGuid = [Guid]::NewGuid().ToString()
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($entropyFilePath, $entropyGuid, $utf8NoBom)
        Write-Host "      [OK] Initialized machine entropy at $entropyFilePath." -ForegroundColor Green
    } else {
        Write-Host "      [OK] Preserving existing machine entropy at $entropyFilePath." -ForegroundColor Green
    }
    $entropyAcl = New-Object System.Security.AccessControl.FileSecurity
    $entropyAcl.SetAccessRuleProtection($true, $false)
    $entropyAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, "FullControl", "Allow")))
    $entropyAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, "FullControl", "Allow")))
    if ($svcAccount) {
        try {
            $entropyAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "Allow")))
        } catch { }
    }
    Set-Acl -Path $entropyFilePath -AclObject $entropyAcl
    Write-Host "      [OK] Secured machine_entropy.bin ACL at $entropyFilePath (SYSTEM/Admin Full, NO Users ACE)." -ForegroundColor Green

    # 2. If engine.json exists, apply the same explicit protected ACL
    $engineJsonPath = Join-Path $PeekDataDir "engine.json"
    if (Test-Path $engineJsonPath) {
        $jsonAcl = New-Object System.Security.AccessControl.FileSecurity
        $jsonAcl.SetAccessRuleProtection($true, $false)
        $jsonAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, "FullControl", "Allow")))
        $jsonAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, "FullControl", "Allow")))
        if ($svcAccount) {
            try { $jsonAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "Allow"))) } catch { }
        }
        $jsonAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usersSid, "ReadAndExecute", "Allow")))
        Set-Acl -Path $engineJsonPath -AclObject $jsonAcl
        Write-Host "      [OK] Secured engine.json ACL at $engineJsonPath (SYSTEM/Admin Full, Users Read)." -ForegroundColor Green
    }

    # 3. Log directory ACL
    $dirAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $dirAcl.SetAccessRuleProtection($true, $false)
    $dirAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $dirAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    if ($svcAccount) {
        try { $dirAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow"))) } catch { }
    }
    $dirAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usersSid, "ReadAndExecute", "ContainerInherit,ObjectInherit", "None", "Allow")))
    Set-Acl -Path $LogDir -AclObject $dirAcl
    Write-Host "      [OK] Secured log directory ACL at $LogDir (SYSTEM/Admin Full, Users Read)." -ForegroundColor Green

    # 4. State directory ACL (SYSTEM/Admin/PeekEngine FullControl, NO Users ACE)
    $StateDir = Join-Path $PeekDataDir "state"
    if (-not (Test-Path $StateDir)) {
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    }
    $stateAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $stateAcl.SetAccessRuleProtection($true, $false)
    $stateAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $stateAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    if ($svcAccount) {
        try { $stateAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow"))) } catch { }
    }
    Set-Acl -Path $StateDir -AclObject $stateAcl
    Write-Host "      [OK] Secured state directory ACL at $StateDir (SYSTEM/Admin Full, NO Users ACE)." -ForegroundColor Green

    # 5. Profiles directory ACL (SYSTEM/Admin/PeekEngine FullControl, NO Users ACE)
    $ProfilesDir = Join-Path $PeekDataDir "Profiles"
    if (-not (Test-Path $ProfilesDir)) {
        New-Item -ItemType Directory -Path $ProfilesDir -Force | Out-Null
    }
    $profilesAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $profilesAcl.SetAccessRuleProtection($true, $false)
    $profilesAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $profilesAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    if ($svcAccount) {
        try {
            $profilesAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
        } catch { }
    }
    Set-Acl -Path $ProfilesDir -AclObject $profilesAcl
    Write-Host "      [OK] Secured Profiles directory at $ProfilesDir (SYSTEM/Admin Full, NO Users ACE)." -ForegroundColor Green
} catch {
    Write-Warning "Could not configure Peek directory or log ACL: $_"
}

# 6. Configure Windows service under low-privilege virtual service account NT SERVICE\PeekEngine
Write-Host "[4/5] Configuring PeekEngine Windows service under NT SERVICE\PeekEngine..." -ForegroundColor Cyan
$engineExe = $engineExePath
$engineWorkingDir = Split-Path -Parent $engineExe

# Clean up legacy Scheduled Task and stale launcher entries
try {
    Unregister-ScheduledTask -TaskName "PeekEngine" -Confirm:$false -ErrorAction SilentlyContinue
} catch { }

try {
    Remove-ItemProperty -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "PeekEngine" -ErrorAction SilentlyContinue
} catch { }

try {
    Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "PeekEngine" -ErrorAction SilentlyContinue
} catch { }

try {
    $startupFolder = [Environment]::GetFolderPath("CommonStartup")
    if (Test-Path $startupFolder) {
        $shortcutPath = Join-Path $startupFolder "PeekEngine.lnk"
        if (Test-Path $shortcutPath) {
            Remove-Item $shortcutPath -Force -ErrorAction SilentlyContinue
        }
    }
} catch { }

try {
    $userStartupFolder = [Environment]::GetFolderPath("Startup")
    if (Test-Path $userStartupFolder) {
        $userShortcut = Join-Path $userStartupFolder "PeekEngine.lnk"
        if (Test-Path $userShortcut) {
            Remove-Item $userShortcut -Force -ErrorAction SilentlyContinue
        }
    }
} catch { }

# Register Windows Service PeekEngine
$serviceName = "PeekEngine"
$existingSvc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if (-not $existingSvc) {
    & sc.exe create $serviceName binPath= "`"$engineExe`" --service" start= auto obj= "NT SERVICE\PeekEngine" displayname= "Peek Face Recognition Engine" | Out-Null
} else {
    & sc.exe config $serviceName binPath= "`"$engineExe`" --service" start= auto obj= "NT SERVICE\PeekEngine" | Out-Null
}
& sc.exe failure $serviceName reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
Write-Host "      [OK] Windows Service '$serviceName' registered (starts at boot under NT SERVICE\PeekEngine)." -ForegroundColor Green
try {
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
} catch { }

# Register Peek in Windows Startup apps so it starts automatically on shutdown/reboot logon
Write-Host "      Registering Peek in Windows Startup apps..." -ForegroundColor Cyan
try {
    Set-ItemProperty -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Peek" -Value "`"$engineExe`"" -Type String -Force -ErrorAction SilentlyContinue
    Write-Host "      [OK] Registered Peek in Windows Startup apps (HKLM Run)." -ForegroundColor Green
} catch {
    try {
        Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Peek" -Value "`"$engineExe`"" -Type String -Force -ErrorAction SilentlyContinue
        Write-Host "      [OK] Registered Peek in user Startup apps (HKCU Run)." -ForegroundColor Green
    } catch { }
}

# Create Start Menu shortcut for Peek
$startMenuPeekDir = Join-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs" "Peek"
if (-not (Test-Path $startMenuPeekDir)) {
    New-Item -ItemType Directory -Path $startMenuPeekDir -Force | Out-Null
}
$legacyShortcut = Join-Path $startMenuPeekDir "Peek Setup.lnk"
if (Test-Path $legacyShortcut) {
    Remove-Item $legacyShortcut -Force -ErrorAction SilentlyContinue
}
$setupExe = Join-Path $InstallDir "PeekSetup.exe"
$setupShortcutPath = Join-Path $startMenuPeekDir "Peek.lnk"
$wsh = New-Object -ComObject WScript.Shell
$setupShortcut = $wsh.CreateShortcut($setupShortcutPath)
$setupShortcut.TargetPath = $setupExe
$setupShortcut.WorkingDirectory = $InstallDir
$setupShortcut.Description = "Peek"
$setupShortcut.IconLocation = "$InstallDir\PeekSetup.exe,0"
$setupShortcut.Save()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($setupShortcut) | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null
Write-Host "      [OK] Created Start Menu shortcut for Peek." -ForegroundColor Green

# 6b. Register user-level PeekUpdate scheduled task
$updateExe = Join-Path $InstallDir "PeekUpdate.exe"
if (Test-Path $updateExe) {
    Write-Host "      Registering user-level PeekUpdate scheduled task..." -ForegroundColor Cyan
    $regProc = Start-Process -FilePath $updateExe -ArgumentList "--register-task" -Wait -PassThru -NoNewWindow
    if ($regProc.ExitCode -eq 0) {
        Write-Host "      [OK] Scheduled Task 'PeekUpdate' registered." -ForegroundColor Green
    } else {
        Write-Warning "Failed to register PeekUpdate scheduled task (exit code $($regProc.ExitCode))."
    }
}

# 7. Start engine service now
if (-not $NoStartService) {
    Write-Host "[5/5] Starting PeekEngine service..." -ForegroundColor Cyan
    try {
        Start-Service -Name "PeekEngine" -ErrorAction Stop
        Start-Sleep -Seconds 1
        $svcStatus = Get-Service -Name "PeekEngine"
        Write-Host "      [OK] PeekEngine service status: $($svcStatus.Status)." -ForegroundColor Green
    } catch {
        Write-Host "      Notice: PeekEngine service could not be started directly ($($_)). Trying fallback start..." -ForegroundColor Yellow
        Start-Process -FilePath $engineExe -WorkingDirectory $InstallDir -WindowStyle Hidden
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
Pause-IfInteractive
