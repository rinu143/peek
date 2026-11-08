# Security Architecture & Threat Model: Peek Face-Unlock for Windows

## 1. Executive Summary & Philosophy

Peek is an open-source biometric face-unlock credential provider for Windows 10 and 11. It provides convenient, low-friction workstation unlocking on commodity PC hardware equipped with standard RGB webcams.

Because Peek is engineered to run on consumer hardware without proprietary depth cameras or dedicated biometric co-processors, its security guarantees and threat model differ fundamentally from hardware-certified Windows Hello Face systems. This document explicitly outlines **what Peek protects against**, **what it does not protect against**, and the operational boundaries of the software.

---

## 2. Threat Model: What Peek Protects Against

Peek is designed to defend against common, low-cost physical and local software attacks:

### 2.1 Unauthorized Physical Access by Strangers / Bystanders
- **ArcFace Deep Metric Embedding**: Enforces cosine similarity matching (default threshold `0.48`) against 512-dimensional biometric embeddings. Random bystanders, coworkers, or individuals without substantial facial similarity to the enrolled user cannot unlock the workstation.
- **Multi-Pose Template Verification**: Users enroll multiple distinct head angles (Center, Left, Right, Up, Down, etc.). Unlock requires matching against enrolled pose clusters, eliminating single-angle coincidental matches.

### 2.2 Basic 2D Presentation Attacks (Spoofs)
Peek integrates a multi-cue passive liveness detection pipeline that rejects common presentation attack instruments (PAIs):
- **Printed Photos (`photo_print`)**: Detected and vetoed via motion parallax analysis (lack of non-rigid facial deformation), high-frequency texture deficits, and eye micro-motion absence.
- **Smartphone & Tablet Screens (`photo_phone_screen`, `video_replay_phone`)**: Detected and vetoed via automated screen bezel detection (identifying high-contrast rectangular device borders), specular glass reflections, and planar depth consistency.
- **Desktop Monitor Replays (`video_replay_monitor`)**: Detected via screen boundary edges, moiré interference patterns, and unnatural head dynamics.

### 2.3 IPC Hijacking, Squatting & Replay Attacks
- **128-bit Challenge Nonce**: Every authentication attempt generates a cryptographically random 128-bit nonce. The engine must echo this exact nonce in the terminal `AUTHENTICATED` message. Replaying past IPC messages or responses fails validation.
- **First Pipe Instance Enforcement (`FILE_FLAG_FIRST_PIPE_INSTANCE`)**: Named pipes (`\\.\pipe\PeekEngine` and `\\.\pipe\PeekEnrollment`) are opened with strict instance flags. If a malicious process attempts to squat or pre-create the pipe before Peek starts, the service aborts immediately (fails closed).
- **Restrictive Security Descriptors (SDDL)**: Pipe DACLs grant access exclusively to `SYSTEM` (LogonUI), `Administrators`, and the authentic user SID. Remote connections are rejected via `PIPE_REJECT_REMOTE_CLIENTS`.

### 2.4 Offline Profile Data Theft
- **Windows DPAPI Encryption**: Enrolled face templates stored in `%LOCALAPPDATA%\Peek\Profiles\*.peek` are encrypted using Windows Data Protection API (`CryptProtectData`) combined with machine-bound entropy. Offline theft of disk files (e.g. removing the hard drive) does not reveal biometric embeddings without access to the user's Windows credentials.

### 2.5 Brute-Force Guessing & Lockout
- **Attempt Throttling & Lockout Tracker**: The engine enforces a lockout threshold: after 3 consecutive failed biometric attempts, the credential provider enforces lockout and disables face unlock for the session, requiring the user to authenticate via their Windows PIN or Password.

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
  - Deepfake Virtual Camera Injections: If an attacker compromises the OS and installs a malicious virtual camera driver (DirectShow / Media Foundation filter), synthetic video streams could be piped directly to the engine unless hardware driver signing is strictly enforced.

### 3.2 LSA Secret Storage & Administrator Access
- Peek utilizes Windows Local Security Authority (LSA) private data (`Peek_Auth_<SID>`) to store encrypted credential serialization data necessary to complete the unlock handshake during `GetSerialization`.
- **Administrative Boundary**: In the Windows security model, any process running with elevated `Administrator` privileges (or as `NT AUTHORITY\SYSTEM`) possesses the rights (`SeSecurityPrivilege`, `SeTcbPrivilege`) to inspect LSA private data and read process memory (e.g. using LSASS dumping tools).
- **Peek does not defend against a compromised Administrator or SYSTEM account**. An attacker with root/admin access on the local host can extract credentials regardless of the credential provider.

### 3.3 Malware Running in the User's Interactive Session
- If malicious software is executed under the interactive user's account, it operates within the same security token and possesses access to:
  - The user's DPAPI encryption master keys.
  - Files stored under `%LOCALAPPDATA%\Peek\Profiles\`.
  - The enrollment named pipe (`\\.\pipe\PeekEnrollment`).
- DPAPI protects against offline disk theft and other non-administrative users on the machine; it cannot isolate a user's data from malware running as that same user.

### 3.4 Denial of Service via Intentional Lockout
- Because Peek locks out face authentication after 3 failed attempts, an attacker standing in front of the workstation camera can intentionally trigger failed attempts to lock out face unlock.
- This results in a localized Denial of Service (DoS) for the biometric modality, forcing the genuine user to manually enter their PIN or password.

---

## 4. Best Practices for Hardening Peek Deployments

For optimal security on workstations running Peek:
1. **Enable BitLocker Drive Encryption**: Ensures DPAPI keys and Windows operating system files cannot be modified offline.
2. **Require Windows Hello PIN as Primary Fallback**: Ensure the user has a strong, complex PIN or alphanumeric password configured.
3. **Do Not Share Accounts**: Each physical individual should have a separate Windows user account with distinct biometric enrollments.
4. **Keep Windows Defender / EDR Active**: Prevents malware from injecting unauthorized virtual camera drivers or dumping process memory.
5. **Secure the Logs Directory**: The `%ProgramData%\Peek\logs` folder is configured with protected ACLs (SYSTEM and Administrators FullControl, Users ReadOnly). Do not grant write access to standard users.

---

## 5. Vulnerability Disclosure

If you discover a security vulnerability in Peek (e.g., IPC privilege escalation, memory safety flaw, or bypass of the dual-gate authorization logic), please report it responsibly:
- Email the maintainers directly with reproduction details, crash dumps, and proof-of-concept clips.
- Please do not submit public issues on GitHub for undisclosed security vulnerabilities.
