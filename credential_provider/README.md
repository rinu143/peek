# Peek Windows Credential Provider (Phase 5)

Native C++/Win32/COM Windows Credential Provider DLL integrating the Peek Face Engine with the Windows Logon and Lock Screen (`LogonUI.exe`).

---

## Architectural Overview

```
                                    +--------------------------------+
                                    |        Windows LogonUI         |
                                    |    (Winlogon / Lock Screen)    |
                                    +--------------------------------+
                                                   |
                                       COM: ICredentialProvider
                                            ICredentialProviderCredential2
                                                   |
                                                   v
                                    +--------------------------------+
                                    |  PeekCredentialProvider.dll   |
                                    |     (Native C++ / Win32)       |
                                    +--------------------------------+
                                                   |
                                     Windows Named Pipe IPC
                                     \\.\pipe\PeekEngine
                                                   |
                                                   v
                                    +--------------------------------+
                                    |   Peek Engine Service (Phase 6)|
                                    |    scripts/run_engine_service  |
                                    |  SCRFD + ArcFace + Liveness    |
                                    +--------------------------------+
```

### Key Architectural Tenets

1. **Thin Native Client**: The Credential Provider contains **zero** computer vision, deep learning, or ONNX code. All biometric acquisition, quality gating, temporal verification, and multi-cue liveness detection execute out-of-process in the Python engine service.
2. **Standard Windows Credential Provider Contract**: Implements `ICredentialProvider` and `ICredentialProviderCredential2` per Microsoft specifications.
3. **Fail-Open Security**: If the Peek engine service is offline, stopped, or crashed, the Peek tile displays a clear status message (`"Peek engine offline. Sign in with password or PIN."`) and Windows password/PIN/Windows Hello tiles remain fully accessible. The system is **never locked out**.
4. **Dual-Gate Authorization Enforcement**: Unlock serialization occurs **only** when an `AUTHENTICATED` message is received over the pipe carrying `is_authorized_to_unlock == true`.

---

## Credential Serialization Analysis & Decision

### Approach Chosen: `KERB_INTERACTIVE_LOGON`

On receiving `AUTHENTICATED` with `is_authorized_to_unlock == true`, `PeekCredential::GetSerialization` packages user credentials using the standard Windows `KERB_INTERACTIVE_LOGON` structure submitted to the Negotiate/Kerberos LSA package.

#### Evaluation of Options

| Criterion | Approach A: `KERB_INTERACTIVE_LOGON` (Implemented) | Approach B: Custom LSA Package (SSP/AP) |
|---|---|---|
| **System Stability** | Runs in `LogonUI.exe`. Any crash only reloads LogonUI. | Runs inside `lsass.exe`. Any crash causes immediate Blue Screen (BSOD: `CRITICAL_PROCESS_DIED`). |
| **Driver Signing / WHQL** | Standard user-mode COM DLL. Does not require kernel/LSA driver signing. | Requires Microsoft WHQL / Authenticode kernel-compliant signatures for Windows 10/11 LSA Protection (`RunAsPPL`). |
| **Network TGT** | Establishes standard Kerberos Ticket Granting Ticket (TGT) for domain shares. | Requires complex Kerberos S4U2Self ticket exchange to access network shares. |
| **Fail-Open Behavior** | Easily falls back to password/PIN with `CPGSR_NO_CREDENTIAL_FINISHED`. | Complex fallback if LSA package fails. |

---

## Building the Project

### Prerequisites
- Windows 10 or Windows 11 (x64)
- Visual Studio 2019/2022 with **Desktop development with C++** or Windows SDK (10.0.19041+)
- CMake 3.15+ (optional)

### Build with CMake
```cmd
cd credential_provider
mkdir build
cd build
cmake .. -A x64
cmake --build . --config Release
```

### Build with MSBuild / Visual Studio
```cmd
cd credential_provider
msbuild PeekCredentialProvider.sln /p:Configuration=Release /p:Platform=x64
```
The output DLL will be generated at `credential_provider/x64/Release/PeekCredentialProvider.dll` (or `build/Release/PeekCredentialProvider.dll`).

---

## Installation & Registration

### 1. Register the Credential Provider (Admin Command Prompt)
```cmd
regsvr32.exe PeekCredentialProvider.dll
```
This registers:
- COM InProcServer: `HKCR\CLSID\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}`
- Windows Credential Provider: `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}`

### 2. Start the Engine Service
```cmd
python scripts/run_engine_service.py
```

### 3. Test on Lock Screen
Press `Win + L` to lock the workstation. The Peek tile will be visible alongside standard sign-in options.

### 4. Unregistering
```cmd
regsvr32.exe /u PeekCredentialProvider.dll
```
