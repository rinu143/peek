// Peek Face Engine - Secret Vault Header
// Manages DPAPI-wrapped Windows password secrets for credential provider

#pragma once

#include <windows.h>
#include <ntsecapi.h>
#include <string>
#include <vector>

namespace PeekSecretVault
{
    // Special session ID for testing / in-process execution without Session 0 WTSQueryUserToken
    constexpr DWORD CURRENT_USER_SESSION = 0xFFFFFFFE;

    // Retrieves the per-install machine-bound entropy value used for DPAPI secret wrapping.
    // SECURITY RESIDUAL-RISK NOTE:
    // Adding machine-bound entropy is defense-in-depth against casual or scripted decryption
    // and prevents offline decryption if the .secret file is copied to another machine.
    // It does NOT eliminate the risk that code running under the enrolled user's token
    // can locate this entropy and decrypt the password via CryptUnprotectData.
    BOOL GetMachineEntropy(_Out_ std::vector<BYTE>& entropy);

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

    // Encrypts and writes a wrapped secret file using DPAPI with machine-bound entropy.
    BOOL WriteWrappedSecret(
        _In_ PCWSTR pszProfileId,
        _In_ DWORD dwSessionId,
        _In_reads_bytes_(cbData) const BYTE* pbData,
        _In_ DWORD cbData
    );

    // Decrypts a DPAPI-wrapped secret using CryptUnprotectData with machine-bound entropy,
    // including a backward-compatible migration path for legacy secrets created without entropy.
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
