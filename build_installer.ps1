<#
.SYNOPSIS
    Convenience launcher to rebuild the Peek Inno Setup installer in dist\release\.
.EXAMPLE
    .\build_installer.ps1 -SkipTests
.EXAMPLE
    .\build_installer.ps1
.EXAMPLE
    .\build_installer.ps1 -Version "0.2.0-beta.1" -Clean -SkipTests
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipTests,
    [switch]$Clean
)

$scriptPath = Join-Path $PSScriptRoot "scripts\release\build_release.ps1"
$argsList = @()
if ($Version) { $argsList += @("-Version", $Version) }
if ($SkipTests) { $argsList += "-SkipTests" }
if ($Clean) { $argsList += "-Clean" }

& $scriptPath @argsList
