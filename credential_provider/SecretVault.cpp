// Peek Face Engine - Secret Vault Implementation
// Manages DPAPI-wrapped Windows password secrets for credential provider

#include "SecretVault.h"
#include "common.h"
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
#pragma comment(lib, "Ole32.lib")

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
        // Support CURRENT_USER_SESSION for test harness / in-process execution without Session 0 WTSQueryUserToken
        if (dwSessionId == CURRENT_USER_SESSION)
        {
            HANDLE hProcessToken = NULL;
            if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_IMPERSONATE, &hProcessToken))
            {
                HANDLE hUserToken = NULL;
                if (DuplicateToken(hProcessToken, SecurityImpersonation, &hUserToken))
                {
                    CloseHandle(hProcessToken);
                    return hUserToken;
                }
                CloseHandle(hProcessToken);
            }
            return NULL;
        }

        HANDLE hToken = NULL;

        // Get the user token for the specified session
        if (!WTSQueryUserToken(dwSessionId, &hToken))
        {
            return NULL;
        }

        return hToken;
    }

    // Helper to obtain the per-install machine-bound entropy value.
    //
    // SECURITY RESIDUAL-RISK NOTE:
    // Adding machine-bound entropy is defense-in-depth against casual or scripted
    // decryption (e.g. offline decryption if <profile_id>.secret is exfiltrated).
    // It is NOT a fix for the fundamental architectural risk that any process executing
    // under the enrolled user's logon session can discover this entropy and call
    // CryptUnprotectData to recover the plaintext password. This does not make the
    // stored password inherently "safe" or comparable to Windows Hello Face's TPM keys.
    BOOL GetMachineEntropy(_Out_ std::vector<BYTE>& entropy)
    {
        entropy.clear();

        // 1. Environment variable override (useful for isolated unit testing)
        wchar_t szEnv[256] = {};
        DWORD dwEnvLen = GetEnvironmentVariableW(L"PEEK_MACHINE_ENTROPY", szEnv, ARRAYSIZE(szEnv));
        if (dwEnvLen > 0 && dwEnvLen < ARRAYSIZE(szEnv))
        {
            int cbNeeded = WideCharToMultiByte(CP_UTF8, 0, szEnv, dwEnvLen, NULL, 0, NULL, NULL);
            if (cbNeeded > 0)
            {
                entropy.resize(cbNeeded);
                WideCharToMultiByte(CP_UTF8, 0, szEnv, dwEnvLen, reinterpret_cast<char*>(entropy.data()), cbNeeded, NULL, NULL);
                return TRUE;
            }
        }

        // 2. Check %ProgramData%\Peek\machine_entropy.bin
        wchar_t szProgramData[MAX_PATH] = {};
        if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_COMMON_APPDATA, NULL, 0, szProgramData)))
        {
            std::wstring entropyDir = szProgramData;
            entropyDir += L"\\Peek";
            std::wstring entropyPath = entropyDir + L"\\machine_entropy.bin";

            HANDLE hFile = CreateFileW(
                entropyPath.c_str(),
                GENERIC_READ,
                FILE_SHARE_READ,
                NULL,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                NULL
            );

            if (hFile != INVALID_HANDLE_VALUE)
            {
                DWORD dwSize = GetFileSize(hFile, NULL);
                if (dwSize > 0 && dwSize < 4096)
                {
                    entropy.resize(dwSize);
                    DWORD dwRead = 0;
                    if (ReadFile(hFile, entropy.data(), dwSize, &dwRead, NULL) && dwRead > 0)
                    {
                        entropy.resize(dwRead);
                        while (!entropy.empty() && (entropy.back() == '\r' || entropy.back() == '\n' || entropy.back() == ' '))
                        {
                            entropy.pop_back();
                        }
                        CloseHandle(hFile);
                        if (!entropy.empty())
                        {
                            return TRUE;
                        }
                    }
                }
                CloseHandle(hFile);
            }

            // 3. Attempt to generate and store machine entropy file if it doesn't exist
            CreateDirectoryW(entropyDir.c_str(), NULL);
            GUID guid = {};
            if (SUCCEEDED(CoCreateGuid(&guid)))
            {
                wchar_t szGuid[64] = {};
                if (StringFromGUID2(guid, szGuid, ARRAYSIZE(szGuid)) > 0)
                {
                    std::wstring guidStr = szGuid;
                    if (!guidStr.empty() && guidStr.front() == L'{' && guidStr.back() == L'}')
                    {
                        guidStr = guidStr.substr(1, guidStr.length() - 2);
                    }
                    for (auto& c : guidStr) c = towlower(c);

                    int cb = WideCharToMultiByte(CP_UTF8, 0, guidStr.c_str(), static_cast<int>(guidStr.length()), NULL, 0, NULL, NULL);
                    if (cb > 0)
                    {
                        std::vector<BYTE> newEntropy(cb);
                        WideCharToMultiByte(CP_UTF8, 0, guidStr.c_str(), static_cast<int>(guidStr.length()), reinterpret_cast<char*>(newEntropy.data()), cb, NULL, NULL);

                        HANDLE hNewFile = CreateFileW(
                            entropyPath.c_str(),
                            GENERIC_WRITE,
                            FILE_SHARE_READ,
                            NULL,
                            CREATE_NEW,
                            FILE_ATTRIBUTE_NORMAL,
                            NULL
                        );

                        if (hNewFile != INVALID_HANDLE_VALUE)
                        {
                            DWORD dwWritten = 0;
                            WriteFile(hNewFile, newEntropy.data(), static_cast<DWORD>(newEntropy.size()), &dwWritten, NULL);
                            CloseHandle(hNewFile);
                            entropy = newEntropy;
                            return TRUE;
                        }
                    }
                }
            }
        }

        // 4. Fallback to Windows MachineGuid in registry
        HKEY hKey = NULL;
        if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\Cryptography", 0, KEY_READ, &hKey) == ERROR_SUCCESS)
        {
            wchar_t szMachineGuid[128] = {};
            DWORD cbData = sizeof(szMachineGuid);
            if (RegQueryValueExW(hKey, L"MachineGuid", NULL, NULL, reinterpret_cast<LPBYTE>(szMachineGuid), &cbData) == ERROR_SUCCESS)
            {
                std::wstring guidStr = szMachineGuid;
                while (!guidStr.empty() && (guidStr.back() == L'\r' || guidStr.back() == L'\n' || guidStr.back() == L' '))
                {
                    guidStr.pop_back();
                }

                int cb = WideCharToMultiByte(CP_UTF8, 0, guidStr.c_str(), static_cast<int>(guidStr.length()), NULL, 0, NULL, NULL);
                if (cb > 0)
                {
                    entropy.resize(cb);
                    WideCharToMultiByte(CP_UTF8, 0, guidStr.c_str(), static_cast<int>(guidStr.length()), reinterpret_cast<char*>(entropy.data()), cb, NULL, NULL);
                    RegCloseKey(hKey);
                    return TRUE;
                }
            }
            RegCloseKey(hKey);
        }

        return FALSE;
    }

    // Encrypts and writes a wrapped secret file using DPAPI with machine-bound entropy.
    BOOL WriteWrappedSecret(
        _In_ PCWSTR pszProfileId,
        _In_ DWORD dwSessionId,
        _In_reads_bytes_(cbData) const BYTE* pbData,
        _In_ DWORD cbData
    )
    {
        if (!pszProfileId || !pbData || cbData == 0)
        {
            return FALSE;
        }

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

        std::wstring secretPath = GetSecretFilePath(pszProfileId);
        if (secretPath.empty())
        {
            CloseHandle(hUserToken);
            return FALSE;
        }

        std::vector<BYTE> entropy;
        GetMachineEntropy(entropy);
        DATA_BLOB entropyBlob = { static_cast<DWORD>(entropy.size()), entropy.data() };
        DATA_BLOB* pEntropy = entropy.empty() ? NULL : &entropyBlob;

        DATA_BLOB inBlob = { cbData, const_cast<PBYTE>(pbData) };
        DATA_BLOB outBlob = { 0, NULL };

        BOOL bResult = CryptProtectData(
            &inBlob,
            L"PeekCredentialSecret",
            pEntropy,
            NULL,
            NULL,
            0,
            &outBlob
        );

        if (!bResult)
        {
            CloseHandle(hUserToken);
            return FALSE;
        }

        HANDLE hFile = CreateFileW(
            secretPath.c_str(),
            GENERIC_WRITE,
            FILE_SHARE_READ,
            NULL,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            NULL
        );

        if (hFile == INVALID_HANDLE_VALUE)
        {
            SecureZeroMemory(outBlob.pbData, outBlob.cbData);
            LocalFree(outBlob.pbData);
            CloseHandle(hUserToken);
            return FALSE;
        }

        DWORD cbWritten = 0;
        BOOL bWritten = WriteFile(hFile, outBlob.pbData, outBlob.cbData, &cbWritten, NULL);
        CloseHandle(hFile);

        SecureZeroMemory(outBlob.pbData, outBlob.cbData);
        LocalFree(outBlob.pbData);
        CloseHandle(hUserToken);

        return bWritten;
    }

    // Decrypt a DPAPI-encrypted password using CryptUnprotectData with machine-bound entropy.
    // Includes backward-compatible migration path for secrets written with legacy NULL entropy.
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

        // Get machine-bound entropy
        std::vector<BYTE> entropy;
        GetMachineEntropy(entropy);
        DATA_BLOB entropyBlob = { static_cast<DWORD>(entropy.size()), entropy.data() };
        DATA_BLOB* pEntropy = entropy.empty() ? NULL : &entropyBlob;

        // Use CryptUnprotectData to decrypt the password
        DATA_BLOB inBlob = { cbWrappedData, wrappedData };
        DATA_BLOB outBlob = { 0, NULL };

        BOOL bResult = FALSE;
        if (pEntropy != NULL)
        {
            bResult = CryptUnprotectData(
                &inBlob,
                NULL,
                pEntropy,
                NULL,
                NULL,
                0,
                &outBlob
            );
        }

        // Backward-compatibility migration path:
        // On decrypt failure with entropy, attempt legacy decrypt with NULL entropy.
        // If that succeeds, re-encrypt and rewrite the file with entropy going forward,
        // then proceed with the now-decrypted password for this unlock.
        // Log (without logging the password) that a legacy secret was migrated.
        if (!bResult)
        {
            bResult = CryptUnprotectData(
                &inBlob,
                NULL,
                NULL, // legacy: NULL entropy
                NULL,
                NULL,
                0,
                &outBlob
            );

            if (bResult)
            {
                Logger::LogInfo("Legacy secret decrypted with NULL entropy; migrating to machine-bound entropy");
                
                if (pEntropy != NULL)
                {
                    DATA_BLOB plainBlob = { outBlob.cbData, outBlob.pbData };
                    DATA_BLOB migratedBlob = { 0, NULL };
                    if (CryptProtectData(&plainBlob, L"PeekCredentialSecret", pEntropy, NULL, NULL, 0, &migratedBlob))
                    {
                        std::wstring secretPath = GetSecretFilePath(pszProfileId);
                        HANDLE hFile = CreateFileW(
                            secretPath.c_str(),
                            GENERIC_WRITE,
                            FILE_SHARE_READ,
                            NULL,
                            CREATE_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL,
                            NULL
                        );
                        if (hFile != INVALID_HANDLE_VALUE)
                        {
                            DWORD cbWritten = 0;
                            WriteFile(hFile, migratedBlob.pbData, migratedBlob.cbData, &cbWritten, NULL);
                            CloseHandle(hFile);
                            Logger::LogInfo("Successfully migrated and re-encrypted secret file with machine-bound entropy");
                        }
                        SecureZeroMemory(migratedBlob.pbData, migratedBlob.cbData);
                        LocalFree(migratedBlob.pbData);
                    }
                }
            }
        }

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
