# Peek Face Authentication — Test Build (v0.1-test)

> [!WARNING]
> **TEST BUILD NOTICE — NOT A SIGNED RELEASE**
> 
> This is a **throwaway evaluation test package**, NOT an official signed release:
> - **Code Signing**: Binaries in this package are **NOT code-signed** with an Authenticode certificate. Windows SmartScreen and Windows Defender will display a blue banner: *"Windows protected your PC — Unknown publisher"*. This is **expected**. Click **"More info"** and then **"Run anyway"**.
> - **Installer**: This package uses a test-grade PowerShell script (`install.ps1` / `install.bat`), NOT an official MSI or MSIX installer.
> - **Service Architecture**: `PeekEngine.exe` is configured to launch via a user-session logon scheduled task rather than a native Windows Service (`NT AUTHORITY\SYSTEM`).
> - **No Auto-Updates / No Telemetry**: This test package contains no auto-updaters, background telemetry, or outbound network calls. All biometric evaluation runs 100% locally.

---

## What Is Peek?

**Peek** is a biometric face authentication system for Windows 10 and Windows 11. It brings fast, private, fail-open face recognition unlock to standard webcams without requiring specialized infrared (IR) Windows Hello hardware.

### Architecture Highlights
- **SCRFD Face Detector & ArcFace Embedder**: Lightweight ONNX deep learning models bundled directly inside `PeekEngine.exe` (no separate Python installation required).
- **Multi-Cue Liveness & Temporal Verification**: Rejects 2D printouts and screen replays using active pose challenge, texture inspection, and temporal sliding-window consistency.
- **Fail-Open Security**: The Windows Credential Provider tile clearly displays service status. If Peek is stopped, the camera is unplugged, or biometric match fails, your standard Windows password and PIN tiles remain fully functional. You are **never locked out**.
- **DPAPI Vault**: If you choose to link your Windows password in Peek Setup, the credential is encrypted using Windows Data Protection API (DPAPI) and replayed to `LogonUI.exe` upon genuine face authentication.

---

## Package Contents

| File | Description |
|---|---|
| `PeekEngine.exe` | Standalone Python Face Engine (PyInstaller onefile bundling models and runtime) |
| `PeekSetup.exe` | Self-contained .NET 8 WPF enrollment wizard and password linking application |
| `PeekCredentialProvider.dll` | Native C++ Windows Credential Provider COM DLL for `LogonUI.exe` |
| `install.bat` / `install.ps1` | Interactive Administrator installer script |
| `uninstall.bat` / `uninstall.ps1` | Clean uninstaller that removes all files, tasks, and registry keys |
| `README.txt` / `README.md` | Testing and documentation instructions |

---

## System Requirements

- **Operating System**: Windows 10 (64-bit) or Windows 11 (64-bit), x64 architecture.
- **Hardware**: Working USB or integrated webcam.
- **Privileges**: Local Administrator rights (required to register the Credential Provider DLL in `HKLM`).
- **Dependencies**: None. Both `PeekEngine.exe` and `PeekSetup.exe` are completely self-contained.

---

## Installation Instructions

1. **Extract the ZIP Archive**:
   - Extract the entire `.zip` file into a local folder (e.g. `C:\Peek-TestPackage` or `Downloads\Peek-TestPackage`).
   - *Do not attempt to run scripts directly from inside the compressed zip view.*

2. **Run Installer as Administrator**:
   - Right-click **`install.bat`** (or `install.ps1`) and select **"Run as administrator"**.
   - If prompted by Windows SmartScreen ("Windows protected your PC"), click **"More info"** &rarr; **"Run anyway"**.
   - If prompted by User Account Control (UAC), click **"Yes"**.

3. **What the Installer Does**:
   - Copies binaries to `C:\Program Files\Peek\`.
   - Registers `PeekCredentialProvider.dll` with Windows LogonUI (`regsvr32.exe`).
   - Configures a test-grade Scheduled Task (`PeekEngine`) to start the engine automatically at user logon.
   - Creates a Start Menu shortcut: **"Peek Setup"**.
   - Starts `PeekEngine.exe` in the background so you can test immediately.

---

## How to Test Peek (Step-by-Step)

### Step 1: Enroll Your Face
1. Open **Peek Setup** from the Start Menu, or run `C:\Program Files\Peek\PeekSetup.exe`.
2. Ensure `PeekEngine` is running (the status indicator in Peek Setup will show green / connected).
3. Follow the 5 guided pose challenges:
   - **Center** (look straight ahead)
   - **Turn Left** (turn head ~15° left)
   - **Turn Right** (turn head ~15° right)
   - **Tilt Up** (tilt head slightly up)
   - **Tilt Down** (tilt head slightly down)
4. Your biometric face profile is securely encrypted and saved in `%LOCALAPPDATA%\Peek\Profiles\`.

### Step 2: Link Your Windows Password (Optional but Recommended)
1. On the second tab of Peek Setup ("Link Password"), enter your current Windows account password.
2. Click **"Verify & Link Password"**.
3. Peek validates the password against Windows Local Security Authority (LSA) and stores it in a DPAPI-encrypted file (`%LOCALAPPDATA%\Peek\Profiles\<profile_id>.secret`).
   *(Note: Without linking your password, Peek can authenticate your identity on the lock screen, but Windows requires your password/PIN to finalize workstation unlock).*

### Step 3: Test Lock Screen Face Unlock
1. Lock your workstation: Press **`Win + L`**.
2. On the Windows lock screen, select the **Peek** tile (or it will be selected automatically).
3. Look directly into your webcam.
4. Peek detects your face, verifies liveness, animates the lock tile, and automatically unlocks your desktop!

### Fail-Open Verification:
- Unplug or cover your webcam and lock the screen.
- Notice that the Peek tile gracefully indicates camera status, and you can immediately switch to your standard Windows Password or PIN tile to log in normally.

---

## Uninstallation Instructions

To completely remove Peek and ensure **no leftover registry or COM registrations** remain:

1. Right-click **`uninstall.bat`** (or `uninstall.ps1`) and select **"Run as administrator"**.
2. The script will:
   - Terminate active `PeekEngine.exe` and `PeekSetup.exe` processes.
   - Unregister `PeekCredentialProvider.dll` via `regsvr32.exe /u`.
   - Thoroughly scrub COM and Credential Provider registry keys from `HKLM` and `HKCR`.
   - Remove the `PeekEngine` scheduled task and startup shortcuts.
   - Remove the Start Menu shortcuts and `C:\Program Files\Peek\` folder.
   - Ask whether you wish to delete enrolled biometric profiles and cached secrets from `%LOCALAPPDATA%\Peek`.
3. Press Enter when prompted. Windows LogonUI will return to its original state.

---

## How to Report Issues

If you encounter any bugs, crashes, or unexpected behavior during testing:

1. **Information to Collect**:
   - Windows OS build version (`winver`).
   - Camera model (integrated laptop webcam, external USB webcam).
   - Any error message text or screenshots.
2. **Log File Locations**:
   - Engine service logs (if run manually in a console: `PeekEngine.exe --debug`).
   - Enrolled profiles: `%LOCALAPPDATA%\Peek\Profiles`.
3. **Submit Reports**:
   - Open an issue on GitHub: https://github.com/rinu/peek/issues (or your designated feedback channel).
   - Please do NOT include photos or secret files in public bug reports.

---

## Real-Release Delta (What Changes for Production)

For testers evaluating this package, here is the explicit delta between this test package and a future production release:

1. **Authenticode EV Code Signing**:
   - Production releases will be signed with a trusted Extended Validation (EV) certificate, eliminating SmartScreen warnings and antivirus false positives.
2. **Standard Windows Service**:
   - Instead of a user-session scheduled task, `PeekEngine` will run as a native Windows Service with session-isolated camera acquisition or integration with Windows Hello drivers.
3. **Enterprise MSI / MSIX Installer**:
   - Production will provide a standard Windows Installer (`.msi`) supporting Group Policy deployment, silent installs, automatic rollback on error, and Windows "Installed Apps" registration.
4. **Hardware-Backed Key Storage (TPM 2.0 / VBS)**:
   - Future versions will explore asymmetric key pairs backed by TPM 2.0 / Virtualization-Based Security (VBS) rather than DPAPI password replaying.
