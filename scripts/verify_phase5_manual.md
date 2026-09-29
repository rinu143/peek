# Phase 5 Verification: Windows Credential Provider Manual Test Checklist

Because the Windows Logon and Lock Screen subsystem (`LogonUI.exe`) executes in an isolated Winlogon desktop session (Session 0 / Winlogon secure desktop), COM credential provider behavior cannot be fully automated via standard headless test runners.

Follow this step-by-step checklist to validate the native C++ Credential Provider on a Windows 10/11 test machine.

---

## Pre-Flight Setup

- [ ] Ensure Windows 10 or 11 (64-bit) test machine is active.
- [ ] Build `PeekCredentialProvider.dll` in Release x64 mode:
  ```cmd
  cd credential_provider
  msbuild PeekCredentialProvider.sln /p:Configuration=Release /p:Platform=x64
  ```
- [ ] Ensure Python 3.10+ virtual environment has dependencies installed (`pip install -r requirements.txt`).
- [ ] Ensure at least one genuine user profile is enrolled in DPAPI storage (`storage/template_store.py`)

---

## Test Cases

### Password linking and stale-secret checks

- [ ] During enrollment, choose to link the current Windows password; a `<profile_id>.secret` file appears under `%LOCALAPPDATA%\Peek\Profiles`.
- [ ] Skip linking on another enrollment and confirm Peek fails open to password/PIN after face verification.
- [ ] Enter a wrong password while linking; confirm it is rejected and no `.secret` file is written.
- [ ] Change the real Windows password, lock the workstation, and authenticate with Peek; confirm the stale `.secret` is deleted and Windows falls open to password/PIN.

### Test 1: DLL Registration & Registry Verification

**Goal**: Confirm that the COM in-process server and Windows Credential Provider registry keys are cleanly created.

1. Open an Administrator Command Prompt.
2. Run:
   ```cmd
   regsvr32.exe credential_provider\x64\Release\PeekCredentialProvider.dll
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

**Goal**: Verify that when the Python background service is stopped or offline, the system **never locks the user out** and standard Windows authentication options remain fully functional.

1. Ensure `scripts/run_engine_service.py` is **NOT** running.
2. Lock the workstation: `Win + L`.
3. Select the "Peek Facial Recognition" tile.
   - [ ] Status text displays: `"Peek engine offline. Sign in with password or PIN."`
   - [ ] No crash, no freeze, no infinite spinner.
4. Click "Sign-in options" and select the standard Windows Password or PIN tile.
   - [ ] Standard password/PIN prompt works normally.
   - [ ] Enter valid password/PIN and unlock desktop.

---

### Test 3: Successful Biometric Unlock Flow

**Goal**: Verify end-to-end authentication when the live user is present in front of the camera.

1. Start the engine service in an Administrator console:
   ```cmd
   python scripts/run_engine_service.py --debug
   ```
   - [ ] Confirm console logs: `Listening on Named Pipe: \\.\pipe\PeekEngine`.
2. Lock the workstation: `Win + L`.
3. Select the "Peek Facial Recognition" tile.
4. Observe the tile status text progression:
   - [ ] Stage 1: `"Acquiring camera device..."`
   - [ ] Stage 2: `"Looking for face..."`
   - [ ] Stage 3: `"Face detected — verifying..."`
   - [ ] Stage 4: `"Evaluating liveness..."` (or active challenge prompt e.g. `"Tilt head up"`)
   - [ ] Stage 5: `"Verified! Signing in..."`
5. Windows completes unlock and returns to the desktop.
6. Verify service console logs:
   - [ ] Log displays: `Authentication verified for user '[User]'. Emitting AUTHENTICATED.`
   - [ ] Log confirms camera capture was released upon unlock completion.

---

### Test 4: Cancel Mid-Authentication (Bounded Latency)

**Goal**: Verify that if a user decides to switch to password/PIN while facial recognition is actively scanning, the engine cancels immediately (<50ms) without locking the camera.

1. Ensure `python scripts/run_engine_service.py` is running.
2. Lock the workstation: `Win + L`.
3. Select the Peek tile to start scanning.
4. While the camera is active (`Looking for face...`), immediately click "Sign-in options" and click the Password icon.
   - [ ] Switch occurs with zero perceived lag (<100ms).
   - [ ] Camera indicator LED turns off promptly.
   - [ ] Service logs confirm: `CANCEL_AUTH received from client; terminating session.`

---

### Test 5: Unenrolled / Impostor Face Rejection

**Goal**: Confirm that an unrecognized face does not grant access.

1. Ensure `python scripts/run_engine_service.py` is running.
2. Lock the workstation: `Win + L`.
3. Have an unenrolled person look into the webcam.
4. Observe tile behavior:
   - [ ] Status text indicates: `"Verifying..."` followed by `"Authentication failed. Please use password/PIN."`
   - [ ] Desktop remains securely locked.
   - [ ] User can switch to password/PIN tile to unlock.

---

### Test 6: Anti-Spoofing & Replay Attack Defense

**Goal**: Confirm that photo/video/bezel replay attacks are blocked at the lock screen.

1. Ensure `python scripts/run_engine_service.py` is running.
2. Lock the workstation: `Win + L`.
3. Present a smartphone replay showing a photo/video of the enrolled user.
4. Observe tile behavior:
   - [ ] Status text displays: `"Authentication failed. Please use password/PIN."`
   - [ ] Service logs: `Spoof attack detected: BEZEL_DETECTED (or SCREEN_MOIRE / LACKS_PARALLAX). Emitting AUTH_FAILED.`
   - [ ] Desktop remains securely locked.

---

### Test 7: Clean Unregistration

**Goal**: Verify that unregistering the DLL cleanly removes all registry keys and restores default Windows lock screen behavior.

1. Open Administrator Command Prompt.
2. Run:
   ```cmd
   regsvr32.exe /u credential_provider\x64\Release\PeekCredentialProvider.dll
   ```
   - [ ] Dialog reports: `DllUnregisterServer in PeekCredentialProvider.dll succeeded.`
3. Lock the workstation: `Win + L`.
   - [ ] Peek tile is completely removed from the lock screen.
   - [ ] Default Windows logon tiles function as normal.
