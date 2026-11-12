<#
.SYNOPSIS
    End-to-end release build automation script for Peek Face Authentication.

.DESCRIPTION
    Executes the full release pipeline:
      1. Stamps SemVer across all components via scripts/release/set_version.ps1
      2. Fetches and validates pinned ONNX models via engine/models/download_models.py
      3. Builds the C++ Credential Provider DLL with static CRT (/MT)
      4. Builds PeekEngine (PyInstaller one-DIR) via PeekEngine.spec
      5. Publishes PeekSetup.exe (.NET 8 WPF single-file self-contained with compression)
      6. Executes complete test suites (PeekSetup unit tests, pytest, C++ CP tests)
      7. Invokes optional code signing on all binaries (DLL, EXEs)
      8. Compiles Inno Setup 6 installer (dist\release\Peek-Setup-<version>-x64.exe)
      9. Signs the installer package (if signing parameters provided)
      10. Computes and generates dist\release\SHA256SUMS

.PARAMETER Version
    Semantic Version string (e.g. "0.2.0-beta.1"). Defaults to the content of VERSION.

.PARAMETER SignTool
    Explicit path to signtool.exe. If omitted, standard Windows SDK paths are searched.

.PARAMETER CertThumbprint
    Certificate SHA-1 thumbprint for signtool (/sha1 <thumbprint>).

.PARAMETER CertificateFile
    PFX certificate file path (/f <file>).

.PARAMETER CertificatePassword
    Password for the PFX certificate (/p <pwd>).

.PARAMETER TimestampServer
    RFC 3161 timestamp authority URL (default: http://timestamp.digicert.com).

.PARAMETER SkipTests
    Skip test suite execution.

.PARAMETER Clean
    Clean build and dist staging directories before building.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Version,

    [string]$SignTool,
    [string]$CertThumbprint,
    [string]$CertificateFile,
    [string]$CertificatePassword,
    [string]$TimestampServer = "http://timestamp.digicert.com",

    [switch]$SkipTests,
    [switch]$Clean,
    [switch]$UnsignedManifest
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $RepoRoot "VERSION"))) {
    $RepoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
}

$DistDir = Join-Path $RepoRoot "dist"
$ReleaseDir = Join-Path $DistDir "release"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       PEEK FACE AUTHENTICATION - RELEASE BUILD PIPELINE    " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "[*] Repository Root : $RepoRoot"
Write-Host "[*] Staging Output  : $DistDir"
Write-Host "[*] Release Output  : $ReleaseDir"
Write-Host ""

# -----------------------------------------------------------------------------
# Code Signing Helper Function
# -----------------------------------------------------------------------------
function Invoke-CodeSign {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath
    )

    if (-not (Test-Path $FilePath)) {
        throw "Cannot sign non-existent file: $FilePath"
    }

    if (-not $CertThumbprint -and -not $CertificateFile) {
        Write-Host "      [-] Code signing skipped (no cert thumbprint or file provided): $(Split-Path $FilePath -Leaf)" -ForegroundColor Gray
        return
    }

    $tool = $SignTool
    if (-not $tool) {
        $cmdSign = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($cmdSign) {
            $tool = $cmdSign.Source
        } else {
            $sdkPaths = @(
                "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe",
                "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22000.0\x64\signtool.exe",
                "C:\Program Files (x86)\Windows Kits\10\bin\10.0.19041.0\x64\signtool.exe",
                "C:\Program Files (x86)\Windows Kits\10\App Certification Kit\signtool.exe"
            )
            $tool = $sdkPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        }
    }

    if (-not $tool -or -not (Test-Path $tool)) {
        Write-Warning "signtool.exe could not be located. Skipping signing for $FilePath."
        return
    }

    Write-Host "      [+] Signing $(Split-Path $FilePath -Leaf) with signtool..." -ForegroundColor Cyan
    $signArgs = @("sign", "/fd", "SHA256")

    if ($CertThumbprint) {
        $signArgs += @("/sha1", $CertThumbprint)
    } elseif ($CertificateFile) {
        $signArgs += @("/f", $CertificateFile)
        if ($CertificatePassword) {
            $signArgs += @("/p", $CertificatePassword)
        }
    }

    if ($TimestampServer) {
        $signArgs += @("/tr", $TimestampServer, "/td", "SHA256")
    }

    $signArgs += $FilePath

    $signProc = Start-Process -FilePath $tool -ArgumentList $signArgs -Wait -PassThru -NoNewWindow
    if ($signProc.ExitCode -ne 0) {
        throw "signtool failed on $FilePath with exit code $($signProc.ExitCode)"
    }
    Write-Host "      [OK] Successfully signed $(Split-Path $FilePath -Leaf)" -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# Clean Step (Optional)
# -----------------------------------------------------------------------------
if ($Clean) {
    Write-Host "[*] Cleaning build output directories..." -ForegroundColor Cyan
    if (Test-Path $DistDir) {
        Get-ChildItem -Path $DistDir -Exclude "release" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}
if (-not (Test-Path $ReleaseDir)) {
    New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
}

# -----------------------------------------------------------------------------
# 1. Version Stamping
# -----------------------------------------------------------------------------
Write-Host "[1/8] Stamping version across components..." -ForegroundColor Cyan
$setVerScript = Join-Path $PSScriptRoot "set_version.ps1"
if (-not (Test-Path $setVerScript)) {
    throw "set_version.ps1 not found at $setVerScript"
}

$setVerArgs = @()
if (-not [string]::IsNullOrWhiteSpace($Version)) {
    $setVerArgs += @("-Version", $Version)
}

& $setVerScript @setVerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Version stamping failed with exit code $LASTEXITCODE"
}

$versionFile = Join-Path $RepoRoot "VERSION"
$effectiveVersion = (Get-Content $versionFile -Raw).Trim()
Write-Host "      [OK] Verified release version: $effectiveVersion" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 2. Supply Chain: Fetch & Verify Pinned Models
# -----------------------------------------------------------------------------
Write-Host "[2/8] Fetching and verifying cryptographic hashes for models..." -ForegroundColor Cyan
$downloadModelsScript = Join-Path $RepoRoot "engine\models\download_models.py"
$pyProc = Start-Process -FilePath "python" -ArgumentList @($downloadModelsScript) -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
if ($pyProc.ExitCode -ne 0) {
    throw "download_models.py failed with exit code $($pyProc.ExitCode)"
}
Write-Host "      [OK] Cryptographically verified model hashes." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 3. Build C++ Credential Provider DLL (/MT Static CRT)
# -----------------------------------------------------------------------------
Write-Host "[3/8] Building PeekCredentialProvider.dll (CMake Release x64, static CRT)..." -ForegroundColor Cyan
$cpBuildDir = Join-Path $RepoRoot "credential_provider\build"
$cmakeConfigArgs = @(
    "-B", $cpBuildDir,
    "-S", (Join-Path $RepoRoot "credential_provider"),
    "-A", "x64"
)
$cmakeProc = Start-Process -FilePath "cmake" -ArgumentList $cmakeConfigArgs -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
if ($cmakeProc.ExitCode -ne 0) {
    throw "CMake configure failed with exit code $($cmakeProc.ExitCode)"
}

$cmakeBuildArgs = @(
    "--build", $cpBuildDir,
    "--config", "Release"
)
$cmakeBuildProc = Start-Process -FilePath "cmake" -ArgumentList $cmakeBuildArgs -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
if ($cmakeBuildProc.ExitCode -ne 0) {
    throw "CMake build failed with exit code $($cmakeBuildProc.ExitCode)"
}

$builtDll = Join-Path $cpBuildDir "Release\PeekCredentialProvider.dll"
if (-not (Test-Path $builtDll)) {
    throw "Expected DLL artifact not found at $builtDll"
}
$stagedDll = Join-Path $DistDir "PeekCredentialProvider.dll"
Copy-Item -Path $builtDll -Destination $stagedDll -Force
Invoke-CodeSign -FilePath $stagedDll
Write-Host "      [OK] PeekCredentialProvider.dll built and staged in dist\" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 4. Build Python Face Engine (PyInstaller one-DIR)
# -----------------------------------------------------------------------------
Write-Host "[4/8] Building PeekEngine (PyInstaller one-DIR via PeekEngine.spec)..." -ForegroundColor Cyan
$icoPath = Join-Path $RepoRoot "build\peek.ico"
if (-not (Test-Path $icoPath)) {
    Write-Host "      Generating application icon build\peek.ico..." -ForegroundColor Gray
    $makeIconScript = Join-Path $RepoRoot "scripts\make_icon.py"
    Start-Process -FilePath "python" -ArgumentList @($makeIconScript) -WorkingDirectory $RepoRoot -Wait -NoNewWindow
}

$pyinstallerArgs = @("-m", "PyInstaller", "--noconfirm", "PeekEngine.spec")
$pyinstProc = Start-Process -FilePath "python" -ArgumentList $pyinstallerArgs -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
if ($pyinstProc.ExitCode -ne 0) {
    throw "PyInstaller build failed with exit code $($pyinstProc.ExitCode)"
}

$engineDir = Join-Path $DistDir "PeekEngine"
$engineExe = Join-Path $engineDir "PeekEngine.exe"
if (-not (Test-Path $engineExe)) {
    throw "Expected engine executable not found at $engineExe"
}
Invoke-CodeSign -FilePath $engineExe
Write-Host "      [OK] PeekEngine one-DIR staged in dist\PeekEngine\" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 5. Build PeekSetup.exe (.NET 8 WPF Single-File)
# -----------------------------------------------------------------------------
Write-Host "[5/8] Publishing PeekSetup.exe (.NET 8 WPF single-file self-contained)..." -ForegroundColor Cyan
$dotnetArgs = @(
    "publish", "app\PeekSetup\PeekSetup.csproj",
    "-c", "Release",
    "-r", "win-x64",
    "--self-contained", "true",
    "-p:PublishSingleFile=true",
    "-p:EnableCompressionInSingleFile=true",
    "-p:IncludeNativeLibrariesForSelfExtract=true",
    "-p:UseSharedCompilation=false"
)
$dotnetProc = Start-Process -FilePath "dotnet" -ArgumentList $dotnetArgs -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
if ($dotnetProc.ExitCode -ne 0) {
    throw "dotnet publish failed with exit code $($dotnetProc.ExitCode)"
}

$setupPublished = Join-Path $RepoRoot "app\PeekSetup\bin\Release\net8.0-windows\win-x64\publish\PeekSetup.exe"
if (-not (Test-Path $setupPublished)) {
    throw "Expected published PeekSetup.exe not found at $setupPublished"
}
$stagedSetup = Join-Path $DistDir "PeekSetup.exe"
Copy-Item -Path $setupPublished -Destination $stagedSetup -Force
Invoke-CodeSign -FilePath $stagedSetup
Write-Host "      [OK] PeekSetup.exe published and staged in dist\" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 5b. Build PeekUpdate.exe (.NET 8 Single-File Self-Contained)
# -----------------------------------------------------------------------------
Write-Host "[5b/8] Publishing PeekUpdate.exe (.NET 8 single-file self-contained)..." -ForegroundColor Cyan
$updateDotnetArgs = @(
    "publish", "app\PeekUpdate\PeekUpdate.csproj",
    "-c", "Release",
    "-r", "win-x64",
    "--self-contained", "true",
    "-p:PublishSingleFile=true",
    "-p:EnableCompressionInSingleFile=true",
    "-p:IncludeNativeLibrariesForSelfExtract=true",
    "-p:UseSharedCompilation=false"
)
$updateProc = Start-Process -FilePath "dotnet" -ArgumentList $updateDotnetArgs -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
if ($updateProc.ExitCode -ne 0) {
    throw "dotnet publish for PeekUpdate failed with exit code $($updateProc.ExitCode)"
}

$updatePublished = Join-Path $RepoRoot "app\PeekUpdate\bin\Release\net8.0-windows\win-x64\publish\PeekUpdate.exe"
if (-not (Test-Path $updatePublished)) {
    $updatePublished = Join-Path $RepoRoot "app\PeekUpdate\bin\Release\net8.0\win-x64\publish\PeekUpdate.exe"
}
if (-not (Test-Path $updatePublished)) {
    throw "Expected published PeekUpdate.exe not found at $updatePublished"
}
$stagedUpdate = Join-Path $DistDir "PeekUpdate.exe"
Copy-Item -Path $updatePublished -Destination $stagedUpdate -Force
Invoke-CodeSign -FilePath $stagedUpdate
Write-Host "      [OK] PeekUpdate.exe published and staged in dist\" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 6. Execute Test Suites
# -----------------------------------------------------------------------------
if (-not $SkipTests) {
    Write-Host "[6/8] Executing automated test suites..." -ForegroundColor Cyan

    # 6a. PeekSetup Unit Tests
    Write-Host "      [Test 1/3] Running .NET PeekSetup unit tests..." -ForegroundColor Gray
    $testProc = Start-Process -FilePath "dotnet" -ArgumentList @("test", "app\PeekSetup.Tests\PeekSetup.Tests.csproj", "-c", "Release", "--no-build", "-p:UseSharedCompilation=false") -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
    if ($testProc.ExitCode -ne 0) {
        throw "PeekSetup unit tests failed with exit code $($testProc.ExitCode)"
    }
    Write-Host "      [OK] All .NET unit tests passed." -ForegroundColor Green

    # 6b. Python Tests
    Write-Host "      [Test 2/3] Running pytest IPC & protocol tests..." -ForegroundColor Gray
    $pytestProc = Start-Process -FilePath "python" -ArgumentList @("-m", "pytest", "tests/") -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
    if ($pytestProc.ExitCode -ne 0) {
        throw "pytest failed with exit code $($pytestProc.ExitCode)"
    }
    Write-Host "      [OK] All Python tests passed." -ForegroundColor Green

    # 6c. Credential Provider C++ Tests
    Write-Host "      [Test 3/3] Running C++ Credential Provider unit tests..." -ForegroundColor Gray
    $cpTestExe = Join-Path $cpBuildDir "Release\test_autoselect.exe"
    if (Test-Path $cpTestExe) {
        $cpTestProc = Start-Process -FilePath $cpTestExe -WorkingDirectory (Join-Path $cpBuildDir "Release") -Wait -PassThru -NoNewWindow
        if ($cpTestProc.ExitCode -ne 0) {
            throw "test_autoselect.exe failed with exit code $($cpTestProc.ExitCode)"
        }
        Write-Host "      [OK] All C++ Credential Provider tests passed." -ForegroundColor Green
    } else {
        Write-Warning "test_autoselect.exe not found in $cpTestExe. Skipping C++ test run."
    }
} else {
    Write-Host "[6/8] Test execution skipped (-SkipTests specified)." -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# 7. Compile Inno Setup Installer
# -----------------------------------------------------------------------------
Write-Host "[7/8] Compiling Inno Setup 6 installer..." -ForegroundColor Cyan

# Locate ISCC.exe
$isccCandidates = @(
    "C:\Users\Rinu\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$isccPath = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $isccPath) {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $isccPath = $cmd.Source
    }
}

if (-not $isccPath -or -not (Test-Path $isccPath)) {
    throw "Inno Setup compiler (ISCC.exe) not found. Please install Inno Setup 6."
}

$issFile = Join-Path $RepoRoot "installer\Peek.iss"
Write-Host "      Compiling $issFile using $isccPath..." -ForegroundColor Gray

$isccProc = Start-Process -FilePath $isccPath -ArgumentList @("/Qp", $issFile) -WorkingDirectory (Join-Path $RepoRoot "installer") -Wait -PassThru -NoNewWindow
if ($isccProc.ExitCode -ne 0) {
    throw "ISCC compilation failed with exit code $($isccProc.ExitCode)"
}

$expectedInstallerName = "Peek-Setup-$effectiveVersion-x64.exe"
$installerPath = Join-Path $ReleaseDir $expectedInstallerName

if (-not (Test-Path $installerPath)) {
    throw "Expected installer file not found at $installerPath"
}

Invoke-CodeSign -FilePath $installerPath
Write-Host "      [OK] Installer compiled successfully: $expectedInstallerName" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 7b. Generate Update Manifests (stable.json & beta.json or unsigned candidates)
# -----------------------------------------------------------------------------
Write-Host "[7b/8] Processing update manifests..." -ForegroundColor Cyan
$signManifestScript = Join-Path $RepoRoot "scripts\release\sign_manifest.ps1"
if (Test-Path $signManifestScript) {
    $defaultKey = Join-Path $RepoRoot "build\keys\peek_update_primary.key"
    $hasKey = Test-Path $defaultKey
    $shouldGenerateUnsigned = $UnsignedManifest -or (-not $hasKey)

    if ($shouldGenerateUnsigned) {
        Write-Host "      [*] Generating unsigned manifest candidates (signing key kept off CI)..." -ForegroundColor Cyan
        $candArgs = @(
            "-BuildManifest",
            "-UnsignedCandidate",
            "-Channel", "stable",
            "-Version", $effectiveVersion,
            "-InstallerPath", $installerPath,
            "-OutManifestPath", (Join-Path $ReleaseDir "stable.candidate.json")
        )
        if ($CertThumbprint) {
            $candArgs += @("-PublisherThumbprint", $CertThumbprint)
        }

        $candProc = Start-Process -FilePath "powershell.exe" -ArgumentList (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $signManifestScript) + $candArgs) -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
        if ($candProc.ExitCode -ne 0) {
            throw "Failed to generate stable.candidate.json with exit code $($candProc.ExitCode)"
        }

        Copy-Item -Path (Join-Path $ReleaseDir "stable.candidate.json") -Destination (Join-Path $ReleaseDir "manifest-candidate.json") -Force

        $betaCandArgs = @(
            "-BuildManifest",
            "-UnsignedCandidate",
            "-Channel", "beta",
            "-Version", $effectiveVersion,
            "-InstallerPath", $installerPath,
            "-OutManifestPath", (Join-Path $ReleaseDir "beta.candidate.json")
        )
        if ($CertThumbprint) {
            $betaCandArgs += @("-PublisherThumbprint", $CertThumbprint)
        }

        $betaCandProc = Start-Process -FilePath "powershell.exe" -ArgumentList (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $signManifestScript) + $betaCandArgs) -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
        if ($betaCandProc.ExitCode -ne 0) {
            throw "Failed to generate beta.candidate.json with exit code $($betaCandProc.ExitCode)"
        }
        Write-Host "      [OK] Generated unsigned manifest candidates in $ReleaseDir (ready for offline signing)" -ForegroundColor Green
    } else {
        Write-Host "      [+] Signing update manifests with local offline key..." -ForegroundColor Cyan
        $manifestArgs = @(
            "-BuildManifest",
            "-Channel", "stable",
            "-Version", $effectiveVersion,
            "-InstallerPath", $installerPath,
            "-OutManifestPath", (Join-Path $ReleaseDir "stable.json")
        )
        if ($CertThumbprint) {
            $manifestArgs += @("-PublisherThumbprint", $CertThumbprint)
        }

        $manifestProc = Start-Process -FilePath "powershell.exe" -ArgumentList (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $signManifestScript) + $manifestArgs) -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
        if ($manifestProc.ExitCode -ne 0) {
            throw "Failed to generate stable.json manifest with exit code $($manifestProc.ExitCode)"
        }

        $betaManifestArgs = @(
            "-BuildManifest",
            "-Channel", "beta",
            "-Version", $effectiveVersion,
            "-InstallerPath", $installerPath,
            "-OutManifestPath", (Join-Path $ReleaseDir "beta.json")
        )
        if ($CertThumbprint) {
            $betaManifestArgs += @("-PublisherThumbprint", $CertThumbprint)
        }

        $betaProc = Start-Process -FilePath "powershell.exe" -ArgumentList (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $signManifestScript) + $betaManifestArgs) -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow
        if ($betaProc.ExitCode -ne 0) {
            throw "Failed to generate beta.json manifest with exit code $($betaProc.ExitCode)"
        }
        Write-Host "      [OK] Generated and signed stable.json and beta.json in $ReleaseDir" -ForegroundColor Green
    }
}

# -----------------------------------------------------------------------------
# 8. Generate SHA256SUMS
# -----------------------------------------------------------------------------
Write-Host "[8/8] Computing SHA-256 sums and writing SHA256SUMS..." -ForegroundColor Cyan
$sumsFile = Join-Path $ReleaseDir "SHA256SUMS"
$releaseFiles = Get-ChildItem -Path $ReleaseDir -File | Where-Object { $_.Name -ne "SHA256SUMS" }

$sumsLines = @()
foreach ($file in $releaseFiles) {
    $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $line = "$hash  $($file.Name)"
    $sumsLines += $line
}

[System.IO.File]::WriteAllLines($sumsFile, $sumsLines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "      [OK] Wrote SHA256SUMS to $sumsFile" -ForegroundColor Green

# -----------------------------------------------------------------------------
# Summary Output
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "             PEEK RELEASE BUILD COMPLETED                   " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Version: $effectiveVersion" -ForegroundColor White
Write-Host "Artifacts Directory: $ReleaseDir" -ForegroundColor White
Write-Host ""
Write-Host "Release Artifacts:" -ForegroundColor White
foreach ($file in $releaseFiles) {
    $sizeMb = [math]::Round($file.Length / 1MB, 2)
    $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "  - $($file.Name) ($sizeMb MB)" -ForegroundColor Cyan
    Write-Host "    SHA-256: $hash" -ForegroundColor Gray
}
Write-Host ""
Write-Host "SHA256SUMS:" -ForegroundColor White
Get-Content $sumsFile | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
Write-Host "============================================================" -ForegroundColor Green
