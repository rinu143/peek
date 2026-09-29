// Peek Face Engine - Secret Vault Header
// Manages DPAPI-wrapped Windows password secrets for credential provider

#pragma once

#include <windows.h>
#include <ntsecapi.h>
#include <string>

namespace PeekSecretVault
{
    // Reads the wrapped secret file for a user and decrypts it using DPAPI
    // Returns TRUE if secret was successfully decrypted, FALSE otherwise
    // The secret file is named <profile_id>.secret and stored alongside the .peek file
    BOOL ReadWrappedSecret(
        _In_ PCWSTR pszProfileId,
        _In_ DWORD dwSessionId,
        _Out_writes_bytes_to_(cbBuffer, *pcbActual) PBYTE pbBuffer,
        _In_ DWORD cbBuffer,
        _Out_ PDWORD pcbActual
    );

    // Decrypts a DPAPI-wrapped secret using CryptUnprotectData
    BOOL DecryptWrappedSecret(
        _In_ PCWSTR pszProfileId,
        _In_ DWORD dwSessionId,
        _Out_writes_bytes_to_(cbBuffer, *pcbActual) PBYTE pbBuffer,
        _In_ DWORD cbBuffer,
        _Out_ PDWORD pcbActual
    );

    // Validates that a password is valid by attempting to log on with it
    BOOL ValidateWindowsPassword(
        _In_ PCWSTR pszUsername,
        _In_ PCWSTR pszDomain,
        _In_ PCWSTR pszPassword
    );

    // Deletes a user's wrapped secret while impersonating that user's session.
    BOOL DeleteWrappedSecret(_In_ PCWSTR pszProfileId, _In_ DWORD dwSessionId);
}
