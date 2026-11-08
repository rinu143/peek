# Phase 5/7 Verification: Windows Credential Provider Manual Test Checklist

Because the Windows Logon and Lock Screen subsystem (`LogonUI.exe`) executes in an isolated Winlogon desktop session (Session 0 / Winlogon secure desktop), COM credential provider behavior cannot be fully automated via standard headless test runners.

Follow this step-by-step checklist to validate the native C++ Credential Provider on a Windows 10/11 test machine with the polished Phase 7 lock-screen tile experience.

---

## Pre-Flight Setup

- [ ] Ensure Windows 10 or 11 (64-bit) test machine is active.
- [ ] Build `PeekCredentialProvider.dll` in Release x64 mode:
  ```cmd
  cd credential_provider
  cmake --build build --config Release
  ```
- [ ] Ensure Python 3.10+ virtual environment has dependencies installed (`pip install -r requirements.txt`).
- [ ] Ensure at least one genuine user profile is enrolled in DPAPI storage (`storage/template_store.py`).

---

## Lock-Screen Tile Specification & Polish Checklist

### 1. Tile Title & Identity
- [ ] **Title**: The tile displays **"PEEK"** in both the Sign-in options list and the tile label (`PFI_LABEL` tagged with `CPFG_CREDENTIAL_PROVIDER_LABEL`).
  - No longer shows "Peek - Face Unlock" or "Peek Facial Recognition".
- [ ] **No Email / Username Displayed**:
  - `PFI_DISPLAY_NAME` returns `CPFS_HIDDEN` in all states.
  - The Microsoft-account email / Windows username is NOT visible in any field on the lock screen tile.
  - The enrolled name is kept internal and used solely for the success greeting.

### 2. Icon Transparency & Resting State
- [ ] **Resting / Idle Icon**:
  - When the tile is not selected, in `IDLE` state, or on static fallback, the icon is `res\frame_face_found.png`.
  - The glyph renders with true 32bpp alpha transparency (no black square/border behind the glyph in LogonUI).
- [ ] **DuplicateArgbBitmap & GDI DIB Section**:
  - Verify in `%SystemRoot%\Temp\credential_provider.log` (or local CP log) that every frame returned by `GetBitmapValue` and `SetFieldBitmap` is a fresh 32bpp top-down `BI_RGB` DIB section duplicated via `DuplicateArgbBitmap`.
  - Confirm the logged frame metrics:
    - `bmBitsPixel = 32`
    - Corner pixel alpha `(0,0) = 0` (fully transparent)
    - Center pixel alpha `> 0` (opaque/semi-transparent icon glyph)
    - First 20 callback invocations log running count and `HRESULT` (S_OK).

### 3. Plain Text Status Line & Icon Animation Progression
- [ ] **Plain Text Status Line Cycle**:
  - Upon selecting the PEEK tile, `PFI_STATUS_TEXT` becomes visible (`CPFS_DISPLAY_IN_SELECTED_TILE`) and displays plain text driven by engine state:
    - `SEARCHING`: **"Looking for face..."**
    - `FACE_FOUND`: **"Found match"**
    - `VERIFYING`: **"Verifying..."**
    - `LIVENESS`: **"Verifying..."** (unchanged text skips redundant updates)
    - `AUTH_FAILED`: **"Face not recognized"**
    - `RETRY`: **"Retrying..."**
    - `SUCCESS`: **"Hello, [Enrolled Name]!"** (or **"Welcome back"**)
    - `TIMEOUT`, retry limit reached, or engine offline: **"Sign in with your PIN or password"**
    - `IDLE`: hidden and empty.
  - On failure, line cycles: **"Face not recognized"** -> **"Retrying..."** -> **"Looking for face..."**.
- [ ] **Icon Animation Synchronization**:
  - `TileAnimator` icon animation remains active alongside status line plain text.
  - `SEARCHING` cycles icon frames 1..3 in ping-pong order: `1 -> 2 -> 3 -> 2`.
  - Icon state transitions occur in sync: `FACE_FOUND` (`frame_face_found.png`), `VERIFYING` (`frame_verifying.png`), `LIVENESS` (`frame_liveness.png`).

### 4. Text Policy & Failure / Fallback Handling
- [ ] **Engine Offline / Camera Failure**:
  - When the engine service is stopped, offline, or pipe disconnected:
    - Status text immediately becomes visible (`CPFS_DISPLAY_IN_SELECTED_TILE`).
    - Status text reads: **"Sign in with your PIN or password"**.
    - Detailed technical reason is recorded in the log file only, never shown to the user.
- [ ] **Retry Limit (Max 3 Cycles)**:
  - When authentication fails (`AUTH_FAILED`):
    - **Cycle 1 & 2**: Status line displays **"Face not recognized"** (failure icon), transitions to **"Retrying..."** (retry icon), then resumes **"Looking for face..."** (searching pulse).
    - **Cycle 3 (Max Reached)**: Consecutive failure count reaches 3 (`kMaxRetryCycles = 3`):
      - Scanning session stops immediately.
      - Status text displays: **"Sign in with your PIN or password"**.
      - Icon displays `frame_failure.png`.
      - Engine stops retrying automatically.
  - **Tile Re-selection**:
    - Clicking away from the tile and re-selecting it (`SetSelected`) resets the consecutive failure counter to 0, resets status text to **"Looking for face..."**, and begins a fresh scanning cycle.

### 5. Success Greeting & Desktop Unlock
- [ ] **Success Presentation**:
  - Upon genuine `AUTHENTICATED` message from engine:
    - Icon displays smiling success glyph (`frame_success.png`).
    - Status text becomes visible displaying: **"Hello, [Enrolled Name]!"** (or **"Welcome back"** if no custom display name was enrolled).
    - Tile holds greeting and success icon for ~750ms so user clearly perceives successful recognition.
    - Windows LogonUI unlocks session and smoothly transitions to the desktop.
  - Security invariant: The tile **never** advances to `SUCCESS` without a verified `AUTHENTICATED` IPC message.

---

## End-to-End Test Scenarios

### Test 1: DLL Registration & Registry Verification

**Goal**: Confirm that the COM in-process server and Windows Credential Provider registry keys are cleanly created.

1. Open an Administrator Command Prompt.
2. Run:
   ```cmd
   regsvr32.exe credential_provider\build\Release\PeekCredentialProvider.dll
   ```
   - [ ] Verify that a dialog reports: `DllRegisterServer in PeekCredentialProvider.dll succeeded.`
3. Verify registry entries:
   ```cmd
   reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}"
   reg query "HKCR\CLSID\{4F64A0FC-9F09-4C0B-A5E7-537C44919903}\InprocServer32"
   ```
   - [ ] Provider key exists and default value is `PeekCredentialProvider`.
   - [ ] `InprocServer32` points to the correct DLL path and `ThreadingModel` is `Apartment`.

---

### Test 2: Fail-Open Security (Engine Service Not Running)

**Goal**: Verify that when the Python background service is stopped or offline, the system **never locks the user out** and immediately guides the user to PIN/password.

1. Ensure `scripts/run_engine_service.py` is **NOT** running.
2. Lock the workstation: `Win + L`.
3. Observe the resting icon: `frame_face_found.png` is displayed with title **"PEEK"** and no email.
4. Select the "PEEK" tile.
   - [ ] Status text immediately displays: **"Sign in with your PIN or password"**.
   - [ ] No freeze, crash, or hung camera session.
5. Click "Sign-in options" and select the standard Windows Password or PIN tile.
   - [ ] Standard password/PIN prompt works normally.
   - [ ] Enter valid password/PIN and unlock desktop.

---

### Test 3: Successful Biometric Unlock Flow (Animation Without Text)

**Goal**: Verify end-to-end authentication when the enrolled user is in front of the camera.

1. Start the engine service in an Administrator console:
   ```cmd
   python scripts/run_engine_service.py --debug
   ```
   - [ ] Confirm console logs: `Listening on Named Pipe: \\.\pipe\PeekEngine`.
2. Lock the workstation: `Win + L`.
3. Verify resting tile:
   - [ ] Tile title is **"PEEK"**.
   - [ ] No email address or Windows user name is visible.
   - [ ] Resting icon is `frame_face_found.png` with transparent background.
4. Select the "PEEK" tile:
   - [ ] Search animation cycles `1, 2, 3, 2` smoothly at ~150ms.
   - [ ] **NO status text is displayed** while searching.
   - [ ] As face is detected and verified, icon switches to face found / verifying / liveness frames without status text.
5. Biometric match completes:
   - [ ] Icon switches to `frame_success.png`.
   - [ ] Status text displays **"Hello, [Name]!"** (or **"Welcome back"**).
   - [ ] After ~750ms hold, Windows unlocks and returns to desktop.
6. Verify `credential_provider.log`:
   - [ ] Logs show 32bpp depth, alpha(0,0)=0, center alpha, and `SetFieldBitmap` result codes for each frame kind.

---

### Test 4: Cancel Mid-Authentication (Rapid Switching)

**Goal**: Verify that if a user switches to password/PIN while facial recognition is actively scanning, the engine cancels immediately (<50ms) and animation stops.

1. Ensure `python scripts/run_engine_service.py` is running.
2. Lock the workstation: `Win + L`.
3. Select the PEEK tile to start scanning.
4. While the radar animation is cycling, immediately click "Sign-in options" and select the Password icon.
   - [ ] Switch occurs with zero perceived lag (<100ms).
   - [ ] Tile animation thread terminates immediately upon deselection.
   - [ ] Camera indicator LED turns off promptly.
   - [ ] Service logs confirm: `CANCEL_AUTH received from client; terminating session.`

---

### Test 5: Retry Limit & Fallback (3 Cycles)

**Goal**: Confirm that repeated failed matches do not loop indefinitely and gracefully stop at 3 cycles with fallback text.

1. Ensure `python scripts/run_engine_service.py` is running.
2. Lock the workstation: `Win + L`.
3. Select the "PEEK" tile and present an un-enrolled face or obscure the camera.
4. Observe retry progression:
   - [ ] **Attempt 1**: Fails, shows calm `frame_failure.png` then `frame_retry.png`; no status text shown.
   - [ ] **Attempt 2**: Fails, shows calm `frame_failure.png` then `frame_retry.png`; no status text shown.
   - [ ] **Attempt 3 (Limit Reached)**: Fails; retry cycle limit (3) is reached:
     - Scanning session terminates (camera turns off).
     - Icon displays `frame_failure.png`.
     - Status text displays: **"Sign in with your PIN or password"**.
     - No further retries occur.
5. Click away and re-select the "PEEK" tile:
   - [ ] Consecutive failure counter resets to 0.
   - [ ] Status text hides again.
   - [ ] Camera activates and search animation restarts.

---

### Test 6: Anti-Spoofing & Replay Attack Defense

**Goal**: Confirm that photo/video/bezel replay attacks are rejected.

1. Ensure `python scripts/run_engine_service.py` is running.
2. Lock the workstation: `Win + L`.
3. Present a smartphone replay showing a photo/video of the enrolled user.
4. Observe tile behavior:
   - [ ] Spoof is rejected; shows failure/retry frames.
   - [ ] Service logs: `Spoof attack detected: BEZEL_DETECTED (or SCREEN_MOIRE / LACKS_PARALLAX). Emitting AUTH_FAILED.`
   - [ ] Desktop remains securely locked.
   - [ ] After 3 failed cycles, displays **"Sign in with your PIN or password"**.

---

### Test 7: Fail-Soft Fallback (Missing Files or GDI+ Failure)

**Goal**: Confirm that if any frame fails to load, the provider falls back safely to `frame_face_found.png` decoded directly, keeps "PEEK" title and hidden email, and never crashes `LogonUI.exe`.

1. If resource extraction fails or GDI+ is unavailable:
   - [ ] Provider falls back to decoded `IDR_PNG_FRAME_FACE_FOUND` icon section.
   - [ ] Title remains **"PEEK"** and email remains hidden.
   - [ ] Winlogon / LogonUI never crashes or freezes.
   - [ ] Password and PIN sign-in continue to work without impediment.

---

### Test 8: Stale-Secret Deletion via ReportResult on Password Change

**Goal**: Verify that when a user password is changed externally, Winlogon rejects the stale secret and `ReportResult` deletes the stale LSA secret (`L$PeekCred_<SID>`) and local secret file, failing open to standard password/PIN entry.

1. Change Windows password externally (`Ctrl + Alt + Del -> Change a password`).
2. Lock the workstation: `Win + L`.
3. Authenticate with face:
   - [ ] Winlogon rejects the serialized logon.
   - [ ] `ReportResult` handles `STATUS_LOGON_FAILURE` and deletes stale secret.
   - [ ] Tile falls open cleanly to password/PIN input.
   - [ ] Subsequent attempts do not re-submit the stale password.

---

### Test 9: Clean Unregistration

**Goal**: Verify that unregistering the DLL cleanly removes all registry keys and restores default Windows lock screen behavior.

1. Open Administrator Command Prompt.
2. Run:
   ```cmd
   regsvr32.exe /u credential_provider\build\Release\PeekCredentialProvider.dll
   ```
   - [ ] Dialog reports: `DllUnregisterServer in PeekCredentialProvider.dll succeeded.`
3. Lock workstation (`Win + L`):
   - [ ] PEEK tile is completely removed from lock screen.
   - [ ] Default Windows logon tiles function as normal.
