# Peek Release Verification Checklist & Test Plan

This document defines the formal validation and acceptance checklist for Peek release builds (`Peek-Setup-<version>-x64.exe`). Every release candidate must pass these verification gates prior to public distribution.

---

## 1. Environment Matrix

| Target OS | Minimum Build | Architecture | Recommended Test Environment |
| :--- | :--- | :--- | :--- |
| **Windows 10** | 10.0.17763 (1809) or later | x64 | Hyper-V / VMware clean VM snapshot |
| **Windows 11 22H2 / 23H2+** | 10.0.22621+ | x64 | Hyper-V / VMware clean VM snapshot with TPM 2.0 |
| **Windows 11 with SAC** | 10.0.22621+ | x64 | Physical device or VM with **Smart App Control** enabled |

---

## 2. Pre-Release Build & Supply Chain Gates

- [ ] **Hash Pinning Verification:**
  - Run `python engine/models/download_models.py`
  - Ensure all 4 model files match their hardcoded SHA-256 digests in `download_models.py`.
  - Confirm downloads are cached under `build/models/`.
- [ ] **Static CRT Verification:**
  - Inspect `PeekCredentialProvider.dll` with `dumpbin /dependents dist\PeekCredentialProvider.dll`.
  - Ensure `MSVCP140.dll` and `VCRUNTIME140.dll` do **NOT** appear in the dynamic dependencies (statically linked via `/MT`).
- [ ] **Single-File Setup Compression:**
  - Verify `PeekSetup.exe` is built with single-file self-contained mode and `EnableCompressionInSingleFile=true`.
  - Verify executable size is optimized.
- [ ] **Automated Test Passes:**
  - Execute `dotnet test app\PeekSetup.Tests\PeekSetup.Tests.csproj -c Release` (144+ unit tests pass).
  - Execute `python -m pytest tests/` (all protocol and IPC tests pass).
  - Execute `credential_provider\build\Release\test_autoselect.exe` (all 33 C++ test cases pass).
- [ ] **Release Build Pipeline Execution:**
  - Run `powershell -ExecutionPolicy Bypass -File scripts\release\build_release.ps1`
  - Output generated under `dist\release\`:
    - `Peek-Setup-<version>-x64.exe`
    - `SHA256SUMS`

---

## 3. Clean Installation Verification (Windows 10 & Windows 11)

### Setup Procedure
1. Revert VM to a pristine snapshot without previous Peek installations.
2. Execute `Peek-Setup-<version>-x64.exe` with administrative elevation.

### Checklist
- [ ] **License & Security Notice Pages:**
  - Installer displays the MIT License page.
  - Next page displays the **Security Notice** detailing:
    1. Standard RGB camera usage (not IR/Windows Hello hardware).
    2. Local LSA private secret storage for automatic unlock (administrator readable).
    3. Fail-open guarantee that Windows sign-in (Password/PIN) is never disabled.
- [ ] **Installation Tasks:**
  - "Check for updates automatically (recommended)" is checked by default.
  - "Open Peek setup when finished" is checked by default.
- [ ] **Installation Directory Structure:**
  - Installed to `%ProgramFiles%\Peek\` (`{autopf}\Peek`).
  - PyInstaller one-DIR engine installed to `%ProgramFiles%\Peek\engine\` (includes `PeekEngine.exe` and model dependencies).
  - Setup utility at `%ProgramFiles%\Peek\PeekSetup.exe`.
  - Credential Provider DLL at `%ProgramFiles%\Peek\PeekCredentialProvider.dll`.
  - Notices: `LICENSE` and `THIRD_PARTY_NOTICES.md` present in root.
- [ ] **Registry Configuration:**
  - `HKLM\SOFTWARE\Peek\InstallPath` = `%ProgramFiles%\Peek` (or full engine path).
  - `HKLM\SOFTWARE\Peek\DisplayVersion` = `<version>`.
  - `HKLM\SOFTWARE\Peek\LogLevel` = 1 (DWORD).
  - `HKLM\SOFTWARE\Peek\Updates\AutoCheck` = 1 (DWORD).
  - `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}` exists and is configured.
- [ ] **Data Directory & ACL Hardening:**
  - `%ProgramData%\Peek\` created.
  - `%ProgramData%\Peek\logs\` created.
  - `%ProgramData%\Peek\machine_entropy.bin` created (36-character GUID string).
  - Directory and file ACLs explicitly set:
    - `NT AUTHORITY\SYSTEM`: FullControl
    - `BUILTIN\Administrators`: FullControl
    - `BUILTIN\Users`: ReadAndExecute
    - Inheritance removed on `%ProgramData%\Peek`.
- [ ] **Scheduled Task Configuration:**
  - Task name: `PeekEngine`
  - Run as: `BUILTIN\Users`
  - RunLevel: `Limited`
  - Trigger: `AtLogOn`
  - Action: Execute `%ProgramFiles%\Peek\engine\PeekEngine.exe` in `%ProgramFiles%\Peek\engine`
  - Settings: Restart on failure (3 retries, 1 minute interval), allow on batteries, execution time limit disabled.
- [ ] **Initial Interactive Startup:**
  - Installer launches `PeekEngine.exe` in background for current interactive user.
  - Installer runs `PeekSetup.exe --self-test` which succeeds with exit code 0.
  - Peek Setup opens upon wizard completion.

---

## 4. End-to-End Functional & Biometric Verification

- [ ] **Enrollment Workflow:**
  - Open Peek Setup -> Click "Enroll Face".
  - Camera preview loads smoothly without latency.
  - Complete 5 guided poses (Center, Slight Up, Slight Down, Slight Left, Slight Right).
  - Enrollment succeeds and creates profile under `%LOCALAPPDATA%\Peek\profiles\`.
- [ ] **Password Linking:**
  - Click "Link Windows Password".
  - Enter valid local/domain Windows password.
  - Verify secret saved to LSA (`L$PeekCred_<SID>`).
- [ ] **Lock Screen Unlock:**
  - Press `Win + L` to lock the workstation.
  - Camera indicator LED illuminates.
  - Status transitions: `Searching...` -> `Face found` -> `Verifying...` -> `Authenticated!`.
  - Workstation unlocks automatically within target SLA (< 1.5 seconds).
- [ ] **Fail-Open Verification:**
  - Lock PC (`Win + L`) and obscure the camera lens.
  - Status text displays: `Face not recognized` or `Peek engine offline`.
  - Windows Password and PIN tiles remain immediately selectable and functional.
  - System never deadlocks or disables standard Windows logon.

---

## 5. In-Place Upgrade Verification

### Setup Procedure
1. Install a previous build of Peek (e.g. `0.1.0` or test package).
2. Complete enrollment and password linking for a user account.
3. Keep `PeekEngine.exe` and `PeekSetup.exe` running.
4. Execute the new release installer `Peek-Setup-<version>-x64.exe`.

### Checklist
- [ ] **Process Handling:**
  - Installer identifies running `PeekEngine` and `PeekSetup` instances and terminates them gracefully.
- [ ] **Backup Generation:**
  - Installer detects previous install and backs up files to `%ProgramData%\Peek\backup\<previous-version>\`.
  - Verify only one backup version is retained (older backups purged).
- [ ] **State & Secret Preservation:**
  - Biometric face models in `%LOCALAPPDATA%\Peek\` remain untouched.
  - `machine_entropy.bin` in `%ProgramData%\Peek\` is **NOT** overwritten or modified.
  - LSA secret `L$PeekCred_<SID>` remains decryptable and intact.
  - Registry subkeys under `HKLM\SOFTWARE\Peek\Identities\` remain intact.
- [ ] **Health Check & Rollback Validation:**
  - Installer executes `PeekSetup.exe --self-test`.
  - If self-test succeeds, upgrade completes smoothly and launches the new engine.
  - Verify lock screen unlock works immediately with the existing face enrollment and linked password without re-enrolling.
  - *Negative Test:* If simulated failure occurs during post-install, installer prompts to roll back (`PeekSetup.exe --rollback`) and successfully restores the previous version from `%ProgramData%\Peek\backup\`.

---

## 6. Downgrade Refusal Gate

- [ ] **Downgrade Guard Test:**
  - On a system with version `0.2.0-beta.1` installed, execute an older installer (e.g. `0.1.0`).
  - Installer checks SemVer in `InitializeSetup()`.
  - Critical error dialog is displayed:
    `A newer version of Peek (0.2.0-beta.1) is already installed. Downgrading to version 0.1.0 is not supported.`
  - Installer aborts immediately with non-zero exit code without modifying any files or registry settings.

---

## 7. Multi-User Machine Verification

### Setup Procedure
1. Create two local test accounts on the VM: `Alice` and `Bob`.
2. Ensure both accounts are members of `BUILTIN\Users`.

### Checklist
- [ ] **Session Isolation:**
  - Sign in as `Alice`:
    - Scheduled task launches `PeekEngine.exe` running as `Alice`.
    - Complete face enrollment and password linking for `Alice`.
  - Switch user to `Bob` (`Win + L` -> Switch user):
    - Sign in as `Bob`.
    - Scheduled task launches `PeekEngine.exe` running as `Bob`.
    - Complete face enrollment and password linking for `Bob`.
- [ ] **Independent Unlocking:**
  - Lock workstation with `Alice` active: Alice's face unlocks Alice.
  - Lock workstation with `Bob` active: Bob's face unlocks Bob.
  - Cross-user recognition rejection: Alice's face does not unlock Bob's locked session.
- [ ] **Secret Isolation:**
  - `Alice` cannot access Bob's biometric templates or LSA secret.
  - LSA secrets are keyed uniquely per account SID (`L$PeekCred_<AliceSID>`, `L$PeekCred_<BobSID>`).

---

## 8. Command-Line & Silent Automation Verification

- [ ] **Silent Installation:**
  ```powershell
  Peek-Setup-<version>-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="C:\Temp\peek_install.log" /AUTOCHECK=0
  ```
  - Verify installation completes with exit code 0 without any UI popups.
  - Verify `C:\Temp\peek_install.log` is generated.
  - Verify `HKLM\SOFTWARE\Peek\Updates\AutoCheck` is set to `0`.
- [ ] **Silent Update Check Override:**
  ```powershell
  Peek-Setup-<version>-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /AUTOCHECK=1
  ```
  - Verify `HKLM\SOFTWARE\Peek\Updates\AutoCheck` is set to `1`.

---

## 9. Uninstallation Verification

### Scenario A: Uninstall WITHOUT Data Removal (Default)
1. Run uninstaller from Windows Settings -> Installed Apps (or `unins000.exe`).
2. Prompt: "Also remove face data and saved passwords?" -> Select **NO** (default).

- [ ] **Execution & Cleanup:**
  - `PeekCredentialProvider.dll` is unregistered via `regsvr32 /u /s`.
  - Credential Provider key removed from `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}`.
  - Scheduled task `PeekEngine` deleted from Task Scheduler.
  - `%ProgramFiles%\Peek\` folder deleted.
  - Start Menu shortcuts removed.
- [ ] **Preservation Verification:**
  - `%LOCALAPPDATA%\Peek\` face profiles are **PRESERVED**.
  - `%ProgramData%\Peek\machine_entropy.bin` is **PRESERVED**.
  - LSA secret `L$PeekCred_<SID>` is **PRESERVED**.
  - Reinstalling Peek restores instant functionality without requiring re-enrollment.
- [ ] **Windows Logon Health:**
  - Reboot PC or lock screen: Windows logon UI operates normally with standard Password/PIN.

---

### Scenario B: Full Uninstall WITH Data Removal (`/REMOVEDATA=1`)
1. Run uninstaller with data removal flag:
   ```powershell
   unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /REMOVEDATA=1
   ```
   *(or select **YES** in the interactive uninstall dialog)*.

- [ ] **Full Data Purge Verification:**
  - `PeekSetup.exe --uninstall-cleanup` executes before file removal.
  - All LSA secrets starting with `L$PeekCred_` are completely wiped.
  - `HKLM\SOFTWARE\Peek\Identities\` is completely wiped.
  - All user profile folders under `%LOCALAPPDATA%\Peek\` are deleted.
  - `%ProgramData%\Peek\` (including `machine_entropy.bin` and logs) is deleted.
  - Zero biometric data, tokens, or plaintext credentials remain on the system.

---

## 10. Smart App Control (SAC) & Security Software Compatibility

- [ ] **Digital Signature Gate:**
  - Run `Get-AuthenticodeSignature` on all shipping artifacts:
    - `Peek-Setup-<version>-x64.exe`
    - `PeekCredentialProvider.dll`
    - `PeekEngine.exe`
    - `PeekSetup.exe`
  - Verify `Status: Valid` with valid certificate chain and RFC 3161 timestamp.
- [ ] **Smart App Control Verification:**
  - On a Windows 11 system with Smart App Control set to **Enforce**:
  - Download installer via browser (or verify Mark-of-the-Web zone identifier `ZoneId=3`).
  - Execute installer: SAC allows execution without blocking or warnings.
  - LogonUI loads `PeekCredentialProvider.dll` without signature errors in Event Viewer (`Microsoft-Windows-CodeIntegrity/Operational`).
- [ ] **Antivirus / EDR False Positive Check:**
  - Submit installer and engine binaries to VirusTotal.
  - Ensure zero false positives across major security engines.

---

## 11. Wire Protocol & Version Mismatch Fail-Open Gate

- [ ] **Wire Protocol & Version Mismatch Fail-Open Gate:**
- [ ] **Version Mismatch Simulation:**
  - Simulate an engine upgrade where CP DLL is at version `0.2.0` and engine responds with `engine_version="0.1.0"` in `ENGINE_READY`.
  - In `credential_provider.log`, verify logged warning:
    `Engine version 0.1 does not match CP version 0.2 (engine_version='0.1.0'). Failing open with restart prompt.`
  - Lock screen displays status:
    `"Peek was just updated, restart your PC"`
  - CP transitions directly to fail-open state without retrying in an infinite loop.
  - User can immediately sign in with Windows password or PIN.

---

## 12. Automatic Update Service & Cryptographic Verification Gates

- [ ] **PeekUpdate Binary & Packaging:**
  - Verify `PeekUpdate.exe` is installed to `%ProgramFiles%\Peek\PeekUpdate.exe`.
  - Verify `PeekUpdate` scheduled task is created for the interactive user.
  - Run `schtasks /Query /TN "PeekUpdate" /FO LIST` and verify logon trigger and 12-hour repetition with 30-minute random delay.
- [ ] **Manifest Cryptographic Verification:**
  - Run `powershell scripts\release\sign_manifest.ps1 -Verify -ManifestPath dist\release\stable.json`.
  - Verify manifest signature passes with embedded primary/secondary public keys.
- [ ] **Update Check via CLI & UI:**
  - Run `PeekUpdate.exe --status` and verify version, channel, and HKLM policy settings.
  - Open Peek Setup -> Updates panel: verify version, channel, and "Check now" button functionality.
- [ ] **Security Rejection Gates:**
  - Expired manifest test: ensure manifest older than 30 days is rejected.
  - Host allow-list test: ensure URLs outside allow-list are rejected.
  - Payload tampering test: simulate altered installer file, verify SHA-256 failure and immediate deletion.
  - Downgrade refusal: ensure version <= installed version is rejected.
- [ ] **Elevated Application & Rollback Guard:**
  - Run `PeekUpdate.exe --apply` elevated: verify secondary copy to `%ProgramData%\Peek\updates\<ver>`, re-verification, silent installer invocation with `/UPDATE`, and post-install `--self-test`.
  - Verify download cache cleanup after install.
