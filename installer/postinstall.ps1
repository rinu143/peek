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

    # Register Scheduled Task: PeekEngine
    Write-Host "[*] Registering PeekEngine scheduled task..."
    $engineExe = Join-Path $InstallDir "engine\PeekEngine.exe"
    $engineDir = Join-Path $InstallDir "engine"
    $action = New-ScheduledTaskAction -Execute $engineExe -WorkingDirectory $engineDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -GroupId "BUILTIN\Users" -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName "PeekEngine" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

    Write-Host "[OK] Post-install configuration completed successfully."
    exit 0
}
catch {
    Write-Error "Post-install configuration failed: $_"
    exit 1
}
