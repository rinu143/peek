# Peek Credential Provider & Face Engine: Failure-Mode Verification Guide

This document specifies the verification protocols for all **Phase 8 failure modes**. Every failure mode must cleanly fall back to Windows PIN or Password within **15 seconds** and must **never crash LogonUI.exe or the PeekEngine process**.

---

## Failure Mode Matrix

| # | Failure Mode | Trigger / Condition | Expected Behavior | Timeout Limit | Crash Immunity |
| :-: | :--- | :--- | :--- | :---: | :---: |
| **1** | **Engine Killed Mid-Session** | Process terminated during auth | Pipe unblocks with EOF; CP displays fallback | < 2.0s | LogonUI immune |
| **2** | **Camera Unplugged** | USB camera disconnected or disabled | 3 backoff retries exhaust; `REASON_CAMERA_ERROR` | < 3.0s | Engine & LogonUI immune |
| **3** | **Pipe Squatting** | Unauthorized process creates pipe | `FILE_FLAG_FIRST_PIPE_INSTANCE` blocks; CP fallback | < 10.0s | Engine & LogonUI immune |
| **4** | **Corrupt / Missing Profile** | `.peek` file missing or invalid bytes | Engine skips file, loads 0 profiles; no auth | < 1.0s | Engine immune |
| **5** | **Missing / Invalid LSA Secret** | LSA secret or registry SID missing | `CPGSR_NO_CREDENTIAL_FINISHED` returned | Immediate | LogonUI immune |
| **6** | **DPAPI Decryption Failure** | Wrong account context or corrupted entropy | `CryptUnprotectData` error caught, profile skipped | < 1.0s | Engine immune |

---

## Automated Verification

Run the automated failure mode test suite:
```powershell
python -m pytest tests/test_failure_modes.py -v
```
All tests must pass with 0 errors.

---

## Manual Test Procedures

### Test 1: Engine Killed Mid-Session
1. Lock the workstation (`Win + L`).
2. Select the **PEEK** tile.
3. While the status displays `"Looking for face..."`, kill the engine from an SSH session, administrative PowerShell, or background timer:
   ```powershell
   Stop-Process -Name "PeekEngine" -Force
   ```
4. **Verification**:
   - [ ] LogonUI does not freeze or become unresponsive.
   - [ ] Status text transitions to fallback or returns to credential selection within 2 seconds.
   - [ ] User can click the PIN/Password icon and unlock the workstation immediately.
   - [ ] Within ~1 minute, Task Scheduler restarts `PeekEngine.exe` automatically.

---

### Test 2: Camera Unplugged Mid-Session
1. Ensure a USB webcam is connected and the Peek tile is visible on the lock screen.
2. Select the **PEEK** tile.
3. While the camera LED is on and status shows `"Looking for face..."`, unplug the USB camera cable (or disable the camera device in Device Manager via `pnputil /disable-device <InstanceId>`).
4. **Verification**:
   - [ ] Engine log shows 3 consecutive frame read failures followed by backoff reopen attempts.
   - [ ] Engine emits `MSG_ERROR` with `reason_code = "CAMERA_ERROR"` ("Camera feed lost").
   - [ ] LogonUI tile updates to PIN/password fallback in less than 3 seconds (well within the 15-second limit).
   - [ ] LogonUI never crashes or displays a blank screen.
   - [ ] Reconnecting the camera allows normal operation on the next unlock attempt.

---

### Test 3: Non-Engine Process Squatting the Pipe
1. Stop the Peek engine service:
   ```powershell
   Stop-Process -Name "PeekEngine" -Force
   ```
2. In an administrative PowerShell window, create a dummy pipe with the exact same name:
   ```powershell
   $pipe = New-Object System.IO.Pipes.NamedPipeServerStream("PeekEngine", [System.IO.Pipes.PipeDirection]::InOut, 1)
   ```
3. Attempt to start `PeekEngine.exe`.
4. **Verification**:
   - [ ] `PeekEngine.exe` fails startup immediately because `FILE_FLAG_FIRST_PIPE_INSTANCE` prevents attaching to an existing pipe.
   - [ ] Traceback is logged to `%ProgramData%\Peek\logs\engine.log` and the engine exits code 1 (fails closed).
   - [ ] In LogonUI, selecting the Peek tile encounters an unauthorized or unresponsive pipe; `PeekPipeClient` server verification or connection timeout triggers within 10 seconds.
   - [ ] LogonUI falls back cleanly to PIN/password without hanging.

---

### Test 4: Corrupt or Missing Profile File
1. Locate the enrolled user profile in `%LOCALAPPDATA%\Peek\Profiles\`.
2. **Scenario 4A (Missing File)**:
   - Temporarily move all `.peek` files to a backup folder.
   - Lock workstation and select PEEK.
   - **Result**: Engine reports 0 profiles enrolled; LogonUI tile immediately prompts for password/PIN.
3. **Scenario 4B (Corrupt File)**:
   - Overwrite a `.peek` file with 64 bytes of random binary data:
     ```powershell
     [System.IO.File]::WriteAllBytes("$env:LOCALAPPDATA\Peek\Profiles\corrupt.peek", [byte[]](1..64))
     ```
   - Lock workstation and select PEEK.
   - **Result**: Engine logs `Failed to load/decrypt profile` and skips the corrupted file without crashing. Tile falls back to PIN/password immediately.

---

### Test 5: Missing or Invalid LSA Secret / Registry Key
1. **Scenario 5A (Missing LSA Secret)**:
   - Delete the user's LSA secret (`Peek_Auth_<SID>`) via PeekSetup or test script.
   - Lock workstation and attempt face unlock.
   - **Result**: `SecretVault::RetrieveSecret` fails with `NotFound`; `PeekCredential::GetSerialization` returns `CPGSR_NO_CREDENTIAL_FINISHED`. LogonUI seamlessly directs the user to enter their PIN or password.
2. **Scenario 5B (Missing Registry Identity)**:
   - Remove the user SID entry under `HKLM\SOFTWARE\Peek\Identities\<SID>`.
   - **Result**: Credential Provider rejects biometric unlock and presents the standard sign-in prompt immediately without crashing.

---

### Test 6: DPAPI Decryption Failure
1. Simulate DPAPI failure by running the engine under a different user account or corrupting `%ProgramData%\Peek\machine_entropy.bin`.
2. Attempt an authentication request.
3. **Verification**:
   - [ ] `CryptUnprotectData` fails with an expected Windows error (`NTE_BAD_KEY_STATE` or `ERROR_INVALID_DATA`).
   - [ ] `SecureProfileStore` catches the exception, logs a warning, and returns `None`.
   - [ ] No unhandled exception crashes the engine or LogonUI.
   - [ ] User is directed to enter their Windows password.
