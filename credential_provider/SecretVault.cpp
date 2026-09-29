// Peek Face Engine - Secret Vault Implementation
// Manages DPAPI-wrapped Windows password secrets for credential provider

#include "SecretVault.h"
#include <windows.h>
#include <winbase.h>
#include <ntsecapi.h>
#include <vector>
#include <shlwapi.h>
#include <string>
#include <direct.h>

#pragma comment(lib, "Advapi32.lib")
#pragma comment(lib, "Shlwapi.lib")

namespace PeekSecretVault
{
    // Helper function to get the secret file path for a profile
    std::wstring GetSecretFilePath(_In_ PCWSTR pszProfileId)
    {
        // Get the local app data directory
        wchar_t szLocalAppData[MAX_PATH];
        if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, szLocalAppData)))
        {
            std::wstring secretPath = szLocalAppData;
            secretPath += L"\\Peek\\Profiles\\";
            
            // Ensure directory exists
            _wmkdir(secretPath.c_str());
            
            secretPath += pszProfileId;
            secretPath += L".secret";
            
            return secretPath;
        }
        
        return L"";
    }

    BOOL ReadWrappedSecret(
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

    // Decrypt a DPAPI-encrypted password using CryptUnprotectData
    BOOL DecryptWrappedSecret(
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

        // Read the wrapped secret file
        BYTE wrappedData[4096];
        DWORD cbWrappedData = 0;
        
        if (!ReadWrappedSecret(pszProfileId, wrappedData, sizeof(wrappedData), &cbWrappedData))
        {
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
            return FALSE;
        }

        // Copy the decrypted data to output buffer
        if (outBlob.cbData > cbBuffer)
        {
            LocalFree(outBlob.pbData);
            return FALSE;
        }

        memcpy(pbBuffer, outBlob.pbData, outBlob.cbData);
        *pcbActual = outBlob.cbData;

        LocalFree(outBlob.pbData);
        return TRUE;
    }
}