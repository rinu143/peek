# Peek Automatic Update Service Architecture & Security Guide

This document describes the design, cryptographic verification pipeline, privacy guarantees, policy controls, and verification procedures for the Peek Automatic Update Service (`PeekUpdate.exe`).

---

## 1. Architecture & Design Principles

The Peek update architecture adheres to strict security and least-privilege principles:

1. **Strict Process & Network Isolation**:
   - Network communication is **exclusively restricted** to `PeekUpdate.exe`.
   - The face recognition engine (`PeekEngine.exe`) and the Windows Credential Provider (`PeekCredentialProvider.dll`) contain **zero network code, sockets, or HTTP dependencies**.
2. **No Silent Installs**:
   - `PeekUpdate.exe` is primarily an update **checker** and staging coordinator.
   - Background runs (via scheduled task) only check for updates and present non-intrusive notifications.
   - Installation is never silent or unattended. It requires explicit user approval and Windows User Account Control (UAC) elevation.
3. **Defense in Depth & Two-Phase Verification**:
   - Updates are downloaded to user space (`%LOCALAPPDATA%\Peek\updates\<ver>`), verified, and then handed off to an elevated process.
   - The elevated process copies the installer into an administrator-only protected directory (`%ProgramData%\Peek\updates\<ver>`) and **re-verifies** SHA-256, Authenticode signatures, and manifest cryptographic signatures before executing the installer.
4. **Fail-Open Workstation Fallback**:
   - If an update fails during installation or if the post-installation health check reports anomalies, `PeekSetup.exe --rollback` restores the previous working version.
   - Standard Windows PIN and password logon remain active and unaffected at all times.

---

## 2. Update Manifest Specification

Update manifests are hosted over HTTPS (e.g., `https://<update-host>/updates/stable.json` and `beta.json`).

### Manifest Schema (Version 1)

```json
{
  "schema": 1,
  "channel": "stable",
  "version": "0.2.1",
  "released": "2026-11-12T12:00:00Z",
  "min_supported_version": "0.1.0",
  "critical": false,
  "notes_url": "https://github.com/rinu143/peek/releases/tag/v0.2.1",
  "url": "https://github.com/rinu143/peek/releases/download/v0.2.1/Peek-Setup-0.2.1-x64.exe",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "size": 137402880,
  "publisher_thumbprint": "A1B2C3D4E5F678901234567890ABCDEF12345678",
  "expires": "2026-12-12T12:00:00Z",
  "signature": "MEUCIQD..."
}
```

### Cryptographic Signing & Canonicalization

1. **Canonical JSON**:
   - The manifest is formatted into a compact, deterministic string with keys sorted in ascending ASCII order and all whitespace removed.
   - The `signature` field is excluded from canonicalization:
     `{"channel":"...","critical":false,"expires":"...","min_supported_version":"...","notes_url":"...","publisher_thumbprint":"...","released":"...","schema":1,"sha256":"...","size":...,"url":"...","version":"..."}`
2. **Signature Algorithm**:
   - ECDSA P-256 (`secp256r1`) with SHA-256.
   - Supports both IEEE P1363 (raw 64-byte `r||s`) and RFC 3279 ASN.1 DER formats.
3. **Key Rotation Support**:
   - Two public keys are embedded into `PeekUpdate.exe` (Primary and Secondary).
   - A manifest signature is valid if it verifies successfully against either authorized public key.

---

## 3. Strict Verification Pipeline (Rejection Gates)

Every update check enforces eight mandatory validation gates in sequential order. If any condition fails, the update is rejected immediately and logged:

| Gate | Validation Rule | Failure Behavior |
|------|-----------------|------------------|
| **1. Schema Gate** | Manifest `schema` must equal `1`. | Reject: Unknown manifest schema. |
| **2. Cryptographic Gate** | ECDSA signature must verify using embedded primary or secondary public keys. | Reject: Manifest signature verification failed. |
| **3. Freshness Gate** | Current UTC time must not exceed `expires`, and `expires` must be within 30 days of release. | Reject: Manifest has expired. |
| **4. SemVer Gate** | Manifest `version` must be strictly greater than currently installed version. | Reject: Version is not an upgrade (downgrades refused). |
| **5. Host Allow-List Gate** | Download URL host must match configured feed host, `github.com`, or `objects.githubusercontent.com`. | Reject: Untrusted download host. |
| **6. Size Gate** | Payload `size` must be positive and must not exceed 500 MB (524,288,000 bytes). | Reject: Payload size exceeds safety limit. |
| **7. SHA-256 Integrity Gate** | Downloaded installer SHA-256 must exactly match manifest `sha256`. | Reject: File checksum mismatch. Download discarded. |
| **8. Authenticode Gate** | If `publisher_thumbprint` is specified, `WinVerifyTrust` must validate the binary signature and certificate thumbprint. | Reject: Authenticode verification failed. |

---

## 4. Privacy Guarantees

Peek Update is built with absolute privacy as a primary tenet:

- **Zero Telemetry**: No usage metrics, device information, hardware specs, camera models, face enrollment statistics, or IP telemetry are ever collected or transmitted.
- **No Identifiers**: Requests do not include hardware GUIDs, machine SIDs, username tokens, or session IDs.
- **Fixed User-Agent**: The HTTP request header contains only `PeekUpdate/<version>` (e.g., `PeekUpdate/0.2.0-beta.1`).
- **Standard TLS**: Employs OS-default TLS 1.2 and TLS 1.3 stack.
- **Silent When Offline**: If DNS resolution fails, the network is unreachable, or the request times out (15 seconds limit), Peek Update exits silently without raising alarming dialogs.
- **ETag Caching**: Utilizes HTTP `ETag` and `If-None-Match` headers to receive `304 Not Modified`, preventing wasteful bandwidth consumption.

---

## 5. User & Administrator Configuration Controls

### A. Graphical Interface (Peek Setup)
1. Open **Peek Setup** from the Start Menu.
2. Click the **Updates** button in the upper header.
3. Toggle **"Check for updates automatically (recommended)"** to enable or disable checks.
4. Switch channels between **Stable** and **Beta**.
5. Use **Check now** to trigger an immediate check.

### B. Per-User Settings
Stored at `%LOCALAPPDATA%\Peek\update-settings.json`:
```json
{
  "auto_check": true,
  "channel": "stable",
  "snooze_until": null,
  "skipped_version": null
}
```
*Note: The user settings file cannot alter the update feed URL.*

### C. Enterprise / Machine Policy (HKLM)
Administrators can enforce machine-wide update policies under `HKLM\SOFTWARE\Peek\Updates` (admin-writable only):

| Registry Value | Type | Description |
|----------------|------|-------------|
| `AutoCheck` | `DWORD` | `1` = Enable automatic background checks, `0` = Disable all checks. |
| `Channel` | `String` | `"stable"` or `"beta"`. Overrides user preference if managed. |
| `FeedUrl` | `String` | Base URL for update manifests (e.g., `https://updates.internal.corp/peek`). |

> [!IMPORTANT]
> **Security Invariant**: `FeedUrl` can only be configured via `HKLM\SOFTWARE\Peek\Updates`. Any attempt to set `feed_url` in `%LOCALAPPDATA%\Peek\update-settings.json` is strictly ignored to prevent local privilege escalation attacks.

---

## 6. Scheduled Task & CLI Modes

### Scheduled Task Specification
- **Task Name**: `PeekUpdate`
- **Schedule**: Executes at user logon and every 12 hours.
- **Random Delay**: Up to 30 minutes (prevents thundering herd on update mirrors).
- **Execution**: Runs `PeekUpdate.exe --check` with limited user privileges.

### CLI Usage

```powershell
# 1. Background check (respects snooze and skip settings)
PeekUpdate.exe --check

# 2. Interactive check (ignores snooze/skip; reports current status)
PeekUpdate.exe --check --now

# 3. Download update payload to local cache
PeekUpdate.exe --download

# 4. Elevated apply & install (runs UAC-elevated installer execution)
PeekUpdate.exe --apply --installer-path "C:\Users\<user>\AppData\Local\Peek\updates\0.2.1\Peek-Setup-0.2.1-x64.exe" --manifest-path "C:\Users\<user>\AppData\Local\Peek\updates\0.2.1\manifest.json"

# 5. Query current version, policy, and state
PeekUpdate.exe --status

# 6. Register or re-register user scheduled task
PeekUpdate.exe --register-task
```

---

## 7. Verification & Test Procedures

### Automated Unit Tests
Run the unit test suite covering all rejection gates:
```powershell
dotnet test app\PeekSetup.Tests\PeekSetup.Tests.csproj --filter "FullyQualifiedName~PeekUpdateTests"
```
Tests pass for:
- Valid manifest and valid signature.
- Tampered manifest payload (signature mismatch).
- Invalid signature format / corrupted bytes.
- Expired manifest (past expiration date and > 30-day freshness window).
- Downgrade attempt (version <= current).
- Disallowed URL host (e.g. `malicious-domain.com`).
- Oversized payload (> 500 MB).
- Unsupported schema version (`schema: 2`).
- Checksum mismatch (corrupted installer byte stream).
- Authenticode signature validation and publisher thumbprint verification.
- Policy override precedence (`HKLM` overrides `%LOCALAPPDATA%`).

### Manual Security Test Scenarios

#### Scenario A: Offline / Network Outage
1. Disconnect Ethernet / Wi-Fi or configure an invalid DNS entry.
2. Execute:
   ```powershell
   PeekUpdate.exe --check --now
   ```
3. **Expected Result**: Exits gracefully with code `0`. Logs `"Network offline or host unreachable"`. No crash, no UI popup.

#### Scenario B: Replay / Expired Manifest Test
1. Create a test manifest with an expired date:
   ```json
   "expires": "2025-01-01T00:00:00Z"
   ```
2. Verify signature or run verification test.
3. **Expected Result**: `PeekUpdate` rejects the manifest with `"Manifest has expired (2025-01-01T00:00:00Z). Update refused."`

#### Scenario C: Tampered Installer Payload (Swap Attack)
1. Download a genuine manifest and installer.
2. Replace the installer `.exe` with a modified binary or arbitrary file while leaving the manifest unchanged.
3. Run:
   ```powershell
   PeekUpdate.exe --apply --installer-path "tampered.exe" --manifest-path "manifest.json"
   ```
4. **Expected Result**: Elevated installer computes SHA-256, detects mismatch, deletes the staged file, and exits with code `1`. The installer is never executed.

#### Scenario D: Registry Policy Lockout
1. In an elevated PowerShell prompt, set:
   ```powershell
   Set-ItemProperty -Path "HKLM:\SOFTWARE\Peek\Updates" -Name "AutoCheck" -Value 0 -Type DWord
   ```
2. In user space, configure `%LOCALAPPDATA%\Peek\update-settings.json` with `"auto_check": true`.
3. Run `PeekUpdate.exe --status`.
4. **Expected Result**: Status reports `AutoCheck: Disabled (Enforced by HKLM Policy)`. Background `--check` exits immediately without making any HTTP requests.
