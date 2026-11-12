<#
.SYNOPSIS
    Generates ECDSA P-256 keys, constructs, signs, and verifies update manifests
    (stable.json / beta.json) for Peek Update Service.

.DESCRIPTION
    Supports three modes:
      1. -GenKey: Generates a new ECDSA P-256 (secp256r1) key pair.
         Writes private key to offline storage file (-OutKeyPath) and outputs public key.
      2. -BuildManifest: Computes SHA-256 and size from release installer, generates
         canonical manifest without signature, signs with ECDSA P-256, and outputs JSON.
      3. -Verify: Reads a manifest JSON, canonicalizes payload without "signature",
         and verifies signature against public key (-PublicKeyPath or -PublicKeyBase64).

.PARAMETER GenKey
    Generate new ECDSA P-256 key pair.

.PARAMETER OutKeyPath
    Output path for generated private key (default: build\keys\peek_update_key.key).

.PARAMETER OutPublicKeyPath
    Output path for generated public key (default: build\keys\peek_update_key.pub).

.PARAMETER BuildManifest
    Construct and sign an update manifest.

.PARAMETER Channel
    Release channel: "stable" or "beta" (default: "stable").

.PARAMETER Version
    Version string (default: read from VERSION file).

.PARAMETER InstallerPath
    Path to Peek installer EXE (default: dist\release\Peek-Setup-<version>-x64.exe).

.PARAMETER Url
    Download URL for installer.

.PARAMETER NotesUrl
    Release notes URL.

.PARAMETER PublisherThumbprint
    Expected Authenticode certificate SHA-1/SHA-256 thumbprint.

.PARAMETER Critical
    Mark update as critical security update.

.PARAMETER MinSupportedVersion
    Minimum version supported for direct in-place update (default: "0.1.0").

.PARAMETER ExpiresDays
    Days until manifest expires (default: 30 days).

.PARAMETER PrivateKeyPath
    Path to ECDSA P-256 private key file used for signing.

.PARAMETER OutManifestPath
    Path to output signed manifest JSON (default: dist\release\<channel>.json).

.PARAMETER Verify
    Verify an existing manifest file.

.PARAMETER ManifestPath
    Path to manifest file to verify.

.PARAMETER PublicKeyPath
    Path to public key file for verification.

.PARAMETER PublicKeyBase64
    Base64 public key for verification.
#>

[CmdletBinding()]
param(
    [switch]$GenKey,
    [string]$OutKeyPath,
    [string]$OutPublicKeyPath,

    [switch]$BuildManifest,
    [switch]$UnsignedCandidate,
    [switch]$SignCandidate,
    [string]$Channel = "stable",
    [string]$Version,
    [string]$InstallerPath,
    [string]$Url,
    [string]$NotesUrl,
    [string]$PublisherThumbprint = "",
    [switch]$Critical,
    [string]$MinSupportedVersion = "0.1.0",
    [int]$ExpiresDays = 30,
    [string]$PrivateKeyPath,
    [string]$OutManifestPath,

    [switch]$Verify,
    [string]$ManifestPath,
    [string]$PublicKeyPath,
    [string]$PublicKeyBase64
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $RepoRoot "VERSION"))) {
    $RepoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
}

# -----------------------------------------------------------------------------
# Helper: Canonical JSON Formatter
# Must match C# CanonicalJsonSerializer strictly:
# Compact, sorted property names, UTF-8.
# -----------------------------------------------------------------------------
function Get-CanonicalJson {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$ManifestData
    )

    # Sorted keys: channel, critical, expires, min_supported_version, notes_url, publisher_thumbprint, released, schema, sha256, size, url, version
    $critStr = if ($ManifestData["critical"]) { "true" } else { "false" }
    $schemaVal = [int]$ManifestData["schema"]
    $sizeVal = [long]$ManifestData["size"]

    $sb = New-Object System.Text.StringBuilder
    $null = $sb.Append("{")
    $null = $sb.Append('"channel":"').Append($ManifestData["channel"]).Append('",')
    $null = $sb.Append('"critical":').Append($critStr).Append(',')
    $null = $sb.Append('"expires":"').Append($ManifestData["expires"]).Append('",')
    $null = $sb.Append('"min_supported_version":"').Append($ManifestData["min_supported_version"]).Append('",')
    $null = $sb.Append('"notes_url":"').Append($ManifestData["notes_url"]).Append('",')
    $null = $sb.Append('"publisher_thumbprint":"').Append($ManifestData["publisher_thumbprint"]).Append('",')
    $null = $sb.Append('"released":"').Append($ManifestData["released"]).Append('",')
    $null = $sb.Append('"schema":').Append($schemaVal).Append(',')
    $null = $sb.Append('"sha256":"').Append($ManifestData["sha256"]).Append('",')
    $null = $sb.Append('"size":').Append($sizeVal).Append(',')
    $null = $sb.Append('"url":"').Append($ManifestData["url"]).Append('",')
    $null = $sb.Append('"version":"').Append($ManifestData["version"]).Append('"')
    $null = $sb.Append("}")

    return $sb.ToString()
}

# -----------------------------------------------------------------------------
# MODE 1: --gen-key
# -----------------------------------------------------------------------------
if ($GenKey) {
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "       PEEK ECDSA P-256 KEY PAIR GENERATOR                  " -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan

    $params = New-Object System.Security.Cryptography.CngKeyCreationParameters
    $params.ExportPolicy = [System.Security.Cryptography.CngExportPolicies]::AllowPlaintextExport
    $keyName = "PeekKey-" + [Guid]::NewGuid().ToString()
    $cngKey = [System.Security.Cryptography.CngKey]::Create([System.Security.Cryptography.CngAlgorithm]::ECDsaP256, $keyName, $params)

    $pubBytes = $cngKey.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)
    $privBytes = $cngKey.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPrivateBlob)
    $cngKey.Delete()

    $pubBase64 = [Convert]::ToBase64String($pubBytes)
    $privBase64 = [Convert]::ToBase64String($privBytes)

    if (-not $OutKeyPath) {
        $keysDir = Join-Path $RepoRoot "build\keys"
        if (-not (Test-Path $keysDir)) { New-Item -ItemType Directory -Path $keysDir -Force | Out-Null }
        $OutKeyPath = Join-Path $keysDir "peek_update_key.key"
    } else {
        $parent = Split-Path -Parent $OutKeyPath
        if ($parent -and -not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
    }
    if (-not $OutPublicKeyPath) {
        $OutPublicKeyPath = [System.IO.Path]::ChangeExtension($OutKeyPath, ".pub")
    }

    [System.IO.File]::WriteAllText($OutKeyPath, $privBase64)
    [System.IO.File]::WriteAllText($OutPublicKeyPath, $pubBase64)

    Write-Host "[OK] Private key saved to: $OutKeyPath" -ForegroundColor Green
    Write-Host "[OK] Public key saved to : $OutPublicKeyPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "Public Key Base64:" -ForegroundColor White
    Write-Host $pubBase64 -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Store the private key offline and secure it with strict access permissions." -ForegroundColor Gray
    return
}

# -----------------------------------------------------------------------------
# MODE 2: --verify
# -----------------------------------------------------------------------------
if ($Verify) {
    if (-not $ManifestPath -or -not (Test-Path $ManifestPath)) {
        throw "Manifest file not found: $ManifestPath"
    }

    $jsonRaw = Get-Content $ManifestPath -Raw -Encoding utf8
    $manifestObj = ConvertFrom-Json $jsonRaw

    if (-not $manifestObj.signature) {
        throw "Manifest does not contain a 'signature' field."
    }

    $manifestDict = @{
        "channel"              = [string]$manifestObj.channel
        "critical"             = [bool]$manifestObj.critical
        "expires"              = [string]$manifestObj.expires
        "min_supported_version"= [string]$manifestObj.min_supported_version
        "notes_url"            = [string]$manifestObj.notes_url
        "publisher_thumbprint" = [string]$manifestObj.publisher_thumbprint
        "released"             = [string]$manifestObj.released
        "schema"               = [int]$manifestObj.schema
        "sha256"               = [string]$manifestObj.sha256
        "size"                 = [long]$manifestObj.size
        "url"                  = [string]$manifestObj.url
        "version"              = [string]$manifestObj.version
    }

    $canonicalJson = Get-CanonicalJson -ManifestData $manifestDict
    $dataBytes = [System.Text.Encoding]::UTF8.GetBytes($canonicalJson)
    $sigBytes = [Convert]::FromBase64String($manifestObj.signature)

    if ($PublicKeyPath -and (Test-Path $PublicKeyPath)) {
        $PublicKeyBase64 = (Get-Content $PublicKeyPath -Raw).Trim()
    }

    if (-not $PublicKeyBase64) {
        # Check default build\keys\peek_update_primary.pub
        $defaultPub = Join-Path $RepoRoot "build\keys\peek_update_primary.pub"
        if (Test-Path $defaultPub) {
            $PublicKeyBase64 = (Get-Content $defaultPub -Raw).Trim()
        } else {
            throw "Public key not provided for verification (-PublicKeyPath or -PublicKeyBase64)."
        }
    }

    $pubBytes = [Convert]::FromBase64String($PublicKeyBase64)
    $cngKey = [System.Security.Cryptography.CngKey]::Import($pubBytes, [System.Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)
    $dsa = New-Object System.Security.Cryptography.ECDsaCng $cngKey
    $isValid = $dsa.VerifyData($dataBytes, $sigBytes)

    if ($isValid) {
        Write-Host "[OK] Manifest signature verified successfully for version $($manifestObj.version) ($($manifestObj.channel))." -ForegroundColor Green
    } else {
        Write-Host "[!] ERROR: Manifest signature verification FAILED." -ForegroundColor Red
        exit 1
    }
    return
}

# -----------------------------------------------------------------------------
# MODE 2b: Sign Candidate Manifest
# -----------------------------------------------------------------------------
if ($SignCandidate) {
    if (-not $ManifestPath -or -not (Test-Path $ManifestPath)) {
        throw "Candidate manifest file not found: $ManifestPath"
    }

    $jsonRaw = Get-Content $ManifestPath -Raw -Encoding utf8
    $manifestObj = ConvertFrom-Json $jsonRaw

    $manifestDict = @{
        "channel"              = [string]$manifestObj.channel
        "critical"             = [bool]$manifestObj.critical
        "expires"              = [string]$manifestObj.expires
        "min_supported_version"= [string]$manifestObj.min_supported_version
        "notes_url"            = [string]$manifestObj.notes_url
        "publisher_thumbprint" = [string]$manifestObj.publisher_thumbprint
        "released"             = [string]$manifestObj.released
        "schema"               = [int]$manifestObj.schema
        "sha256"               = [string]$manifestObj.sha256
        "size"                 = [long]$manifestObj.size
        "url"                  = [string]$manifestObj.url
        "version"              = [string]$manifestObj.version
    }

    $canonicalJson = Get-CanonicalJson -ManifestData $manifestDict
    $dataBytes = [System.Text.Encoding]::UTF8.GetBytes($canonicalJson)

    # Locate private key
    if (-not $PrivateKeyPath) {
        $defaultKey = Join-Path $RepoRoot "build\keys\peek_update_primary.key"
        if (Test-Path $defaultKey) {
            $PrivateKeyPath = $defaultKey
        } else {
            throw "Private key not specified (-PrivateKeyPath) and default not found at $defaultKey."
        }
    }

    $privBase64 = (Get-Content $PrivateKeyPath -Raw).Trim()
    $privBytes = [Convert]::FromBase64String($privBase64)
    $cngKey = [System.Security.Cryptography.CngKey]::Import($privBytes, [System.Security.Cryptography.CngKeyBlobFormat]::EccPrivateBlob)
    $dsa = New-Object System.Security.Cryptography.ECDsaCng $cngKey
    $sigBytes = $dsa.SignData($dataBytes)
    $sigBase64 = [Convert]::ToBase64String($sigBytes)

    $manifestOutput = [ordered]@{
        "schema"               = [int]$manifestObj.schema
        "channel"              = [string]$manifestObj.channel
        "version"              = [string]$manifestObj.version
        "released"             = [string]$manifestObj.released
        "min_supported_version"= [string]$manifestObj.min_supported_version
        "critical"             = [bool]$manifestObj.critical
        "notes_url"            = [string]$manifestObj.notes_url
        "url"                  = [string]$manifestObj.url
        "sha256"               = [string]$manifestObj.sha256
        "size"                 = [long]$manifestObj.size
        "publisher_thumbprint" = [string]$manifestObj.publisher_thumbprint
        "expires"              = [string]$manifestObj.expires
        "signature"            = $sigBase64
    }

    if (-not $OutManifestPath) {
        $parent = Split-Path -Parent $ManifestPath
        $leaf = Split-Path -Leaf $ManifestPath
        $newLeaf = $leaf -replace "\.candidate\.json$", ".json"
        if ($newLeaf -eq $leaf) { $newLeaf = "$($manifestObj.channel).json" }
        $OutManifestPath = Join-Path $parent $newLeaf
    }

    $jsonOutput = ConvertTo-Json -InputObject $manifestOutput -Depth 10
    [System.IO.File]::WriteAllText($OutManifestPath, $jsonOutput, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] Candidate manifest signed successfully: $OutManifestPath" -ForegroundColor Green
    Write-Host "     Version: $($manifestObj.version) ($($manifestObj.channel))" -ForegroundColor Gray
    Write-Host "     Sig    : $sigBase64" -ForegroundColor Gray
    return
}

# -----------------------------------------------------------------------------
# MODE 3: Build & Sign Manifest (or Build Unsigned Candidate)
# -----------------------------------------------------------------------------
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       PEEK UPDATE MANIFEST BUILDER & SIGNER                " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionFile = Join-Path $RepoRoot "VERSION"
    if (Test-Path $versionFile) {
        $Version = (Get-Content $versionFile -Raw).Trim()
    } else {
        throw "Version not specified and VERSION file not found."
    }
}

if (-not $InstallerPath) {
    $InstallerPath = Join-Path $RepoRoot "dist\release\Peek-Setup-$Version-x64.exe"
}
if (-not (Test-Path $InstallerPath)) {
    throw "Installer file not found at $InstallerPath"
}

$installerItem = Get-Item $InstallerPath
$fileSize = $installerItem.Length
$fileHash = (Get-FileHash -Path $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()

if (-not $Url) {
    $Url = "https://github.com/peek/releases/download/v$Version/$($installerItem.Name)"
}
if (-not $NotesUrl) {
    $NotesUrl = "https://github.com/peek/releases/tag/v$Version"
}

$nowUtc = [DateTime]::UtcNow
$releasedStr = $nowUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
$expiresStr = $nowUtc.AddDays($ExpiresDays).ToString("yyyy-MM-ddTHH:mm:ssZ")

# If UnsignedCandidate is requested, output unsigned candidate JSON without private key
if ($UnsignedCandidate) {
    $candidateOutput = [ordered]@{
        "schema"               = 1
        "channel"              = $Channel
        "version"              = $Version
        "released"             = $releasedStr
        "min_supported_version"= $MinSupportedVersion
        "critical"             = [bool]$Critical
        "notes_url"            = $NotesUrl
        "url"                  = $Url
        "sha256"               = $fileHash
        "size"                 = $fileSize
        "publisher_thumbprint" = $PublisherThumbprint
        "expires"              = $expiresStr
        "signature"            = ""
    }

    $jsonOutput = ConvertTo-Json -InputObject $candidateOutput -Depth 10

    if (-not $OutManifestPath) {
        $ReleaseDir = Join-Path $RepoRoot "dist\release"
        if (-not (Test-Path $ReleaseDir)) { New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null }
        $OutManifestPath = Join-Path $ReleaseDir "$Channel.candidate.json"
    }

    [System.IO.File]::WriteAllText($OutManifestPath, $jsonOutput, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] Unsigned manifest candidate generated: $OutManifestPath" -ForegroundColor Green
    Write-Host "     Version  : $Version ($Channel)" -ForegroundColor Gray
    Write-Host "     SHA256   : $fileHash" -ForegroundColor Gray
    Write-Host "     Size     : $fileSize bytes" -ForegroundColor Gray
    Write-Host "     Expires  : $expiresStr" -ForegroundColor Gray
    Write-Host "     Signature: <UNSIGNED - SIGN OFFLINE>" -ForegroundColor Yellow
    return
}

$manifestDict = @{
    "schema"               = 1
    "channel"              = $Channel
    "version"              = $Version
    "released"             = $releasedStr
    "min_supported_version"= $MinSupportedVersion
    "critical"             = [bool]$Critical
    "notes_url"            = $NotesUrl
    "url"                  = $Url
    "sha256"               = $fileHash
    "size"                 = $fileSize
    "publisher_thumbprint" = $PublisherThumbprint
    "expires"              = $expiresStr
}

$canonicalJson = Get-CanonicalJson -ManifestData $manifestDict
$dataBytes = [System.Text.Encoding]::UTF8.GetBytes($canonicalJson)

# Locate private key
if (-not $PrivateKeyPath) {
    $defaultKey = Join-Path $RepoRoot "build\keys\peek_update_primary.key"
    if (Test-Path $defaultKey) {
        $PrivateKeyPath = $defaultKey
    } else {
        throw "Private key not specified and default not found at $defaultKey. Run with -GenKey first."
    }
}

$privBase64 = (Get-Content $PrivateKeyPath -Raw).Trim()
$privBytes = [Convert]::FromBase64String($privBase64)

$cngKey = [System.Security.Cryptography.CngKey]::Import($privBytes, [System.Security.Cryptography.CngKeyBlobFormat]::EccPrivateBlob)
$dsa = New-Object System.Security.Cryptography.ECDsaCng $cngKey
$sigBytes = $dsa.SignData($dataBytes)
$sigBase64 = [Convert]::ToBase64String($sigBytes)

# Build final JSON output with indentation for readability
$manifestOutput = [ordered]@{
    "schema"               = 1
    "channel"              = $Channel
    "version"              = $Version
    "released"             = $releasedStr
    "min_supported_version"= $MinSupportedVersion
    "critical"             = [bool]$Critical
    "notes_url"            = $NotesUrl
    "url"                  = $Url
    "sha256"               = $fileHash
    "size"                 = $fileSize
    "publisher_thumbprint" = $PublisherThumbprint
    "expires"              = $expiresStr
    "signature"            = $sigBase64
}

$jsonOutput = ConvertTo-Json -InputObject $manifestOutput -Depth 10

if (-not $OutManifestPath) {
    $ReleaseDir = Join-Path $RepoRoot "dist\release"
    if (-not (Test-Path $ReleaseDir)) { New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null }
    $OutManifestPath = Join-Path $ReleaseDir "$Channel.json"
}

[System.IO.File]::WriteAllText($OutManifestPath, $jsonOutput, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[OK] Signed manifest generated: $OutManifestPath" -ForegroundColor Green
Write-Host "     Version: $Version ($Channel)" -ForegroundColor Gray
Write-Host "     SHA256 : $fileHash" -ForegroundColor Gray
Write-Host "     Size   : $fileSize bytes" -ForegroundColor Gray
Write-Host "     Expires: $expiresStr" -ForegroundColor Gray
Write-Host "     Sig    : $sigBase64" -ForegroundColor Gray
