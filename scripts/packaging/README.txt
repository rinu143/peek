================================================================================
PEEK FACE AUTHENTICATION — TEST BUILD (v0.1-test)
================================================================================

*** IMPORTANT TEST BUILD NOTICE (PLEASE READ FIRST) ***

This is a THROWAWAY TEST PACKAGE for initial tester evaluation, NOT a signed
or official release:

1. NOT CODE SIGNED: Windows SmartScreen and Windows Defender will warn that
   this executable is from an "Unknown publisher". This is 100% EXPECTED for
   this test build.
   To run: Click "More info", then click "Run anyway".

2. TEST-GRADE INSTALLER: install.bat / install.ps1 copies files to Program Files,
   registers the Credential Provider DLL, and creates a Scheduled Task to run
   PeekEngine at logon. (A real production release will use a proper Windows
   Service and an official MSI installer).

3. ZERO NETWORK CALLS: Peek contains no telemetry, no auto-updater, and no
   outbound network activity. All biometric processing runs locally on your PC.


================================================================================
PACKAGE CONTENTS
================================================================================
- PeekEngine.exe              Standalone Python Face Engine (bundled ONNX models)
- PeekSetup.exe               WPF Face Enrollment & Password Linking application
- PeekCredentialProvider.dll  Windows LogonUI Credential Provider DLL
- install.bat / install.ps1   Administrator installation script
- uninstall.bat / uninstall.ps1  Clean uninstaller (removes registry & files)
- README.txt / README.md      This documentation


================================================================================
INSTALLATION (Requires Administrator)
================================================================================
1. Extract the entire ZIP archive into a local folder (do not run inside zip).
2. Right-click "install.bat" and choose "Run as administrator".
3. If SmartScreen appears, click "More info" -> "Run anyway".
4. The installer will copy binaries to "C:\Program Files\Peek", register the
   Credential Provider DLL with Windows LogonUI, configure a logon task, and
   start the PeekEngine background service.


================================================================================
HOW TO TEST PEEK
================================================================================
Step 1: Open "Peek Setup" from your Start Menu (or C:\Program Files\Peek\PeekSetup.exe).
Step 2: Follow the on-screen instructions to enroll your face across 5 guided poses
        (Center, Turn Left, Turn Right, Tilt Up, Tilt Down).
Step 3: Switch to the "Link Password" tab in Peek Setup, enter your Windows password,
        and click "Verify & Link Password". This encrypts your password in a
        local DPAPI vault so Peek can unlock your desktop.
Step 4: Lock your PC by pressing Windows Key + L.
Step 5: Look at the webcam on your lock screen. Peek will verify your face and liveness
        and unlock your PC automatically!

FAIL-OPEN SAFETY:
If the camera is disconnected, or if face recognition fails, your standard Windows
password and PIN options remain available. You can NEVER be locked out of your machine.


================================================================================
UNINSTALLATION
================================================================================
To completely remove Peek without leaving any orphaned registry or COM entries:
1. Right-click "uninstall.bat" and choose "Run as administrator".
2. The uninstaller will terminate running Peek processes, unregister the DLL,
   scrub all Credential Provider and CLSID registry keys, remove the scheduled
   task and shortcuts, and delete "C:\Program Files\Peek".
3. When prompted, you can choose whether or not to delete your local biometric
   profile data in %LOCALAPPDATA%\Peek.


================================================================================
HOW TO REPORT ISSUES
================================================================================
If you run into issues, please report:
1. Windows version (type "winver" in Run dialog).
2. Webcam model (built-in or USB model).
3. Any error messages or behavior description.
Submit feedback via the project repository or to the engineering team.
================================================================================
