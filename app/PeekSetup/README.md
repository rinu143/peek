# PeekSetup — Native Windows WPF Guided Enrollment & Setup Application

`PeekSetup` is a native Windows desktop setup wizard (WPF, .NET 8) that walks users through:
1. Entering their display name / account details (pre-filled from Windows user name).
2. Guided 9-direction face enrollment via the `\\.\pipe\PeekEnrollment` IPC stream.
3. Optionally linking their Windows password for seamless lock-screen automatic unlock.
4. Setup confirmation and fallback reminders.

---

## Architecture & Principles

- **Thin Client Architecture**: Contains zero face detection, embedding, or CV/ML logic. Operates purely as a thin client over the Windows Named Pipe `\\.\pipe\PeekEnrollment`.
- **In-Memory Frame Streaming**: Decodes transient JPEG frames in-memory (`BitmapImage` with `BitmapCacheOption.OnLoad` and `Freeze()`). No camera preview frames ever touch disk.
- **DPAPI Secret Interchangeability**: Writes wrapped secret files to `%LOCALAPPDATA%\Peek\Profiles\<profile_id>.secret` in the exact binary format expected by `credential_provider/SecretVault.cpp` and `apps/PeekEnrollment/main.py`:
  ```
  [DWORD length: 4 bytes little-endian UTF-16LE byte count][UTF-16LE password bytes]
  ```
  Encrypted via `System.Security.Cryptography.ProtectedData.Protect` (CurrentUser scope, null entropy).
- **Secure Password Handling**:
  - The password is collected exclusively via WPF `PasswordBox.SecurePassword` (`SecureString`).
  - Validated via P/Invoke `advapi32!LogonUserW` (`LOGON32_LOGON_INTERACTIVE = 2`) against the active account.
  - Returned token handles are closed immediately via `kernel32!CloseHandle`.
  - Plaintext byte buffers and unmanaged unicode buffers are zeroed immediately via `CryptographicOperations.ZeroMemory` and `Marshal.ZeroFreeGlobalAllocUnicode`.
  - Passwords are never converted to managed `System.String`, never logged, and never sent across the pipe.

---

## Project Structure

```
app/
├── PeekSetup.sln                      # Solution containing app and test projects
├── PeekSetup/
│   ├── PeekSetup.csproj               # WPF App targeting net8.0-windows
│   ├── App.xaml / App.xaml.cs         # Application entry and Fluent dark theme styling
│   ├── MainWindow.xaml / .cs          # Single-window 4-step wizard UI
│   ├── Models/
│   │   └── EnrollmentProtocol.cs      # IPC wire protocol constants and serialization
│   ├── Services/
│   │   ├── IPasswordValidator.cs      # LogonUserW validation abstraction
│   │   ├── WindowsPasswordValidator.cs# P/Invoke advapi32!LogonUserW implementation
│   │   ├── IPasswordLinker.cs         # Secret vault writer interface
│   │   ├── PasswordLinker.cs          # Length-prefixed buffer builder & DPAPI protector
│   │   ├── IEnrollmentPipeClient.cs   # Named pipe client interface & event args
│   │   └── EnrollmentPipeClient.cs    # Async NamedPipeClientStream implementation
│   └── ViewModels/
│       ├── MainViewModel.cs           # Wizard state machine and property bindings
│       ├── PoseItemViewModel.cs       # 9-pose progress tracking items
│       └── RelayCommand.cs            # ICommand and AsyncRelayCommand implementations
└── PeekSetup.Tests/
    ├── PeekSetup.Tests.csproj         # xUnit test project targeting net8.0-windows
    ├── PasswordLinkingTests.cs        # Mirrors test_password_linking.py coverage
    ├── EnrollmentProtocolTests.cs     # Serialization and protocol validation
    ├── MainViewModelTests.cs          # State machine, error handling, step transitions
    ├── PipeServerIntegrationTests.cs  # Live Named Pipe client-server integration
    └── run_test_enrollment_server.py  # Python pipe server helper for integration tests
```

---

## Building and Testing

### Build Application
```cmd
dotnet build app/PeekSetup/PeekSetup.csproj
```

### Run All Tests
```cmd
dotnet test app/PeekSetup.sln
```

### Launch Application
```cmd
dotnet run --project app/PeekSetup/PeekSetup.csproj
```

---

## Verification & Acceptance Criteria

- [x] **Step 1 (Welcome)**: Pre-fills account name from `Environment.UserName` (editable); requires non-empty display name.
- [x] **Step 2 (Face Enrollment)**: Connects to `\\.\pipe\PeekEnrollment`, renders preview frames from memory, overlays guidance and 9-pose progress, provides Cancel button.
- [x] **Step 2 (Resilience)**: If the engine is offline, shows clear actionable message: `"Peek engine isn't running — start it and try again."` with a Retry button.
- [x] **Step 3 (Password Linking)**: Explains benefits in plain language, collects password via `PasswordBox` (never plain `TextBox`), validates via `LogonUserW`, writes DPAPI wrapped secret, allows skipping.
- [x] **Step 4 (Complete)**: Summarizes enrolled profile and unlock mode; reminds user standard sign-in remains active fallback.
- [x] **Security**: Plaintext passwords never reach disk or logs; memory zeroed immediately.
- [x] **Cross-Compatibility**: Secret file format matches `credential_provider/SecretVault.cpp` and `apps/PeekEnrollment/main.py`.
