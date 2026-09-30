<#
.SYNOPSIS
    Automates the complete packaging workflow for the Peek Test Build.

.DESCRIPTION
    1. Builds PeekEngine.exe using PyInstaller (--onefile), bundling scripts/run_engine_service.py
       and all ONNX models under engine/models/.
    2. Publishes app/PeekSetup as a self-contained single-file win-x64 executable.
    3. Builds credential_provider in Release (x64) using CMake.
    4. Stages binaries, install/uninstall scripts, and documentation into dist\Peek-TestPackage\.
    5. Produces a single distribution zip archive: dist\Peek-v0.1-test.zip.

.NOTES
    Architecture: x64
#>

[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DistDir = Join-Path $RepoRoot "dist"
$StagingDir = Join-Path $DistDir "Peek-TestPackage"
$ZipPath = Join-Path $DistDir "Peek-v0.1-test.zip"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "         PEEK FACE AUTHENTICATION - PACKAGING PIPELINE      " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "[*] Repository Root : $RepoRoot"
Write-Host "[*] Output Directory: $DistDir"
Write-Host ""

if (-not $SkipBuild) {
    # -------------------------------------------------------------------------
    # 1. Build PeekEngine.exe (PyInstaller standalone)
    # -------------------------------------------------------------------------
    Write-Host "[1/3] Building PeekEngine.exe (Python PyInstaller standalone)..." -ForegroundColor Cyan
    $pyinstallerArgs = @(
        "-m", "PyInstaller",
        "--onefile",
        "--name", "PeekEngine",
        "--paths", ".",
        "--add-data", "engine\models\*.onnx;engine\models",
        "--collect-all", "onnxruntime",
        "--collect-all", "cv2",
        "--noconfirm",
        "scripts\run_engine_service.py"
    )
    $pyProc = Start-Process -FilePath "python" -ArgumentList $pyinstallerArgs -WorkingDirectory $RepoRoot -Wait -PassThru
    if ($pyProc.ExitCode -ne 0) {
        throw "PyInstaller build failed with exit code $($pyProc.ExitCode)"
    }
    Write-Host "      [OK] PeekEngine.exe created in dist\PeekEngine.exe" -ForegroundColor Green

    # -------------------------------------------------------------------------
    # 2. Publish PeekSetup.exe (.NET 8 WPF Single-File)
    # -------------------------------------------------------------------------
    Write-Host "[2/3] Publishing PeekSetup.exe (.NET 8 WPF single-file self-contained)..." -ForegroundColor Cyan
    $dotnetArgs = @(
        "publish", "app\PeekSetup\PeekSetup.csproj",
        "-c", "Release",
        "-r", "win-x64",
        "--self-contained", "true",
        "-p:PublishSingleFile=true",
        "-p:IncludeNativeLibrariesForSelfExtract=true"
    )
    $dotnetProc = Start-Process -FilePath "dotnet" -ArgumentList $dotnetArgs -WorkingDirectory $RepoRoot -Wait -PassThru
    if ($dotnetProc.ExitCode -ne 0) {
        throw "dotnet publish failed with exit code $($dotnetProc.ExitCode)"
    }
    Write-Host "      [OK] PeekSetup.exe published successfully." -ForegroundColor Green

    # -------------------------------------------------------------------------
    # 3. Build PeekCredentialProvider.dll (CMake Release x64)
    # -------------------------------------------------------------------------
    Write-Host "[3/3] Building PeekCredentialProvider.dll (CMake Release x64)..." -ForegroundColor Cyan
    $cmakeConfigArgs = @(
        "-B", "credential_provider\build",
        "-S", "credential_provider",
        "-A", "x64"
    )
    $cmakeConfProc = Start-Process -FilePath "cmake" -ArgumentList $cmakeConfigArgs -WorkingDirectory $RepoRoot -Wait -PassThru
    if ($cmakeConfProc.ExitCode -ne 0) {
        throw "CMake configuration failed with exit code $($cmakeConfProc.ExitCode)"
    }

    $cmakeBuildArgs = @(
        "--build", "credential_provider\build",
        "--config", "Release"
    )
    $cmakeBuildProc = Start-Process -FilePath "cmake" -ArgumentList $cmakeBuildArgs -WorkingDirectory $RepoRoot -Wait -PassThru
    if ($cmakeBuildProc.ExitCode -ne 0) {
        throw "CMake build failed with exit code $($cmakeBuildProc.ExitCode)"
    }
    Write-Host "      [OK] PeekCredentialProvider.dll built successfully." -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# 4. Stage artifacts into packaging folder
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[*] Staging artifacts into $StagingDir..." -ForegroundColor Cyan

if (-not (Test-Path $StagingDir)) {
    New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null
} else {
    Get-ChildItem -Path $StagingDir -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

$engineSource = Join-Path $DistDir "PeekEngine.exe"
$setupSource = Join-Path $RepoRoot "app\PeekSetup\bin\Release\net8.0-windows\win-x64\publish\PeekSetup.exe"
$dllSource = Join-Path $RepoRoot "credential_provider\build\Release\PeekCredentialProvider.dll"
$packagingSrcDir = Join-Path $RepoRoot "scripts\packaging"

Copy-Item -Path $engineSource -Destination (Join-Path $StagingDir "PeekEngine.exe") -Force
Copy-Item -Path $setupSource -Destination (Join-Path $StagingDir "PeekSetup.exe") -Force
Copy-Item -Path $dllSource -Destination (Join-Path $StagingDir "PeekCredentialProvider.dll") -Force

Copy-Item -Path (Join-Path $packagingSrcDir "install.ps1") -Destination $StagingDir -Force
Copy-Item -Path (Join-Path $packagingSrcDir "install.bat") -Destination $StagingDir -Force
Copy-Item -Path (Join-Path $packagingSrcDir "uninstall.ps1") -Destination $StagingDir -Force
Copy-Item -Path (Join-Path $packagingSrcDir "uninstall.bat") -Destination $StagingDir -Force
Copy-Item -Path (Join-Path $packagingSrcDir "README.txt") -Destination $StagingDir -Force
Copy-Item -Path (Join-Path $packagingSrcDir "README.md") -Destination $StagingDir -Force

Write-Host "      [OK] All components staged successfully." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 5. Create Distribution ZIP Archive
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[*] Creating distribution ZIP archive: $ZipPath..." -ForegroundColor Cyan

if (Test-Path $ZipPath) {
    Remove-Item -Path $ZipPath -Force
}

# Compress-Archive of the staging folder contents
Compress-Archive -Path "$StagingDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal

$zipItem = Get-Item $ZipPath
$zipSizeMb = [math]::Round($zipItem.Length / 1MB, 2)

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "            TEST PACKAGE CREATED SUCCESSFULLY               " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Archive Location: $ZipPath" -ForegroundColor White
Write-Host "Archive Size    : $zipSizeMb MB" -ForegroundColor White
Write-Host ""
Write-Host "Contents:"
Get-ChildItem -Path $StagingDir | ForEach-Object {
    $sizeStr = if ($_.Length -gt 1MB) { "$([math]::Round($_.Length / 1MB, 2)) MB" } else { "$([math]::Round($_.Length / 1KB, 2)) KB" }
    Write-Host "  - $($_.Name.PadRight(30)) ($sizeStr)"
}
Write-Host "============================================================" -ForegroundColor Green
