# Security Architecture & Threat Model: Peek Face-Unlock for Windows

## 1. Executive Summary & Philosophy

Peek is an open-source biometric face-unlock credential provider for Windows 10 and 11. It provides convenient, low-friction workstation unlocking on commodity PC hardware equipped with standard RGB webcams.

Because Peek is engineered to run on consumer hardware without proprietary depth cameras or dedicated biometric co-processors, its security guarantees and threat model differ fundamentally from hardware-certified Windows Hello Face systems. This document explicitly outlines **what Peek protects against**, **what it does not protect against**, and the operational boundaries of the software.

---

## 2. Threat Model: What Peek Protects Against

Peek is designed to defend against common, low-cost physical and local software attacks:

### 2.1 Unauthorized Physical Access by Strangers / Bystanders
- **ArcFace Deep Metric Embedding**: Enforces cosine similarity matching (default threshold `0.48`) against 512-dimensional biometric embeddings. Random bystanders, coworkers, or individuals without substantial facial similarity to the enrolled user cannot unlock the workstation.
- **Temporal Verification Window**: Authentication requires any single enrolled biometric template to meet or exceed the similarity threshold, confirmed across at least 5 out of 7 consecutive frames in the temporal verification window. This prevents transient flashes, false alarms, or momentary alignment glitches from unlocking the machine.

### 2.2 Basic 2D Presentation Attacks (Spoofs)
Peek integrates a multi-cue passive liveness detection pipeline that is **designed to detect** common presentation attack instruments (PAIs):
- **Printed Photos (`photo_print`)**: Designed to detect via motion parallax analysis (lack of non-rigid facial deformation), high-frequency texture deficits, and eye micro-motion absence.
- **Smartphone & Tablet Screens (`photo_phone_screen`, `video_replay_phone`)**: Designed to detect via automated screen bezel detection (identifying high-contrast rectangular device borders), specular glass reflections, and planar depth consistency.
- **Desktop Monitor Replays (`video_replay_monitor`)**: Designed to detect via screen boundary edges, moiré interference patterns, and unnatural head dynamics.
- **Attack Resistance Benchmark**: For empirical measurement protocols, test datasets, and current evaluation benchmarks across presentation attack categories, see [docs/spoof_results.md](docs/spoof_results.md) (currently *not yet measured* on physical recorded clips).

### 2.3 Multiple Face Identities & Security Implications
- **Multi-Identity Capacity**: Peek supports enrolling up to 5 distinct authorized face identities per machine (e.g. multiple appearances, glasses on/off, or trusted family members).
- **Single Account Shared Authorization**: Crucially, every authorized identity unlocks the **exact same underlying Windows user account**.
- **Expansion of Attack Surface (FAR)**: Each additional enrolled biometric identity geometrically expands the acceptance volume in the embedding space, incrementally increasing the overall False Accept Rate (FAR). Workstations requiring higher security should limit enrollment to a single identity.

### 2.4 IPC Hijacking, Squatting & Replay Attacks
- **128-bit Challenge Nonce**: Every authentication attempt generates a cryptographically random 128-bit nonce. The engine must echo this exact nonce in the terminal `AUTHENTICATED` message. Replaying past IPC messages or responses fails validation.
- **First Pipe Instance Enforcement (`FILE_FLAG_FIRST_PIPE_INSTANCE`)**: Named pipes (`\\.\pipe\PeekEngine` and `\\.\pipe\PeekEnrollment`) are opened with strict instance flags. If a malicious process attempts to squat or pre-create the pipe before Peek starts, the service aborts immediately (fails closed).
- **Restrictive Security Descriptors (SDDL)**: Pipe DACLs grant access exclusively to `SYSTEM` (LogonUI), `Administrators`, and the authentic user SID. Remote connections are rejected via `PIPE_REJECT_REMOTE_CLIENTS`.

### 2.5 Offline Profile Data Theft
- **Windows DPAPI Encryption**: Enrolled face templates stored in `%LOCALAPPDATA%\Peek\Profiles\*.peek` are encrypted using Windows Data Protection API (`CryptProtectData`) combined with machine-bound entropy. Offline theft of disk files (e.g. removing the hard drive) does not reveal biometric embeddings without access to the user's Windows credentials.

### 2.6 Credential & Authorization Storage
- **Windows Password in LSA Private Secret**: The user's Windows password is encrypted and stored in a Windows Local Security Authority (LSA) private secret named `L$PeekCred_<SID>`. By operating system design, this secret is readable only by `SYSTEM` and processes holding elevated administrator privileges (`SeSecurityPrivilege`, `SeTcbPrivilege`).
- **Authorized Identity Registry Key**: The list of authorized biometric identity GUIDs is stored in the registry under `HKLM\SOFTWARE\Peek\Identities`. This key is strictly restricted by access control lists to administrators and `SYSTEM` write access.

### 2.7 Multi-Tier Lockout & Throttling
Peek separates anti-brute-force defense into two distinct, independent mechanisms:
1. **Lock-Screen Credential Provider Retry Limit**: The Windows Credential Provider UI allows up to **3 consecutive retries per tile selection**. If camera acquisition fails or an unrecognized face is presented 3 times in a single logon UI session, the CP immediately falls back to Windows PIN or Password for that selection.
2. **Engine-Level Rate Limiting & Lockout Tracker**: The background engine tracks authentication outcomes across sessions. If **5 failed authentication attempts** occur within a **10-minute sliding window**, the engine enforces a **5-minute lockout period**. During lockout, all incoming authentication requests are immediately rejected with an error, forcing logon via PIN or password.

---

## 3. Explicit Security Boundaries: What Peek DOES NOT Protect Against

Understanding Peek's limitations is critical for evaluating its deployment in sensitive or enterprise environments:

### 3.1 RGB Camera Limitations (Not Equivalent to Windows Hello Hardware)
> [!WARNING]
> Peek is **NOT** a certified Windows Hello hardware provider. It relies entirely on standard RGB color cameras and software-based computer vision.

- **Certified Windows Hello Hardware**: Requires dedicated Near-Infrared (NIR) illumination (850nm / 940nm), structured light or Time-of-Flight (ToF) 3D sensors, and hardware root-of-trust attestation. NIR sensors evaluate sub-dermal skin reflectance and physical 3D contours impervious to 2D displays.
- **Peek Passive RGB Constraints**:
  - Peek cannot measure absolute geometric depth or infrared reflectance.
  - Highly sophisticated, high-budget presentation attacks—such as custom silicone 3D masks, theatrical prosthetics, or hyper-realistic 3D animatronic sculptures—may evade passive RGB heuristics.
  - **Virtual Camera & `engine.json` Hardware Override**:
    - Hardware camera selection can be configured via `%ProgramData%\Peek\engine.json` (`{"camera_index": N}`).
    - To prevent unprivileged local users from tampering with camera selection or redirecting capture to a virtual loopback camera, `%ProgramData%\Peek` and `engine.json` are protected by DACLs allowing write access only to `SYSTEM` and `Administrators`.
    - The engine inspects the file's DACL at startup and **ignores `engine.json` if it is writable by non-admin users**.
    - However, if an administrator installs a virtual camera driver (DirectShow / Media Foundation filter) or points `engine.json` to a virtual camera, synthetic or deepfake video streams could be piped directly into the pipeline.

### 3.2 LSA Secret Storage & Administrator Access
- Peek utilizes Windows Local Security Authority (LSA) private secret `L$PeekCred_<SID>` to store the user's Windows password necessary to complete the unlock handshake during `GetSerialization`.
- **Administrative Boundary**: In the Windows security model, any process running with elevated `Administrator` privileges (or as `NT AUTHORITY\SYSTEM`) possesses the rights (`SeSecurityPrivilege`, `SeTcbPrivilege`) to inspect LSA private data and read process memory (e.g. using LSASS dumping tools).
- **Peek does not defend against a compromised Administrator or SYSTEM account**. An attacker with root/admin access on the local host can extract credentials regardless of the credential provider.

### 3.3 Malware Running in the User's Interactive Session
- If malicious software is executed under the interactive user's account, it operates within the same security token and possesses access to:
  - The user's DPAPI encryption master keys.
  - Files stored under `%LOCALAPPDATA%\Peek\Profiles\`.
  - The enrollment named pipe (`\\.\pipe\PeekEnrollment`).
- DPAPI protects against offline disk theft and other non-administrative users on the machine; it cannot isolate a user's data from malware running as that same user.

### 3.4 Denial of Service via Intentional Lockout
- Because Peek enforces engine-level lockout after 5 failed authentication attempts within 10 minutes (and CP retry limits after 3 failures), an unauthorized individual standing in front of the workstation camera can intentionally trigger failed attempts to lock out face unlock.
- This results in a localized Denial of Service (DoS) for the biometric modality, forcing the genuine user to manually enter their PIN or password.

---

## 4. Best Practices for Hardening Peek Deployments

For optimal security on workstations running Peek:
1. **Enable BitLocker Drive Encryption**: Ensures DPAPI keys and Windows operating system files cannot be modified offline.
2. **Require Windows Hello PIN as Primary Fallback**: Ensure the user has a strong, complex PIN or alphanumeric password configured.
3. **Do Not Share Accounts**: Each physical individual should have a separate Windows user account with distinct biometric enrollments. Limit enrolled identities per user (maximum 5).
4. **Keep Windows Defender / EDR Active**: Prevents malware from injecting unauthorized virtual camera drivers or dumping process memory.
5. **Secure ProgramData and Logs Directory**: The `%ProgramData%\Peek` directory, `engine.json`, and `logs` folder are configured with protected ACLs (SYSTEM and Administrators FullControl, Users ReadAndExecute). Never grant write permissions to unprivileged users.

---

## 5. Vulnerability Disclosure

If you discover a security vulnerability in Peek (e.g., IPC privilege escalation, memory safety flaw, or bypass of the dual-gate authorization logic), please report it responsibly:
- Email the maintainers directly with reproduction details, crash dumps, and proof-of-concept clips.
- Please do not submit public issues on GitHub for undisclosed security vulnerabilities.
