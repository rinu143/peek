// Peek Face Engine - Secret Vault Implementation
// Manages DPAPI-wrapped Windows password secrets for credential provider

#include "SecretVault.h"
#include <windows.h>
#include <winbase.h>
#include <ntsecapi.h>
#include <vector>
#include <shlwapi.h>
#include <shlobj.h>
#include <string>
#include <direct.h>
#include <wtsapi32.h>

#pragma comment(lib, "Advapi32.lib")
#pragma comment(lib, "Shlwapi.lib")
#pragma comment(lib, "Wtsapi32.lib")
#pragma comment(lib, "Crypt32.lib")
#pragma comment(lib, "Shell32.lib")

namespace PeekSecretVault
{
    HANDLE GetUserTokenFromSessionId(DWORD dwSessionId);
    // RAII wrapper for impersonation to ensure revert happens even on exceptions
    class ImpersonationGuard
    {
    public:
        ImpersonationGuard(HANDLE hToken) : m_hToken(hToken), m_bImpersonated(false)
        {
            if (hToken != NULL)
            {
                m_bImpersonated = ImpersonateLoggedOnUser(hToken);
            }
        }

        ~ImpersonationGuard()
        {
            if (m_bImpersonated)
            {
                RevertToSelf();
            }
        }

        bool IsImpersonated() const { return m_bImpersonated; }

    private:
        HANDLE m_hToken;
        bool m_bImpersonated;
    };
    // Helper function to get the secret file path for a profile
    std::wstring GetSecretFilePath(_In_ PCWSTR pszProfileId)
    {
        // Get the local app data directory
        wchar_t szLocalAppData[MAX_PATH];
        if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, szLocalAppData)))
        {
            std::wstring secretPath = szLocalAppData;
            secretPath += L"\\Peek\\Profiles\\";
            
            secretPath += pszProfileId;
            secretPath += L".secret";
            
            return secretPath;
        }
        
        return L"";
    }

    // Internal helper to read the wrapped secret file under the active impersonation context
    static BOOL ReadWrappedSecretFileInternal(
        _In_ PCWSTR pszProfileId,
        _Out_writes_bytes_to_(cbBuffer, *pcbActual) PBYTE pbBuffer,
        _In_ DWORD cbBuffer,
        _Out_ PDWORD pcbActual
    )
    {
        if (!pszProfileId || !pbBuffer || !pcbActual)
        {
            return FALSE;
        }

        *pcbActual = 0;

        std::wstring secretPath = GetSecretFilePath(pszProfileId);
        if (secretPath.empty())
        {
            return FALSE;
        }

        // Check if file exists
        DWORD dwAttrib = GetFileAttributesW(secretPath.c_str());
        if (dwAttrib == INVALID_FILE_ATTRIBUTES)
        {
            return FALSE;
        }

        // Read the entire file
        HANDLE hFile = CreateFileW(
            secretPath.c_str(),
            GENERIC_READ,
            FILE_SHARE_READ,
            NULL,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            NULL
        );

        if (hFile == INVALID_HANDLE_VALUE)
        {
            return FALSE;
        }

        DWORD cbFileSize = GetFileSize(hFile, NULL);
        if (cbFileSize == 0 || cbFileSize > cbBuffer)
        {
            CloseHandle(hFile);
            return FALSE;
        }

        DWORD cbRead = 0;
        BOOL bResult = ReadFile(hFile, pbBuffer, cbFileSize, &cbRead, NULL);
        CloseHandle(hFile);

        if (!bResult)
        {
            return FALSE;
        }

        *pcbActual = cbRead;
        return TRUE;
    }

    BOOL ReadWrappedSecret(
        _In_ PCWSTR pszProfileId,
        _In_ DWORD dwSessionId,
        _Out_writes_bytes_to_(cbBuffer, *pcbActual) PBYTE pbBuffer,
        _In_ DWORD cbBuffer,
        _Out_ PDWORD pcbActual
    )
    {
        if (!pszProfileId || !pbBuffer || !pcbActual)
        {
            return FALSE;
        }

        *pcbActual = 0;

        HANDLE hUserToken = GetUserTokenFromSessionId(dwSessionId);
        if (!hUserToken)
        {
            return FALSE;
        }
        ImpersonationGuard impersonation(hUserToken);
        if (!impersonation.IsImpersonated())
        {
            CloseHandle(hUserToken);
            return FALSE;
        }

        BOOL bResult = ReadWrappedSecretFileInternal(pszProfileId, pbBuffer, cbBuffer, pcbActual);
        CloseHandle(hUserToken);
        return bResult;
    }

    BOOL ValidateWindowsPassword(
        _In_ PCWSTR pszUsername,
        _In_ PCWSTR pszDomain,
        _In_ PCWSTR pszPassword
    )
    {
        if (!pszUsername || !pszPassword)
        {
            return FALSE;
        }

        // Use LogonUser to validate the password
        HANDLE hToken = NULL;
        BOOL bResult = LogonUserW(
            pszUsername,
            pszDomain,
            pszPassword,
            LOGON32_LOGON_INTERACTIVE,
            LOGON32_PROVIDER_DEFAULT,
            &hToken
        );

        if (bResult && hToken)
        {
            CloseHandle(hToken);
        }

        return bResult;
    }

    // Helper to get user token from session ID - for impersonation.
    HANDLE GetUserTokenFromSessionId(DWORD dwSessionId)
    {
        HANDLE hToken = NULL;

        // Get the user token for the specified session
        if (!WTSQueryUserToken(dwSessionId, &hToken))
        {
            return NULL;
        }

        return hToken;
    }

    // Decrypt a DPAPI-encrypted password using CryptUnprotectData
    BOOL DecryptWrappedSecret(
        _In_ PCWSTR pszProfileId,
        _In_ DWORD dwSessionId,
        _Out_writes_bytes_to_(cbBuffer, *pcbActual) PBYTE pbBuffer,
        _In_ DWORD cbBuffer,
        _Out_ PDWORD pcbActual
    )
    {
        if (!pszProfileId || !pbBuffer || !pcbActual)
        {
            return FALSE;
        }

        *pcbActual = 0;

        // Read the wrapped secret file
        BYTE wrappedData[4096];
        DWORD cbWrappedData = 0;
        
        // Consolidate token fetch and impersonation: fetch user token once and hold
        // the impersonation scope across both the file read and CryptUnprotectData.
        HANDLE hUserToken = GetUserTokenFromSessionId(dwSessionId);
        if (!hUserToken)
        {
            return FALSE;
        }

        ImpersonationGuard impersonation(hUserToken);
        if (!impersonation.IsImpersonated())
        {
            CloseHandle(hUserToken);
            return FALSE;
        }

        if (!ReadWrappedSecretFileInternal(pszProfileId, wrappedData, sizeof(wrappedData), &cbWrappedData))
        {
            CloseHandle(hUserToken);
            return FALSE;
        }

        // Use CryptUnprotectData to decrypt the password
        DATA_BLOB inBlob = { cbWrappedData, wrappedData };
        DATA_BLOB outBlob = { 0, NULL };

        BOOL bResult = CryptUnprotectData(
            &inBlob,
            NULL,       // ppszDataDescr (description) - can be NULL
            NULL,       // pPctd (optional entropy) - can be NULL
            NULL,       // pvReserved (reserved for future use) - must be NULL
            NULL,       // pPromptStruct (prompt structure) - can be NULL
            0,          // dwFlags (no flags needed)
            &outBlob    // ppData (output data blob)
        );

        if (!bResult)
        {
            SecureZeroMemory(wrappedData, sizeof(wrappedData));
            CloseHandle(hUserToken);
            return FALSE;
        }

        // Copy the decrypted data to output buffer
        if (outBlob.cbData > cbBuffer)
        {
            SecureZeroMemory(outBlob.pbData, outBlob.cbData);
            LocalFree(outBlob.pbData);
            SecureZeroMemory(wrappedData, sizeof(wrappedData));
            CloseHandle(hUserToken);
            return FALSE;
        }

        memcpy(pbBuffer, outBlob.pbData, outBlob.cbData);
        *pcbActual = outBlob.cbData;

        // Securely zero out the plaintext password before freeing memory
        SecureZeroMemory(outBlob.pbData, outBlob.cbData);
        LocalFree(outBlob.pbData);
        SecureZeroMemory(wrappedData, sizeof(wrappedData));
        CloseHandle(hUserToken);
        return TRUE;
    }

    // Delete a wrapped secret file (for stale secret handling)
    BOOL DeleteWrappedSecret(_In_ PCWSTR pszProfileId, _In_ DWORD dwSessionId)
    {
        if (!pszProfileId)
        {
            return FALSE;
        }

        HANDLE hUserToken = GetUserTokenFromSessionId(dwSessionId);
        if (!hUserToken) return FALSE;
        ImpersonationGuard impersonation(hUserToken);
        if (!impersonation.IsImpersonated())
        {
            CloseHandle(hUserToken);
            return FALSE;
        }
        std::wstring secretPath = GetSecretFilePath(pszProfileId);
        if (secretPath.empty())
        {
            CloseHandle(hUserToken);
            return FALSE;
        }

        // Attempt to delete the file
        BOOL deleted = DeleteFileW(secretPath.c_str());
        CloseHandle(hUserToken);
        return deleted;
    }
}
