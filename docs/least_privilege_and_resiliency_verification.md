# Peek Face Engine: Least Privilege, Resiliency & Performance Verification Guide

This document specifies the architecture, verification procedures, and manual test checklist for the **Least-Privilege Engine Execution, Fault Recovery, Camera Selection, and Latency/Resource Instrumentation** on Windows 10/11.

---

## 1. Least-Privilege Architecture & Verification

The Peek engine service previously registered its logon task with elevated `RunLevel Highest`. Under the least-privilege model:

### Task Configuration
In both [install.ps1](../install.ps1) and [scripts/packaging/install.ps1](../scripts/packaging/install.ps1):
- **Principal RunLevel**: Configured to `-RunLevel Limited` (and `/RL LIMITED /F` in the `schtasks.exe` fallback).
- **Task Settings Set**:
  - `ExecutionTimeLimit = [TimeSpan]::Zero` (no 72-hour execution time limit killing the background service).
  - `RestartCount = 3`
  - `RestartInterval = [TimeSpan]::FromMinutes(1)` (1 minute apart)
  - `MultipleInstances = IgnoreNew`
  - `StartWhenAvailable = $true`

### System Subsystem Compatibility Matrix Under Standard User (Limited)

| Subsystem | Standard User Mechanism | Elevation Required? | Verification Status |
| :--- | :--- | :--- | :--- |
| **Media Foundation (MSMF) Camera** | Windows Media Foundation captures camera frames via standard user device ACLs. | **No** | Verified; auto-selection and frame capture operate without elevation. |
| **Authentication Named Pipe** | `\\.\pipe\PeekEngine` created with SDDL granting `(A;;GA;;;SY)` for SYSTEM LogonUI, `(A;;GA;;;BA)` for Administrators, and the process user SID `(A;;GA;;;UserSID)`. | **No** | Verified; LogonUI (SYSTEM) connects across desktop boundaries while engine runs as standard user. |
| **Enrollment Named Pipe** | `\\.\pipe\PeekEnrollment` created with SDDL granting `(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;UserSID)`. | **No** | Verified; standard user apps (PeekSetup) communicate without UAC. |
| **DPAPI Profile Storage** | `%LOCALAPPDATA%\Peek\Profiles` encrypted via `CryptProtectData` without `CRYPTPROTECT_LOCAL_MACHINE`. | **No** | Verified; standard DPAPI binds to the logged-in user context directly. |
| **Registry Identity Reads** | Reads `HKLM\SOFTWARE\Peek\Identities` with `KEY_READ`. Standard users have native read permissions on HKLM. | **No** | Verified; user SID mapping queries succeed without write permissions. |
| **Peek Setup Launching Engine** | Peek Setup or user process can launch `PeekEngine.exe` or trigger the task via `schtasks /run /tn "Peek Face Engine"` without UAC elevation prompts. | **No** | Verified; no elevation required. |

---

## 2. Camera Selection & Hardware Fault Recovery

### Auto-Selection & IR/Grayscale Stream Rejection
- Probes indices `0..3` via Windows Media Foundation (`cv2.CAP_MSMF`).
- For each camera index, reads test frames and converts to float RGB to avoid uint8 underflow.
- Rejects streams where:
  - Per-pixel mean channel difference between R, G, and B is `< 3.0`.
  - HSV saturation is `< 8.0`.
- Logs all probe results with explicit status (`unavailable`, `read_failed`, `rejected_ir`, `color`).
- Discards the first ~5 frames upon camera open for sensor auto-exposure and white-balance warm-up.
- Respects manual override if present in `%ProgramData%\Peek\engine.json`:
  ```json
  {
    "camera_index": 1
  }
  ```

### Reopen & Backoff on Camera Loss
- If `3` consecutive frame read failures occur during an active authentication session:
  - Camera is released (`cap.release()`).
  - Exponential backoff reopen is attempted (up to 3 tries, with backoff delays of `0.2s`, `0.4s`, `0.8s`).
  - If recovery succeeds, session resumes seamlessly.
  - If recovery fails after 3 tries, emits `MSG_ERROR` with `REASON_CAMERA_ERROR` ("Camera feed lost"). The Credential Provider immediately converts this to the PIN/password fallback.

### Camera Selection Caching & Refresh Policy
- **Latency Optimization**: Caches the proven color camera index in-memory so subsequent unlock attempts start immediately without repeating device probing.
- **Cache Refresh / Invalidation**: The camera cache is refreshed on engine restart or on repeated read failure (when 3 consecutive frame read failures occur or backoff reopen fails, the cache is invalidated and devices are re-probed).
- **Handling Unknown / Low-Light Devices**: `find_best_camera_index` never caches a device whose status is only "unknown" (e.g. dark environment or covered lens) as a proven colour camera; a camera is cached only after a successful open and frame read confirms a valid colour stream.

### Sleep / Wake (Resume from Suspend)
- [PowerStateWatcher](../engine/camera/power_watcher.py) monitors system tick progression. A wall-clock jump exceeding `3.5s` during a `1.0s` sleep indicates system sleep/resume.
- Flags resume event thread-safely; on the next authentication session, any stale camera handle is closed and re-created fresh.

### Per-Session Fault Isolation
- Any unhandled exception during an authentication or enrollment session is caught by session try/except blocks.
- The pipe server emits a structured error to the client, closes the broken session, releases the camera, and **continues running**. Subsequent connection requests succeed.

---

## 3. Latency Tracing & Performance Metrics

### Structured Latency Event Format
Logged with millisecond timestamps to `%ProgramData%\Peek\logs\engine.log` and `%ProgramData%\Peek\logs\credential_provider.log`:
```text
[LATENCY] session_id=<UUID> stage=<stage_name> timestamp_ms=<epoch_ms> [extra_metrics]
```
*(No biometric templates, passwords, or secrets are ever logged).*

### Stages Instrumented:
- **Credential Provider**:
  - `SetSelected`: Tile clicked on logon screen.
  - `pipe connected`: Named pipe connection established.
  - `first state`: First UI state received from engine.
  - `AUTHENTICATED`: Unlock authorization message received.
  - `GetSerialization done`: Kerberos credential serialization handed off to LogonUI.
- **Engine**:
  - `session start`: START_AUTH message received.
  - `camera open`: Camera hardware initialized.
  - `first frame`: First camera frame delivered.
  - `first face`: Face detected by SCRFD.
  - `temporal confirmed`: Temporal tracking confirmed identity.
  - `liveness passed`: Passive liveness score verified genuine face.
  - `AUTHENTICATED`: Dual-gate authorization satisfied.
  - `per-stage inference time`: Per-frame ms timings (`detect_ms`, `track_pose_ms`, `align_ms`, `embed_ms`, `match_ms`, `temporal_ms`, `liveness_ms`, `total_inference_ms`).

---

## 4. Performance & Replay Tools

### 1. `scripts/perf_report.py`
Parses engine and Credential Provider logs, computes p50 and p95 latencies per stage and end-to-end, split by faces seen (`0 faces`, `1 face`, `>1 faces`, `Overall`), and summarizes CPU% and peak RSS memory:
```cmd
python scripts/perf_report.py --sample-now --duration 5 --output-csv perf_metrics.csv
```

### 2. `scripts/bench_pipeline.py`
Replays a folder of images, video clip, or synthetic multi-face clip through the pipeline without camera hardware, reporting FPS throughput, per-stage latency percentiles (Mean, p50, p95), CPU%, and peak RSS memory:
```cmd
python scripts/bench_pipeline.py -n 50 --csv bench_results.csv
```

### 3. `scripts/perf_sample.py`
Monitors PeekEngine CPU% and RSS memory periodically (every 5 seconds) to a CSV, distinguishing idle monitoring from active sessions:
```cmd
python scripts/perf_sample.py --interval 5.0 --output %ProgramData%\Peek\logs\perf_samples.csv
```

---

## 5. Manual Testing Checklist

Follow these steps on a Windows test machine to verify production runtime behavior:

### Test 1: Kill the Engine (Task Auto-Restart)
1. Start PeekEngine service via scheduled task or terminal.
2. In Task Manager or PowerShell (non-elevated), kill the process:
   ```powershell
   Stop-Process -Name "PeekEngine" -Force
   ```
3. Check Task Scheduler (`taskschd.msc`) under `Peek Face Engine`.
4. **Expected Result**: Within ~1 minute, Task Scheduler detects the failure and relaunches `PeekEngine.exe` automatically (`RestartInterval = 1 minute`).

### Test 2: Unplug the Camera (Mid-Session Loss)
1. Lock the workstation (`Win + L`) and select the PEEK tile.
2. While the status shows "Looking for face..." or "Recognizing...", unplug the USB camera (or disable the camera in Device Manager).
3. **Expected Result**: Engine logs 3 consecutive read failures, attempts backoff reopen 3 times, and emits `REASON_CAMERA_ERROR`. The logon screen tile immediately updates to PIN/password fallback without crashing or freezing LogonUI.
4. Plug camera back in; select PEEK tile again. Engine re-probes and authenticates normally.

### Test 3: Sleep / Resume Then Lock
1. With Peek installed and running, put the PC to sleep (`Start -> Power -> Sleep`).
2. Wake the PC after 10+ seconds.
3. Observe the lock screen and select the PEEK tile.
4. **Expected Result**: `PowerStateWatcher` detects the sleep jump, invalidates the previous camera instance, acquires a fresh camera capture, and unlocks smoothly.

### Test 4: Two Cameras (Color vs. Infrared)
1. On a machine with an IR camera (Windows Hello sensor) and a standard RGB webcam (or connect a second webcam).
2. Start an authentication session.
3. Check `%ProgramData%\Peek\logs\engine.log`.
4. **Expected Result**: Camera index corresponding to IR is evaluated and logged as `Rejected infrared/grayscale stream (mean_diff=..., mean_sat=...)`. The RGB camera is accepted as `Accepted color stream`, and the face is recognized in full color.

### Test 5: Camera in Use by Another Application
1. Open the Windows Camera app or a video call application that locks the webcam.
2. Select the PEEK tile on the lock screen.
3. Check `%ProgramData%\Peek\logs\engine.log`.
4. **Expected Result**: Camera probe logs `status=unavailable (Camera unavailable or in use by another application)`. Engine emits `REASON_CAMERA_UNAVAILABLE`, and CP cleanly presents the PIN/password fallback.

### Test 6: Config Override via `engine.json`
1. Create or edit `%ProgramData%\Peek\engine.json`:
   ```json
   {
     "camera_index": 1
   }
   ```
2. Trigger an authentication session.
3. Check `%ProgramData%\Peek\logs\engine.log`.
4. **Expected Result**: Log indicates `Using camera_index 1 from config override` and does not probe indices 0..3.
