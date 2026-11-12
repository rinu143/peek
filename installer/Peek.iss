; ==============================================================================
; Peek Face Authentication - Inno Setup 6 Installer Script
; Produces: Peek-Setup-<version>-x64.exe
; ==============================================================================

#ifndef MyAppVersion
#define MyAppVersion "0.2.0-beta.1"
#endif

#if FileExists("version.iss")
#include "version.iss"
#endif

#define MyAppName "Peek"
#define MyAppPublisher "Peek Biometrics Team"
#define MyAppURL "https://github.com/peek"
#define MyAppExeName "PeekSetup.exe"
#define MyAppId "{{A35F4C74-8846-4B82-AE68-2C7BE6E44E3D}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\release
OutputBaseFilename=Peek-Setup-{#MyAppVersion}-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=no
RestartIfNeededByRun=no
SetupIconFile=..\build\peek.ico
UninstallDisplayIcon={app}\PeekSetup.exe
UninstallDisplayName={#MyAppName} Face Authentication

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autocheck"; Description: "Check for updates automatically (recommended)"; GroupDescription: "Additional options:"
Name: "openpeeksetup"; Description: "Open Peek setup when finished"; GroupDescription: "Launch:"

[Files]
; 1. PyInstaller one-dir engine into <InstallDir>\engine\
Source: "..\dist\PeekEngine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs
; 2. Single-file self-contained setup utility
Source: "..\dist\PeekSetup.exe"; DestDir: "{app}"; Flags: ignoreversion
; 3. Single-file self-contained update utility
Source: "..\dist\PeekUpdate.exe"; DestDir: "{app}"; Flags: ignoreversion
; 4. Credential Provider DLL with restartreplace
Source: "..\dist\PeekCredentialProvider.dll"; DestDir: "{app}"; Flags: ignoreversion restartreplace uninsrestartdelete
; 5. Documentation & Licenses
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Peek\Peek Setup"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"

[Registry]
; HKLM\SOFTWARE\Peek
Root: HKLM; Subkey: "SOFTWARE\Peek"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekeyifempty
Root: HKLM; Subkey: "SOFTWARE\Peek"; ValueType: dword; ValueName: "LogLevel"; ValueData: 1; Flags: uninsdeletevalue
Root: HKLM; Subkey: "SOFTWARE\Peek"; ValueType: string; ValueName: "DisplayVersion"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletevalue
; Updates\AutoCheck
Root: HKLM; Subkey: "SOFTWARE\Peek\Updates"; ValueType: dword; ValueName: "AutoCheck"; ValueData: 1; Tasks: autocheck; Flags: uninsdeletevalue
Root: HKLM; Subkey: "SOFTWARE\Peek\Updates"; ValueType: dword; ValueName: "AutoCheck"; ValueData: 0; Tasks: not autocheck; Flags: uninsdeletevalue
Root: HKLM; Subkey: "SOFTWARE\Peek\Updates"; ValueType: string; ValueName: "Channel"; ValueData: "stable"; Flags: createvalueifdoesntexist uninsdeletevalue
Root: HKLM; Subkey: "SOFTWARE\Peek\Updates"; ValueType: string; ValueName: "FeedUrl"; ValueData: "https://raw.githubusercontent.com/peek/updates/main"; Flags: createvalueifdoesntexist uninsdeletevalue

[Code]
var
  SecurityNoticePage: TOutputMsgWizardPage;
  IsUpgradeInstall: Boolean;
  ExistingInstalledVersion: String;
  ShouldRemoveUserData: Boolean;

// Helper: Parse integer safely
function SafeStrToInt(const S: String; DefaultVal: Integer): Integer;
begin
  Result := StrToIntDef(S, DefaultVal);
end;

// Helper: Compare two SemVer strings (e.g. "0.2.0-beta.1" vs "0.1.0")
// Returns: 1 if V1 > V2, -1 if V1 < V2, 0 if V1 = V2
function CompareSemVer(V1, V2: String): Integer;
var
  Part1, Part2: String;
  P1, P2: Integer;
  Num1, Num2: Integer;
  i: Integer;
begin
  Result := 0;
  // Clean off build metadata if present (after '+')
  P1 := Pos('+', V1);
  if P1 > 0 then V1 := Copy(V1, 1, P1 - 1);
  P2 := Pos('+', V2);
  if P2 > 0 then V2 := Copy(V2, 1, P2 - 1);

  // Compare Major, Minor, Patch
  for i := 1 to 3 do
  begin
    P1 := Pos('.', V1);
    if P1 > 0 then
    begin
      Part1 := Copy(V1, 1, P1 - 1);
      V1 := Copy(V1, P1 + 1, Length(V1));
    end
    else
    begin
      // Strip any prerelease suffix on patch
      P1 := Pos('-', V1);
      if P1 > 0 then
      begin
        Part1 := Copy(V1, 1, P1 - 1);
        V1 := Copy(V1, P1, Length(V1));
      end
      else
      begin
        Part1 := V1;
        V1 := '';
      end;
    end;

    P2 := Pos('.', V2);
    if P2 > 0 then
    begin
      Part2 := Copy(V2, 1, P2 - 1);
      V2 := Copy(V2, P2 + 1, Length(V2));
    end
    else
    begin
      P2 := Pos('-', V2);
      if P2 > 0 then
      begin
        Part2 := Copy(V2, 1, P2 - 1);
        V2 := Copy(V2, P2, Length(V2));
      end
      else
      begin
        Part2 := V2;
        V2 := '';
      end;
    end;

    Num1 := SafeStrToInt(Part1, 0);
    Num2 := SafeStrToInt(Part2, 0);

    if Num1 > Num2 then
    begin
      Result := 1;
      Exit;
    end
    else if Num1 < Num2 then
    begin
      Result := -1;
      Exit;
    end;
  end;

  // If major.minor.patch are equal, check prerelease (presence of '-' indicates lower precedence than release)
  if (V1 <> '') and (V2 = '') then
    Result := -1
  else if (V1 = '') and (V2 <> '') then
    Result := 1
  else if (V1 <> '') and (V2 <> '') then
  begin
    if V1 > V2 then Result := 1
    else if V1 < V2 then Result := -1;
  end;
end;

// Helper: Stop Peek running processes
procedure StopPeekProcesses();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/f /im PeekEngine.exe /im PeekSetup.exe /im PeekUpdate.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(400);
end;

// Helper: Recursively delete directory
procedure DeleteDirectoryContents(const DirPath: String);
var
  FindRec: TFindRec;
begin
  if FindFirst(DirPath + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          begin
            DeleteDirectoryContents(DirPath + '\' + FindRec.Name);
            RemoveDir(DirPath + '\' + FindRec.Name);
          end
          else
          begin
            DeleteFile(DirPath + '\' + FindRec.Name);
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

// Helper: Copy directory contents recursively
procedure CopyDirectory(const SourceDir, TargetDir: String);
var
  FindRec: TFindRec;
begin
  CreateDir(TargetDir);
  if FindFirst(SourceDir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          begin
            CopyDirectory(SourceDir + '\' + FindRec.Name, TargetDir + '\' + FindRec.Name);
          end
          else
          begin
            FileCopy(SourceDir + '\' + FindRec.Name, TargetDir + '\' + FindRec.Name, False);
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

// Check CLI parameters for /AUTOCHECK=0|1 and /REMOVEDATA=1
function GetCommandLineParam(const ParamName: String): String;
var
  i: Integer;
  P, Prefix: String;
begin
  Result := '';
  Prefix := '/' + Uppercase(ParamName) + '=';
  for i := 1 to ParamCount do
  begin
    P := ParamStr(i);
    if Pos(Prefix, Uppercase(P)) = 1 then
    begin
      Result := Copy(P, Length(Prefix) + 1, Length(P));
      Exit;
    end;
  end;
end;

function HasCommandLineSwitch(const SwitchName: String): Boolean;
var
  i: Integer;
  P, Target: String;
begin
  Result := False;
  Target := '/' + Uppercase(SwitchName);
  for i := 1 to ParamCount do
  begin
    P := Uppercase(ParamStr(i));
    if (P = Target) or (Pos(Target + '=', P) = 1) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
var
  InstalledVer: String;
begin
  Result := True;
  IsUpgradeInstall := False;
  ExistingInstalledVersion := '';

  // 1. Check for existing installed version in registry
  if RegQueryStringValue(HKLM, 'SOFTWARE\Peek', 'DisplayVersion', InstalledVer) then
  begin
    ExistingInstalledVersion := InstalledVer;
  end
  else if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\' + ExpandConstant('{#MyAppId}') + '_is1', 'DisplayVersion', InstalledVer) then
  begin
    ExistingInstalledVersion := InstalledVer;
  end;

  if ExistingInstalledVersion <> '' then
  begin
    // Check for downgrade
    if CompareSemVer(ExistingInstalledVersion, '{#MyAppVersion}') > 0 then
    begin
      SuppressibleMsgBox('A newer version of Peek (' + ExistingInstalledVersion + ') is already installed.' + #13#10 +
                         'Downgrading to version ' + '{#MyAppVersion}' + ' is not supported.' + #13#10 + #13#10 +
                         'Setup will now terminate.', mbCriticalError, MB_OK, MB_OK);
      Result := False;
      Exit;
    end;
    IsUpgradeInstall := True;
  end;

  // Stop any running Peek processes before proceeding
  StopPeekProcesses();
end;

procedure InitializeWizard();
var
  NoticeMsg: String;
begin
  // Security Notice Wizard Page (Inserted immediately after License page)
  NoticeMsg :=
    'IMPORTANT SECURITY & HARDWARE NOTICE:' + #13#10 + #13#10 +
    '1. Camera Hardware Requirements:' + #13#10 +
    '   Peek uses your standard RGB webcam for biometric verification. It does NOT use or ' +
    'require Windows Hello specialized infrared (IR) or 3D depth hardware. RGB recognition ' +
    'may be susceptible to presentation attacks such as high-resolution printed photos or videos.' + #13#10 + #13#10 +
    '2. Password & Secret Storage:' + #13#10 +
    '   To automatically unlock your workstation upon face recognition, Peek stores your Windows ' +
    'password in a local Windows LSA private secret. While encrypted by DPAPI and protected by OS ' +
    'security boundaries, local computer administrators have the authority to read LSA secrets.' + #13#10 + #13#10 +
    '3. Guaranteed Fallback Authentication:' + #13#10 +
    '   Windows standard sign-in (Password and PIN) is NEVER disabled. If the camera is disconnected, ' +
    'face recognition fails, or the Peek engine is offline, you can always sign in normally.';

  SecurityNoticePage := CreateOutputMsgPage(wpLicense,
    'Security Notice',
    'Please review these important security and hardware considerations before proceeding.',
    NoticeMsg);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  StopPeekProcesses();
end;

// Backup previous install before copying files
procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir, BackupRoot, VersionBackupDir: String;
  ResultCode: Integer;
  AutoCheckParam: String;
  PsScript: String;
begin
  if CurStep = ssInstall then
  begin
    AppDir := ExpandConstant('{app}');
    if DirExists(AppDir) and (FileExists(AppDir + '\PeekSetup.exe') or FileExists(AppDir + '\PeekCredentialProvider.dll')) then
    begin
      BackupRoot := ExpandConstant('{commonappdata}\Peek\backup');
      if not DirExists(BackupRoot) then
        ForceDirectories(BackupRoot);

      // Clean existing backups to keep exactly one
      DeleteDirectoryContents(BackupRoot);

      if ExistingInstalledVersion = '' then
        ExistingInstalledVersion := 'pre-update';

      VersionBackupDir := BackupRoot + '\' + ExistingInstalledVersion;
      CopyDirectory(AppDir, VersionBackupDir);
    end;
  end
  else if CurStep = ssPostInstall then
  begin
    // 1. Handle /AUTOCHECK=0|1 CLI override if provided
    AutoCheckParam := GetCommandLineParam('AUTOCHECK');
    if AutoCheckParam = '0' then
      RegWriteDWordValue(HKLM, 'SOFTWARE\Peek\Updates', 'AutoCheck', 0)
    else if AutoCheckParam = '1' then
      RegWriteDWordValue(HKLM, 'SOFTWARE\Peek\Updates', 'AutoCheck', 1);

    // 2. Execute post-install configuration via PowerShell
    // Configures ProgramData ACLs, machine_entropy.bin, regsvr32 CP DLL, and BUILTIN\Users scheduled task
    PsScript :=
      '$ErrorActionPreference = "Stop"; ' +
      '$InstallDir = "' + ExpandConstant('{app}') + '"; ' +
      '$DataDir = Join-Path $env:ProgramData "Peek"; ' +
      '$LogDir = Join-Path $DataDir "logs"; ' +
      'New-Item -ItemType Directory -Path $DataDir -Force | Out-Null; ' +
      'New-Item -ItemType Directory -Path $LogDir -Force | Out-Null; ' +
      '$sys = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null); ' +
      '$adm = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null); ' +
      '$usr = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinUsersSid, $null); ' +
      '$acl = New-Object System.Security.AccessControl.DirectorySecurity; ' +
      '$acl.SetAccessRuleProtection($true, $false); ' +
      '$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow"))); ' +
      '$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow"))); ' +
      '$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usr, "ReadAndExecute", "ContainerInherit,ObjectInherit", "None", "Allow"))); ' +
      'Set-Acl -Path $DataDir -AclObject $acl; ' +
      'Set-Acl -Path $LogDir -AclObject $acl; ' +
      '$entropyPath = Join-Path $DataDir "machine_entropy.bin"; ' +
      'if (-not (Test-Path $entropyPath)) { ' +
      '  $guid = [Guid]::NewGuid().ToString(); ' +
      '  [System.IO.File]::WriteAllText($entropyPath, $guid, (New-Object System.Text.UTF8Encoding($false))); ' +
      '  $fAcl = New-Object System.Security.AccessControl.FileSecurity; ' +
      '  $fAcl.SetAccessRuleProtection($true, $false); ' +
      '  $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sys, "FullControl", "Allow"))); ' +
      '  $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adm, "FullControl", "Allow"))); ' +
      '  $fAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usr, "ReadAndExecute", "Allow"))); ' +
      '  Set-Acl -Path $entropyPath -AclObject $fAcl; ' +
      '}; ' +
      '$engineExe = Join-Path $InstallDir "engine\PeekEngine.exe"; ' +
      '$engineDir = Join-Path $InstallDir "engine"; ' +
      '$action = New-ScheduledTaskAction -Execute $engineExe -WorkingDirectory $engineDir; ' +
      '$trigger = New-ScheduledTaskTrigger -AtLogOn; ' +
      '$principal = New-ScheduledTaskPrincipal -GroupId "BUILTIN\Users" -RunLevel Limited; ' +
      '$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable; ' +
      'Register-ScheduledTask -TaskName "PeekEngine" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null; ';

    Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "' + PsScript + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    // 3. Register Credential Provider DLL via regsvr32
    Exec('regsvr32.exe', '/s "' + ExpandConstant('{app}\PeekCredentialProvider.dll') + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    // Verify CP registration in registry
    if not RegKeyExists(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}') then
    begin
      SuppressibleMsgBox('ERROR: Peek Credential Provider DLL registration failed.' + #13#10 +
                         'Windows LogonUI integration could not be established.' + #13#10 +
                         'Check ' + ExpandConstant('{commonappdata}\Peek\logs') + ' for details.', mbCriticalError, MB_OK, MB_OK);
    end;

    // 4. Start the engine for the interactive user
    ExecAsOriginalUser(ExpandConstant('{app}\engine\PeekEngine.exe'), '', ExpandConstant('{app}\engine'), SW_HIDE, ewNoWait, ResultCode);
    Sleep(1200);

    // 4b. Register user-level PeekUpdate scheduled task
    if FileExists(ExpandConstant('{app}\PeekUpdate.exe')) then
    begin
      ExecAsOriginalUser(ExpandConstant('{app}\PeekUpdate.exe'), '--register-task', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
      if ResultCode <> 0 then
      begin
        Exec(ExpandConstant('{app}\PeekUpdate.exe'), '--register-task', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end;
    end;

    // 5. Health Check: Run PeekSetup.exe --self-test
    Exec(ExpandConstant('{app}\PeekSetup.exe'), '--self-test', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then
    begin
      if IsUpgradeInstall then
      begin
        if SuppressibleMsgBox('WARNING: Peek post-installation health check reported issues.' + #13#10 +
                              'Log path: ' + ExpandConstant('{commonappdata}\Peek\logs') + #13#10 + #13#10 +
                              'Would you like to roll back to the previous version (' + ExistingInstalledVersion + ')?',
                              mbConfirmation, MB_YESNO, IDNO) = IDYES then
        begin
          Exec(ExpandConstant('{app}\PeekSetup.exe'), '--rollback', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
          SuppressibleMsgBox('Rollback completed. The previous version has been restored.', mbInformation, MB_OK, MB_OK);
          Exit;
        end;
      end
      else
      begin
        SuppressibleMsgBox('Notice: Peek installation completed, but the initial health check reported warnings.' + #13#10 +
                           'Log path: ' + ExpandConstant('{commonappdata}\Peek\logs'), mbInformation, MB_OK, MB_OK);
      end;
    end;

    // 6. Launch PeekSetup if user checked task
    if WizardIsTaskSelected('openpeeksetup') then
    begin
      ExecAsOriginalUser(ExpandConstant('{app}\PeekSetup.exe'), '', ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait, ResultCode);
    end;
  end;
end;

// ==============================================================================
// UNINSTALLATION LOGIC
// ==============================================================================

function InitializeUninstall(): Boolean;
var
  RemoveDataParam: String;
begin
  Result := True;
  ShouldRemoveUserData := False;

  // 1. Check for /REMOVEDATA=1 parameter
  RemoveDataParam := GetCommandLineParam('REMOVEDATA');
  if RemoveDataParam = '1' then
  begin
    ShouldRemoveUserData := True;
  end
  else if not UninstallSilent then
  begin
    // Prompt user whether to remove face data and saved passwords (Default: NO)
    if SuppressibleMsgBox('Also remove face data and saved passwords?' + #13#10 + #13#10 +
                          '(Default: No. If removed, enrolled biometric face profiles and Windows ' +
                          'passwords linked to Peek will be permanently erased).',
                          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    begin
      ShouldRemoveUserData := True;
    end;
  end;

  // Stop running Peek processes
  StopPeekProcesses();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  PsCleanup: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    // 1. Stop any running Peek processes
    StopPeekProcesses();

    // 2. Unregister Credential Provider DLL
    Exec('regsvr32.exe', '/u /s "' + ExpandConstant('{app}\PeekCredentialProvider.dll') + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    // 3. Remove scheduled tasks
    Exec('schtasks.exe', '/Delete /TN "PeekEngine" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec('schtasks.exe', '/Delete /TN "PeekUpdate" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    // 4. Delete CP and COM registry keys
    RegDeleteKeyIncludingSubkeys(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}');
    RegDeleteKeyIncludingSubkeys(HKLM, 'SOFTWARE\Classes\CLSID\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}');
    RegDeleteKeyIncludingSubkeys(HKLM, 'SOFTWARE\Classes\WOW6432Node\CLSID\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}');

    // 5. If data removal was requested, run PeekSetup.exe --uninstall-cleanup
    if ShouldRemoveUserData then
    begin
      if FileExists(ExpandConstant('{app}\PeekSetup.exe')) then
      begin
        Exec(ExpandConstant('{app}\PeekSetup.exe'), '--uninstall-cleanup', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end
      else
      begin
        // Direct PowerShell fallback cleanup
        PsCleanup :=
          'try { ' +
          '  Remove-Item -Path "HKLM:\SOFTWARE\Peek" -Recurse -Force -ErrorAction SilentlyContinue; ' +
          '  Remove-Item -Path (Join-Path $env:ProgramData "Peek") -Recurse -Force -ErrorAction SilentlyContinue; ' +
          '  Remove-Item -Path (Join-Path $env:LOCALAPPDATA "Peek") -Recurse -Force -ErrorAction SilentlyContinue; ' +
          '} catch {}';
        Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "' + PsCleanup + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end;
    end
    else
    begin
      // Preserve data, remove only HKLM\SOFTWARE\Peek (InstallPath and DisplayVersion)
      RegDeleteValue(HKLM, 'SOFTWARE\Peek', 'InstallPath');
      RegDeleteValue(HKLM, 'SOFTWARE\Peek', 'DisplayVersion');
    end;
  end;
end;
