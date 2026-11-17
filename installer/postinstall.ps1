<#
.SYNOPSIS
    Peek Inno Setup Post-Installation Script
.DESCRIPTION
    Configures ProgramData directory security ACLs, generates initial machine_entropy.bin
    if absent, hardens registry permissions, and registers the PeekEngine Windows service
    under the low-privilege virtual service account NT SERVICE\PeekEngine.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"

try {
    Write-Host "[*] Configuring Peek directories and ACLs..."
    $DataDir = Join-Path $env:ProgramData "Peek"
    $LogDir = Join-Path $DataDir "logs"
    $StateDir = Join-Path $DataDir "state"
    $ProfilesDir = Join-Path $DataDir "Profiles"

    foreach ($dir in @($DataDir, $LogDir, $StateDir, $ProfilesDir)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }

    $sys = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null)
    $adm = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
    $usr = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinUsersSid, $null)

    # 1. Base ProgramData\Peek folder ACL: SYSTEM & Admins FullControl, Users ReadAndExecute
    $baseAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $baseAcl.SetAccessRuleProtection($true, $false)
    $baseAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $baseAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $baseAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usr, "ReadAndExecute", "ContainerInherit,ObjectInherit", "None", "Allow")))
    Set-Acl -Path $DataDir -AclObject $baseAcl
    Set-Acl -Path $LogDir -AclObject $baseAcl

    # 2. Virtual service account NT SERVICE\PeekEngine configuration
    $engineExe = Join-Path $InstallDir "engine\PeekEngine.exe"
    if (-not (Test-Path $engineExe)) {
        $engineExe = Join-Path $InstallDir "PeekEngine.exe"
    }
    $engineDir = Split-Path -Parent $engineExe

    # Register/Update Windows Service PeekEngine under NT SERVICE\PeekEngine
    Write-Host "[*] Registering PeekEngine Windows service under NT SERVICE\PeekEngine..."
    $serviceName = "PeekEngine"
    $existingSvc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if (-not $existingSvc) {
        & sc.exe create $serviceName binPath= "`"$engineExe`" --service" start= auto obj= "NT SERVICE\PeekEngine" displayname= "Peek Face Recognition Engine" | Out-Null
    } else {
        & sc.exe config $serviceName binPath= "`"$engineExe`" --service" start= auto obj= "NT SERVICE\PeekEngine" | Out-Null
    }
    & sc.exe failure $serviceName reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null

    # Resolve virtual service account NT SERVICE\PeekEngine for ACLs
    $svcAccount = $null
    try {
        $svcAccount = New-Object System.Security.Principal.NTAccount("NT SERVICE\PeekEngine")
    } catch { }

    # 3. Profiles directory: readable/writable ONLY by SYSTEM and Administrators (and PeekEngine service). NO Users ACE.
    $profAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $profAcl.SetAccessRuleProtection($true, $false)
    $profAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $profAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    if ($svcAccount) {
        try {
            $profAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
        } catch { }
    }
    Set-Acl -Path $ProfilesDir -AclObject $profAcl

    # 4. State directory: readable/writable ONLY by SYSTEM and Administrators (and PeekEngine service). NO Users ACE.
    $stateAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $stateAcl.SetAccessRuleProtection($true, $false)
    $stateAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $stateAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    if ($svcAccount) {
        try {
            $stateAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
        } catch { }
    }
    Set-Acl -Path $StateDir -AclObject $stateAcl

    # 5. Machine entropy file: readable/writable ONLY by SYSTEM and Administrators (and PeekEngine service). NO Users ACE.
    $entropyPath = Join-Path $DataDir "machine_entropy.bin"
    if (-not (Test-Path $entropyPath)) {
        Write-Host "[*] Generating machine_entropy.bin..."
        $guid = [Guid]::NewGuid().ToString()
        [System.IO.File]::WriteAllText($entropyPath, $guid, (New-Object System.Text.UTF8Encoding($false)))
    }
    $fAcl = New-Object System.Security.AccessControl.FileSecurity
    $fAcl.SetAccessRuleProtection($true, $false)
    $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "Allow")))
    $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "Allow")))
    if ($svcAccount) {
        try {
            $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svcAccount, "FullControl", "Allow")))
        } catch { }
    }
    Set-Acl -Path $entropyPath -AclObject $fAcl

    # 6. Registry Hardening
    # HKLM:\SOFTWARE\Peek must be Administrators/SYSTEM write only. InstallPath, LogLevel, Updates, Identities stay admin-only.
    $peekRegKey = "HKLM:\SOFTWARE\Peek"
    if (Test-Path $peekRegKey) {
        $regKeyObj = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey("SOFTWARE\Peek", [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, [System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadKey)
        if ($regKeyObj) {
            $regAcl = New-Object System.Security.AccessControl.RegistrySecurity
            $regAcl.SetAccessRuleProtection($true, $false)
            $regAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($sys, "FullControl", "ContainerInherit", "None", "Allow")))
            $regAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($adm, "FullControl", "ContainerInherit", "None", "Allow")))
            $regAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($usr, [System.Security.AccessControl.RegistryRights]::ReadKey, "ContainerInherit", "None", "Allow")))
            $regKeyObj.SetAccessControl($regAcl)
            $regKeyObj.Close()
        }
    }

    # HKLM:\SOFTWARE\Peek\Prefs: Users may write (SetValue and ReadKey only)
    $prefsRegKey = "HKLM:\SOFTWARE\Peek\Prefs"
    if (-not (Test-Path $prefsRegKey)) {
        New-Item -Path $prefsRegKey -Force | Out-Null
    }
    $prefsKeyObj = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey("SOFTWARE\Peek\Prefs", [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, [System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadKey)
    if ($prefsKeyObj) {
        $prefsAcl = New-Object System.Security.AccessControl.RegistrySecurity
        $prefsAcl.SetAccessRuleProtection($true, $false)
        $prefsAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($sys, "FullControl", "ContainerInherit", "None", "Allow")))
        $prefsAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($adm, "FullControl", "ContainerInherit", "None", "Allow")))
        $prefsAcl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule(
            $usr,
            [System.Security.AccessControl.RegistryRights]::SetValue -bor [System.Security.AccessControl.RegistryRights]::ReadKey,
            "ContainerInherit",
            "None",
            "Allow"
        )))
        $prefsKeyObj.SetAccessControl($prefsAcl)
        $prefsKeyObj.Close()
    }

    # Move AutoSelect from HKLM:\SOFTWARE\Peek to HKLM:\SOFTWARE\Peek\Prefs
    $autoSelectVal = 1
    if (Test-Path $peekRegKey) {
        $existingVal = (Get-ItemProperty -Path $peekRegKey -Name "AutoSelect" -ErrorAction SilentlyContinue).AutoSelect
        if ($null -ne $existingVal) {
            $autoSelectVal = $existingVal
            Remove-ItemProperty -Path $peekRegKey -Name "AutoSelect" -ErrorAction SilentlyContinue
        }
    }
    Set-ItemProperty -Path $prefsRegKey -Name "AutoSelect" -Value $autoSelectVal -Type DWord -Force

    # Ensure ColdBootUnlock is set in Prefs (default 1)
    if (-not (Get-ItemProperty -Path $prefsRegKey -Name "ColdBootUnlock" -ErrorAction SilentlyContinue)) {
        Set-ItemProperty -Path $prefsRegKey -Name "ColdBootUnlock" -Value 1 -Type DWord -Force
    }

    # 7. Remove legacy Scheduled Task, Run keys, and Startup launchers
    Write-Host "[*] Removing legacy Scheduled Task and user-session auto-start launchers..."
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

    # 8. Start the PeekEngine service
    try {
        Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    } catch { }

    Write-Host "[OK] Post-install configuration completed successfully."
    exit 0
}
catch {
    Write-Error "Post-install configuration failed: $_"
    exit 1
}
