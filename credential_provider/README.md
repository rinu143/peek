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

## Security Model & Residual Risk Disclosure: Password-Vault Unlock Mechanism

> [!CAUTION]
> **CRITICAL SECURITY DISCLOSURE & RESIDUAL RISK NOTICE**
>
> **Reversible Password Storage & Replay**:
> Peek's workstation unlock mechanism stores the user's actual Windows account password in a DPAPI-wrapped file (`%LOCALAPPDATA%\Peek\Profiles\<profile_id>.secret`) protected with machine-bound entropy (stored separately in `%ProgramData%\Peek\machine_entropy.bin` or Windows `MachineGuid`). Upon a genuine face and liveness match (`AUTHENTICATED` with `isAuthorizedToUnlock == true`), the Credential Provider decrypts that file and replays the plaintext password to Winlogon via a `KERB_INTERACTIVE_LOGON` structure.
>
> **Fundamental Difference from Windows Hello Face**:
> This architecture differs materially from Windows Hello Face:
> - **Windows Hello Face** never stores the Windows account password in reversible form. It uses asymmetric TPM 2.0 / VBS hardware-backed key pairs where biometric authentication unlocks access to the private key for Kerberos/FIDO sign-in.
> - **Peek Password Vault** stores the user's real Windows account password in reversible form and replays it.
>
> **Residual Risk of DPAPI User-Context Decryption**:
> Because DPAPI (`CryptProtectData`/`CryptUnprotectData`) user-scope encryption decrypts under the user's own logon session:
> - **Any process or malware achieving code execution under the enrolled user's own Windows account token can locate the entropy value and invoke `CryptUnprotectData` to recover the user's plaintext Windows account password.**
> - The Credential Provider is not the only entity that can decrypt this secret; any process running under that user's logon session can do so.
>
> **What Machine-Bound Entropy Does and Does NOT Protect Against**:
> - **What it protects against**: Defense-in-depth against casual or scripted decryption, and prevents offline decryption if `<profile_id>.secret` is copied to another machine.
> - **What it does NOT protect against**: It does NOT prevent malware or local processes in the user's session from locating the entropy value, unprotecting the secret, and obtaining the plaintext password. This pass does not eliminate that risk, and DPAPI wrapping must not be mistaken for a complete solution.

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
