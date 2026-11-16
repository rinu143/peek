<#
.SYNOPSIS
    Peek Inno Setup Post-Installation Script
.DESCRIPTION
    Configures ProgramData directory security ACLs, generates initial machine_entropy.bin
    if absent, and registers the PeekEngine logon scheduled task for BUILTIN\Users.
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

    if (-not (Test-Path $DataDir)) {
        New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    }
    if (-not (Test-Path $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }

    $sys = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null)
    $adm = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
    $usr = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinUsersSid, $null)

    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usr, "ReadAndExecute", "ContainerInherit,ObjectInherit", "None", "Allow")))
    Set-Acl -Path $DataDir -AclObject $acl
    Set-Acl -Path $LogDir -AclObject $acl

    # Machine entropy file
    $entropyPath = Join-Path $DataDir "machine_entropy.bin"
    if (-not (Test-Path $entropyPath)) {
        Write-Host "[*] Generating machine_entropy.bin..."
        $guid = [Guid]::NewGuid().ToString()
        [System.IO.File]::WriteAllText($entropyPath, $guid, (New-Object System.Text.UTF8Encoding($false)))
        $fAcl = New-Object System.Security.AccessControl.FileSecurity
        $fAcl.SetAccessRuleProtection($true, $false)
        $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "Allow")))
        $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "Allow")))
        $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usr, "ReadAndExecute", "Allow")))
        Set-Acl -Path $entropyPath -AclObject $fAcl
    }

    # Profiles directory
    $ProfilesDir = Join-Path $DataDir "Profiles"
    if (-not (Test-Path $ProfilesDir)) {
        New-Item -ItemType Directory -Path $ProfilesDir -Force | Out-Null
    }
    $profAcl = New-Object System.Security.AccessControl.DirectorySecurity
    $profAcl.SetAccessRuleProtection($true, $false)
    $profAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $profAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $profAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usr, "Modify", "ContainerInherit,ObjectInherit", "None", "Allow")))
    Set-Acl -Path $ProfilesDir -AclObject $profAcl

    # HKLM:\SOFTWARE\Peek user permission for AutoSelect
    $peekRegKey = "HKLM:\SOFTWARE\Peek"
    if (Test-Path $peekRegKey) {
        try {
            $peekAcl = Get-Acl -Path $peekRegKey
            $peekRegRule = New-Object System.Security.AccessControl.RegistryAccessRule(
                $usr,
                [System.Security.AccessControl.RegistryRights]::SetValue -bor [System.Security.AccessControl.RegistryRights]::CreateSubKey -bor [System.Security.AccessControl.RegistryRights]::ReadKey,
                [System.Security.AccessControl.InheritanceFlags]::None,
                [System.Security.AccessControl.PropagationFlags]::None,
                [System.Security.AccessControl.AccessControlType]::Allow
            )
            $peekAcl.AddAccessRule($peekRegRule)
            Set-Acl -Path $peekRegKey -AclObject $peekAcl
        } catch { }
    }

    # Register Scheduled Task: PeekEngine at System Startup and Logon
    Write-Host "[*] Registering PeekEngine startup task..."
    $engineExe = Join-Path $InstallDir "engine\PeekEngine.exe"
    $engineDir = Join-Path $InstallDir "engine"
    $action = New-ScheduledTaskAction -Execute $engineExe -WorkingDirectory $engineDir
    $triggerStartup = New-ScheduledTaskTrigger -AtStartup
    $triggerLogon = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "PeekEngine" -Action $action -Trigger @($triggerStartup, $triggerLogon) -Principal $principal -Settings $settings -Force | Out-Null

    # Configure user logon auto-start via Run key and CommonStartup shortcut
    try {
        Set-ItemProperty -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "PeekEngine" -Value "`"$engineExe`"" -Type String -Force -ErrorAction SilentlyContinue
    } catch { }

    try {
        $startupFolder = [Environment]::GetFolderPath("CommonStartup")
        if (Test-Path $startupFolder) {
            $shortcutPath = Join-Path $startupFolder "PeekEngine.lnk"
            $wsh = New-Object -ComObject WScript.Shell
            $shortcut = $wsh.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = $engineExe
            $shortcut.WorkingDirectory = $engineDir
            $shortcut.Description = "Peek Face Recognition Engine"
            $shortcut.Save()
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) | Out-Null
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null
        }
    } catch { }

    Write-Host "[OK] Post-install configuration completed successfully."
    exit 0
}
catch {
    Write-Error "Post-install configuration failed: $_"
    exit 1
}
