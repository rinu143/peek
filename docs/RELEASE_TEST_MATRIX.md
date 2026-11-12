# Peek Release Test Matrix & Compatibility Guide

This document defines the formal validation matrix and acceptance criteria for Peek Face Authentication across Windows operating system builds, monthly cumulative updates, security enforcement policies (Smart App Control), and multi-user configurations.

Every release candidate must be validated against this matrix before final public distribution.

---

## 1. Operating System Compatibility Matrix

| Operating System | Minimum Build | Architecture | Hardware Profile | Status / Support |
| :--- | :--- | :--- | :--- | :--- |
| **Windows 10 Enterprise / Pro (22H2)** | 10.0.19045 | x64 | Standard webcam (RGB 720p/1080p), TPM 2.0 | **Tier 1 (Full Support)** |
| **Windows 10 Pro / Home (21H2)** | 10.0.19044 | x64 | Standard webcam (RGB 720p), TPM optional | **Tier 1 (Full Support)** |
| **Windows 10 Enterprise LTSC 2019 / 2021** | 10.0.17763 | x64 | Standard webcam, minimum installer baseline | **Tier 2 (Certified)** |
| **Windows 11 Pro / Enterprise (22H2)** | 10.0.22621 | x64 | Standard webcam, Secure Boot, TPM 2.0 | **Tier 1 (Full Support)** |
| **Windows 11 Pro / Enterprise (23H2)** | 10.0.22631 | x64 | Standard webcam, Secure Boot, TPM 2.0 | **Tier 1 (Full Support)** |
| **Windows 11 Pro / Enterprise (24H2)** | 10.0.26100 | x64 | Standard webcam, Secure Boot, TPM 2.0 | **Tier 1 (Full Support)** |
| **Windows Server 2022 / 2025** | 10.0.20348+ | x64 | Desktop Experience enabled, USB webcam | **Tier 3 (Compatible)** |

> [!NOTE]
> 32-bit (x86) and ARM64 architectures are blocked at installer invocation (`ArchitecturesAllowed=x64compatible`).

---

## 2. Windows Cumulative Update (Patch Tuesday) Resilience

### Context & Risk
Monthly Windows Cumulative Updates frequently update core security subsystems, including:
- `LogonUI.exe` and the Credential Provider loading mechanism.
- `LSASS.exe` and Local Security Authority credential caching.
- Universal C Runtime (UCRT) and Windows system DLLs.

### Verification Protocol
1. **Pre-Update State**:
   - Install Peek on a clean VM running the prior month's cumulative update level (N-1).
   - Complete face enrollment and Windows password linking.
   - Verify lock screen unlock functions properly.
2. **Apply Update**:
   - Check for updates via Windows Update and install the latest cumulative quality update (N).
   - Perform the required system reboot.
3. **Post-Reboot Verification**:
   - **Credential Provider Initialization**:
     - Check `%ProgramData%\Peek\logs\credential_provider.log`.
     - Confirm `PeekCredentialProvider.dll` initialized without COM registration failures.
     - Confirm static CRT (`/MT`) linkage prevented missing DLL errors.
   - **LSA Secret Retrieval**:
     - Lock the workstation (`Win + L`).
     - Verify face unlock successfully retrieves and unwraps `L$PeekCred_<SID>`.
   - **Engine Startup**:
     - Confirm `PeekEngine.exe` automatically started via the `PeekEngine` scheduled task for the logged-on user.
   - **Fail-Open Check**:
     - Obscure the camera; verify the standard Windows Password and PIN tiles remain fully functional.

---

## 3. Smart App Control (SAC) & Code Integrity Matrix

### Context & Risk
Windows 11 incorporates **Smart App Control (SAC)**, an AI-powered cloud reputation service that blocks untrusted or unsigned binaries downloaded from the Internet (carrying the Mark-of-the-Web zone identifier `Zone.Identifier=3`).

### Test Environments

| Configuration | SAC Mode | Expected Behavior (Signed Release) | Expected Behavior (Unsigned Local Build) |
| :--- | :--- | :--- | :--- |
| **Windows 11 22H2+ Clean VM** | **Evaluation** | Installer runs with no warnings; app loads smoothly. | SmartScreen warning banner; user can choose "Run anyway". |
| **Windows 11 22H2+ Enforce** | **On (Enforce)** | Installer executes without prompt; Credential Provider loads in LogonUI. | Blocked with notification: *"Smart App Control blocked this app for your protection"*. |
| **Windows 11 WDAC Audit** | **Audit Mode** | Event log records allow event; zero code integrity violations. | Audit event logged under Event ID 3076 (`CodeIntegrity/Operational`). |

### Verification Steps (Signed Builds)
1. Set Mark-of-the-Web on the installer:
   ```powershell
   Set-Content -Path "Peek-Setup-0.2.0-x64.exe:Zone.Identifier" -Value "[ZoneTransfer]`r`nZoneId=3"
   ```
2. Double-click the installer:
   - Installer launches without SmartScreen or SAC blocking screens.
3. Inspect Windows Event Viewer:
   - Navigate to `Applications and Services Logs` -> `Microsoft` -> `Windows` -> `CodeIntegrity` -> `Operational`.
   - Verify no Event ID 3077 (block) entries exist for `PeekCredentialProvider.dll`, `PeekEngine.exe`, or `PeekSetup.exe`.
4. Verify Authenticode signature status:
   ```powershell
   Get-AuthenticodeSignature "Peek-Setup-0.2.0-x64.exe"
   Get-AuthenticodeSignature "$env:ProgramFiles\Peek\PeekCredentialProvider.dll"
   ```
   - Must return `Status: Valid` with a valid timestamp.

---

## 4. Multi-User & Fast User Switching (FUS) Matrix

### Context & Risk
Shared family and enterprise workstations frequently host multiple local or domain user accounts. Peek must enforce strict session and cryptographic isolation between users.

### Test Matrix

| Scenario ID | User A State | User B State | Action | Expected Result | Pass/Fail |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **FUS-01** | Enrolled in Peek | Not enrolled | User A locks PC (`Win + L`); User B switches user | User A unlocks with face; User B sees standard Password/PIN tiles with Peek fail-open message | [ ] |
| **FUS-02** | Enrolled in Peek | Enrolled in Peek | User A locks PC; User A looks at camera | User A session unlocks (< 1.5s). User B session is untouched. | [ ] |
| **FUS-03** | Enrolled in Peek | Enrolled in Peek | User A locks PC; User B looks at camera | Unlock rejected; status displays `"Face not recognized"`. User A remains locked. | [ ] |
| **FUS-04** | Logged on | Logged on | Switch user via Fast User Switching | Scheduled task runs independent `PeekEngine` process under User B's token. Engine named pipe enforces local session access control. | [ ] |
| **FUS-05** | Enrolled in Peek | Enrolled in Peek | User B attempts to inspect User A's data | User B cannot read User A's profile (`%LOCALAPPDATA%\Peek\profiles\`) or User A's LSA secret (`L$PeekCred_<UserA_SID>`). | [ ] |

---

## 5. Environmental & Biometric Boundary Conditions

| Condition | Lux Level | Test Description | Acceptance Threshold |
| :--- | :--- | :--- | :--- |
| **Standard Indoor** | 300–500 lux | Balanced fluorescent / LED office lighting | TAR $\ge 98\%$, Decision Time $\le 1.2\text{s}$ |
| **Direct Sunlight / Glare** | 800–1200 lux | Window or sunlight directly hitting camera lens or face | TAR $\ge 92\%$, No false positive spoof accepts |
| **Backlit Environment** | 600 lux background | Bright window behind subject's head | TAR $\ge 90\%$, Detector adjusts exposure dynamically |
| **Low-Light / Night** | 20–50 lux | Dark room with monitor illumination only | TAR $\ge 85\%$, Fails open gracefully to password if face unresolvable |
| **Pose Variation (Yaw)** | 400 lux | Head turned $\pm 15^\circ$ left/right | Successful recognition |
| **Pose Variation (Pitch)**| 400 lux | Head tilted $\pm 15^\circ$ up/down | Successful recognition |
| **Facial Occlusions** | 400 lux | Prescription glasses, hats, natural facial hair | Matching succeeds without false rejections |

---

## 6. Formal Sign-Off Record Template

Before any public release, copy the sign-off block below into the release notes or release tracking ticket:

```markdown
### Release Compatibility Sign-Off

- **Release Version**: 0.2.0
- **Build Timestamp**: YYYY-MM-DD HH:mm:ss UTC
- **Installer SHA-256**: <hash>
- **Lead QA / Maintainer**: @<github_handle>

| Environment | OS Build | Status | Notes / Evidence |
| :--- | :--- | :--- | :--- |
| Clean Windows 10 x64 | 10.0.19045.xxxx | [PASS/FAIL] | Clean install, unlock verified |
| Clean Windows 11 x64 | 10.0.22631.xxxx | [PASS/FAIL] | Clean install, unlock verified |
| Windows Cumulative Update | KBxxxxxxx | [PASS/FAIL] | Post-reboot persistence OK |
| Smart App Control (SAC) | Windows 11 Enforce | [PASS/FAIL] | Zero MotW block events |
| Multi-User FUS | 2 local users | [PASS/FAIL] | Cross-user rejection confirmed |
| Update Checking & Apply | PeekUpdate --check | [PASS/FAIL] | Manifest verified & elevated install OK |

**Approval Status**: [APPROVED / BLOCKED]
```
