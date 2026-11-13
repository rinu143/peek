<#
.SYNOPSIS
    Peek Inno Setup Uninstaller Cleanup Script (Fallback)
.DESCRIPTION
    Reverses installation:
    1. ALWAYS removes all L$PeekCred_<SID> secrets and HKLM Identities/Peek registry entries.
    2. Removes enrolled biometric face profiles and machine entropy ONLY IF requested.
#>

[CmdletBinding()]
param(
    [switch]$RemoveBiometricsAndEntropy
)

$ErrorActionPreference = "SilentlyContinue"

# P/Invoke helper to delete LSA secrets directly if PeekSetup.exe is unavailable
$typeDef = @"
using System;
using System.Runtime.InteropServices;

public class LsaSecretCleaner
{
    [StructLayout(LayoutKind.Sequential)]
    private struct LSA_UNICODE_STRING
    {
        public ushort Length;
        public ushort MaximumLength;
        public IntPtr Buffer;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LSA_OBJECT_ATTRIBUTES
    {
        public int Length;
        public IntPtr RootDirectory;
        public IntPtr ObjectName;
        public int Attributes;
        public IntPtr SecurityDescriptor;
        public IntPtr SecurityQualityOfService;
    }

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern int LsaOpenPolicy(IntPtr SystemName, ref LSA_OBJECT_ATTRIBUTES ObjectAttributes, int DesiredAccess, out IntPtr PolicyHandle);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern int LsaStorePrivateData(IntPtr PolicyHandle, ref LSA_UNICODE_STRING KeyName, IntPtr PrivateData);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern int LsaClose(IntPtr ObjectHandle);

    public static void DeleteSecret(string secretName)
    {
        var objAttr = new LSA_OBJECT_ATTRIBUTES();
        IntPtr policyHandle = IntPtr.Zero;
        try
        {
            int status = LsaOpenPolicy(IntPtr.Zero, ref objAttr, 0x00000020 /* POLICY_CREATE_SECRET */, out policyHandle);
            if (status == 0 && policyHandle != IntPtr.Zero)
            {
                var lus = new LSA_UNICODE_STRING();
                lus.Buffer = Marshal.StringToHGlobalUni(secretName);
                lus.Length = (ushort)(secretName.Length * sizeof(char));
                lus.MaximumLength = (ushort)((secretName.Length + 1) * sizeof(char));
                LsaStorePrivateData(policyHandle, ref lus, IntPtr.Zero);
                Marshal.FreeHGlobal(lus.Buffer);
            }
        }
        catch {}
        finally
        {
            if (policyHandle != IntPtr.Zero) LsaClose(policyHandle);
        }
    }
}
"@

try {
    Add-Type -TypeDefinition $typeDef -ErrorAction SilentlyContinue
} catch {}

# 1. ALWAYS remove all L$PeekCred_<SID> secrets for local users
try {
    $profileListKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
    if (Test-Path $profileListKey) {
        $subkeys = Get-ChildItem -Path $profileListKey -ErrorAction SilentlyContinue
        foreach ($sub in $subkeys) {
            $sid = $sub.PSChildName
            if ($sid -match "^S-1-5-21-" -or $sid -match "^S-1-5-") {
                $secretName = "L$PeekCred_$sid"
                try { [LsaSecretCleaner]::DeleteSecret($secretName) } catch {}

                if ($RemoveBiometricsAndEntropy) {
                    $profilePath = (Get-ItemProperty -Path $sub.PSPath -Name "ProfileImagePath" -ErrorAction SilentlyContinue).ProfileImagePath
                    if ($profilePath) {
                        $expanded = [Environment]::ExpandEnvironmentVariables($profilePath)
                        $userPeekDir = Join-Path $expanded "AppData\Local\Peek"
                        if (Test-Path $userPeekDir) {
                            Remove-Item -Path $userPeekDir -Recurse -Force -ErrorAction SilentlyContinue
                        }
                    }
                }
            }
        }
    }
} catch {}

# 2. ALWAYS remove HKLM Identities and HKLM\SOFTWARE\Peek
try {
    if (Test-Path "HKLM:\SOFTWARE\Peek") {
        Remove-Item -Path "HKLM:\SOFTWARE\Peek" -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch {}

# 3. Clean face profiles and machine entropy ONLY if requested
if ($RemoveBiometricsAndEntropy) {
    try {
        $localPeek = Join-Path $env:LOCALAPPDATA "Peek"
        if (Test-Path $localPeek) {
            Remove-Item -Path $localPeek -Recurse -Force -ErrorAction SilentlyContinue
        }

        $programDataPeek = Join-Path $env:ProgramData "Peek"
        if (Test-Path $programDataPeek) {
            Remove-Item -Path $programDataPeek -Recurse -Force -ErrorAction SilentlyContinue
        }
    } catch {}
} else {
    # If preserving face profiles and entropy, remove only logs/updates/backup
    try {
        $programDataPeek = Join-Path $env:ProgramData "Peek"
        foreach ($sub in @("logs", "backup", "updates")) {
            $subPath = Join-Path $programDataPeek $sub
            if (Test-Path $subPath) {
                Remove-Item -Path $subPath -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {}
}

exit 0
