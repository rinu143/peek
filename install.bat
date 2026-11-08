@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo   Peek Face Recognition - Test Package Installer
echo ============================================================
echo.

:: Check for Administrator permissions
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Administrator privileges required.
    echo     Requesting elevation via Windows UAC...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
