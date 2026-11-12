# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Enterprise GPO policy templates for managing update channels and automated checks across Active Directory domains.
- Infrared (IR) structured light camera integration profile.

---

## [0.2.0-beta.1] - 2026-11-12

### Added
- **Production Packaging & Inno Setup 6 Installer**:
  - Versioned, signing-ready installer `Peek-Setup-0.2.0-beta.1-x64.exe` targeting 64-bit Windows 10 (build 17763+) and Windows 11.
  - Clean in-place upgrade engine preserving biometric profiles, `HKLM\SOFTWARE\Peek\Identities`, machine entropy, and LSA secrets.
  - Automated pre-upgrade backup to `%ProgramData%\Peek\backup\<previous-version>\` with single-backup retention.
  - Explicit downgrade refusal gate preventing accidental installation of older builds.
  - Complete data removal option via `/REMOVEDATA=1` or interactive prompt, purging LSA secrets, registry keys, and face models.
- **Engine Distribution & CRT Hardening**:
  - PyInstaller one-DIR build (`<InstallDir>\engine\`) eliminating runtime extraction overhead and temporary directory clutter.
  - C++ Credential Provider compiled with static C Runtime (`/MT`) to prevent CRT DLL resolution failures at the Windows LogonUI desktop.
  - Self-contained single-file `.NET 8` setup application (`PeekSetup.exe`) with built-in compression.
- **Supply-Chain Security & Hash Pinning**:
  - Pinned SHA-256 cryptographic digests in `engine/models/download_models.py` for InsightFace SCRFD detector, MobileFaceNet embedder, and MediaPipe selfie segmentation models.
  - Local caching of verified model weights under `build/models/`.
  - Added comprehensive `THIRD_PARTY_NOTICES.md` documenting upstream licenses and sources.
- **Automatic Update Checking Service (`PeekUpdate.exe`)**:
  - Independent .NET 8 CLI utility supporting `--check`, `--check --now`, `--apply` (elevated), and `--status` modes.
  - User-level scheduled task running at user logon and recurring every 12 hours with a randomized jitter delay up to 30 minutes.
  - Dual ECDSA P-256 public key signature verification with key rotation support and 8-stage verification pipeline.
  - Native Windows toast notifications and system tray balloon fallback for update availability.
  - 7-day snooze and skip-version controls stored in `%LOCALAPPDATA%\Peek\update-settings.json`.
  - Machine-wide policy configuration under `HKLM\SOFTWARE\Peek\Updates` (`AutoCheck`, `Channel`, `FeedUrl`).
- **Peek Setup UI Updates Center**:
  - Modern modal dialog in `PeekSetup.exe` displaying current channel, installed version, last checked time, and release notes link.
  - One-click update check and user-approved download/install flow with live progress indicators.
  - Critical security update banner for mandatory or zero-day security releases.
- **Automated CI/CD & Offline Key Security**:
  - GitHub Actions CI workflow (`.github/workflows/ci.yml`) covering Python pytest suite, .NET xUnit tests, and C++ MSBuild on `windows-latest`.
  - Automated Release workflow (`.github/workflows/release.yml`) triggered by `v*.*.*` tags to generate draft GitHub releases.
  - **Offline Manifest Signing**: CI workflow explicitly executes with `-UnsignedManifest` producing unsigned candidate manifests. Private signing keys are strictly excluded from CI environments and remain in offline maintainer custody.

### Changed
- **Wire Protocol & Version Handshake**:
  - Updated IPC handshake: `ENGINE_READY` message now carries `engine_version` and `protocol_version`.
  - Credential Provider validates SemVer major and minor compatibility; on mismatch, it fails open immediately with `"Peek was just updated, restart your PC"` and prompts the user to sign in with password/PIN.
- **Multi-User Logon Integration**:
  - Scheduled task principal adjusted to `BUILTIN\Users` (`RunLevel: Limited`), enabling seamless logon and unlock across all local user accounts on shared PCs.

### Security
- **Strict Process & Network Isolation**: Zero network, socket, or HTTP code exists in `PeekEngine.exe` or `PeekCredentialProvider.dll`. All networking is strictly isolated to `PeekUpdate.exe`.
- **Privilege Escalation Protection**: `FeedUrl` can only be modified through administrator-owned `HKLM\SOFTWARE\Peek\Updates`. User settings are prevented from altering the feed endpoint.
- **Two-Phase Update Validation**: Downloaded binaries are re-verified for SHA-256, Authenticode signatures, and manifest authenticity within an administrator-only staging directory prior to execution.
- **Fail-Open Invariant**: Windows sign-in (Password and PIN) remains available at all times as a non-blocking fallback if camera, engine, or update errors occur.

---

## [0.1.0] - 2026-09-15

### Added
- Initial proof-of-concept release for Peek biometric face recognition.
- Custom Windows Credential Provider DLL for Windows LogonUI integration.
- Python face recognition engine utilizing InsightFace and OpenCV.
- Basic WPF enrollment UI and DPAPI-wrapped Windows credential storage.
