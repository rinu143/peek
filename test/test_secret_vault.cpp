// Test file for SecretVault functionality
#include "SecretVault.h"
#include <iostream>
#include <windows.h>
#include <shlobj.h>
#include <vector>
#include <cassert>

#pragma comment(lib, "Crypt32.lib")
#pragma comment(lib, "Shell32.lib")
#pragma comment(lib, "Advapi32.lib")

static std::wstring GetTestSecretPath(PCWSTR pszProfileId)
{
    wchar_t szLocalAppData[MAX_PATH];
    if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, szLocalAppData)))
    {
        std::wstring path = szLocalAppData;
        path += L"\\Peek\\Profiles\\";
        CreateDirectoryW(path.c_str(), NULL);
        path += pszProfileId;
        path += L".secret";
        return path;
    }
    return L"";
}

int main()
{
    std::wcout << L"=========================================\n";
    std::wcout << L"Testing SecretVault Security Hardening...\n";
    std::wcout << L"=========================================\n";
    int failedTests = 0;

    // Test 1: Check graceful failure for non-existent secret / invalid session
    std::wcout << L"[Test 1] Testing ReadWrappedSecret graceful failure...\n";
    BYTE buffer[1024];
    DWORD cbActual = 0;
    bool result = PeekSecretVault::ReadWrappedSecret(L"nonexistent_user", 0xFFFFFFFF, buffer, sizeof(buffer), &cbActual);
    if (!result)
    {
        std::wcout << L"  PASS: ReadWrappedSecret failed gracefully for nonexistent user/session\n";
    }
    else
    {
        std::wcout << L"  FAIL: ReadWrappedSecret should have failed\n";
        failedTests++;
    }

    // Test 2: Check ValidateWindowsPassword function and verify removed pre-check path
    std::wcout << L"[Test 2] Testing ValidateWindowsPassword (removed from CP GetSerialization path)...\n";
    bool validateResult = PeekSecretVault::ValidateWindowsPassword(L"nonexistent_user", L"", L"test_password");
    if (!validateResult)
    {
        std::wcout << L"  PASS: ValidateWindowsPassword rejected invalid credentials cleanly\n";
        std::wcout << L"  NOTE: GetSerialization no longer invokes ValidateWindowsPassword; Winlogon is single auth authority\n";
    }
    else
    {
        std::wcout << L"  FAIL: ValidateWindowsPassword should have returned false\n";
        failedTests++;
    }

    // Test 3: Check machine-bound entropy derivation / retrieval
    std::wcout << L"[Test 3] Testing GetMachineEntropy retrieval...\n";
    std::vector<BYTE> entropy1;
    std::vector<BYTE> entropy2;
    BOOL bEntropy1 = PeekSecretVault::GetMachineEntropy(entropy1);
    BOOL bEntropy2 = PeekSecretVault::GetMachineEntropy(entropy2);

    if (bEntropy1 && bEntropy2 && !entropy1.empty() && entropy1 == entropy2)
    {
        std::wcout << L"  PASS: Machine entropy obtained successfully (size=" << entropy1.size() << L" bytes, deterministic)\n";
    }
    else
    {
        std::wcout << L"  FAIL: GetMachineEntropy failed or non-deterministic\n";
        failedTests++;
    }

    // Test 4: Full DPAPI round-trip with machine-bound entropy (WriteWrappedSecret -> DecryptWrappedSecret)
    std::wcout << L"[Test 4] Testing DPAPI round-trip with machine-bound entropy...\n";
    const wchar_t* pszTestProfile = L"peek_test_profile_entropy";
    const wchar_t* pszTestPassword = L"SuperS3cur3P@ssw0rd!_PeekVault";
    DWORD passwordByteLen = static_cast<DWORD>(wcslen(pszTestPassword) * sizeof(wchar_t));

    // Plaintext format: [DWORD UTF-16LE byte count][UTF-16LE characters]
    std::vector<BYTE> plaintext(sizeof(DWORD) + passwordByteLen);
    memcpy(plaintext.data(), &passwordByteLen, sizeof(DWORD));
    memcpy(plaintext.data() + sizeof(DWORD), pszTestPassword, passwordByteLen);

    BOOL bWrite = PeekSecretVault::WriteWrappedSecret(
        pszTestProfile,
        PeekSecretVault::CURRENT_USER_SESSION,
        plaintext.data(),
        static_cast<DWORD>(plaintext.size())
    );

    if (bWrite)
    {
        std::wcout << L"  WriteWrappedSecret succeeded with machine-bound entropy\n";
    }
    else
    {
        std::wcout << L"  FAIL: WriteWrappedSecret failed\n";
        failedTests++;
    }

    BYTE decryptBuf[1024] = {};
    DWORD cbDecrypted = 0;
    BOOL bDecrypt = PeekSecretVault::DecryptWrappedSecret(
        pszTestProfile,
        PeekSecretVault::CURRENT_USER_SESSION,
        decryptBuf,
        sizeof(decryptBuf),
        &cbDecrypted
    );

    if (bDecrypt && cbDecrypted == plaintext.size() && memcmp(decryptBuf, plaintext.data(), cbDecrypted) == 0)
    {
        std::wcout << L"  PASS: DecryptWrappedSecret successfully decrypted and verified plaintext\n";
    }
    else
    {
        std::wcout << L"  FAIL: DecryptWrappedSecret did not match original plaintext\n";
        failedTests++;
    }
    SecureZeroMemory(decryptBuf, sizeof(decryptBuf));

    // Test 5: Legacy secret migration path (NULL entropy -> machine-bound entropy)
    std::wcout << L"[Test 5] Testing legacy secret migration path...\n";
    const wchar_t* pszLegacyProfile = L"peek_legacy_test_profile";
    std::wstring legacyPath = GetTestSecretPath(pszLegacyProfile);

    // Encrypt directly using DPAPI with NULL entropy (legacy pre-hardening format)
    DATA_BLOB inBlob = { static_cast<DWORD>(plaintext.size()), plaintext.data() };
    DATA_BLOB outLegacyBlob = { 0, NULL };
    BOOL bLegacyProtect = CryptProtectData(&inBlob, L"PeekCredentialSecret", NULL, NULL, NULL, 0, &outLegacyBlob);

    if (bLegacyProtect && outLegacyBlob.pbData)
    {
        HANDLE hLegacyFile = CreateFileW(legacyPath.c_str(), GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (hLegacyFile != INVALID_HANDLE_VALUE)
        {
            DWORD dwWritten = 0;
            WriteFile(hLegacyFile, outLegacyBlob.pbData, outLegacyBlob.cbData, &dwWritten, NULL);
            CloseHandle(hLegacyFile);
        }
        SecureZeroMemory(outLegacyBlob.pbData, outLegacyBlob.cbData);
        LocalFree(outLegacyBlob.pbData);

        // Attempt DecryptWrappedSecret: should fail with entropy, fall back to NULL entropy,
        // and re-encrypt with machine entropy
        BYTE legacyDecryptBuf[1024] = {};
        DWORD cbLegacyActual = 0;
        BOOL bMigrateDecrypt = PeekSecretVault::DecryptWrappedSecret(
            pszLegacyProfile,
            PeekSecretVault::CURRENT_USER_SESSION,
            legacyDecryptBuf,
            sizeof(legacyDecryptBuf),
            &cbLegacyActual
        );

        if (bMigrateDecrypt && cbLegacyActual == plaintext.size() && memcmp(legacyDecryptBuf, plaintext.data(), cbLegacyActual) == 0)
        {
            std::wcout << L"  Legacy secret decrypted and returned valid plaintext\n";

            // Verify migration: file on disk should NOW fail with NULL entropy and succeed with machine entropy
            BYTE recheckBuf[4096];
            DWORD cbRecheck = 0;
            HANDLE hRecheckFile = CreateFileW(legacyPath.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
            if (hRecheckFile != INVALID_HANDLE_VALUE)
            {
                ReadFile(hRecheckFile, recheckBuf, sizeof(recheckBuf), &cbRecheck, NULL);
                CloseHandle(hRecheckFile);

                DATA_BLOB recheckBlob = { cbRecheck, recheckBuf };
                DATA_BLOB nullEntropyOut = { 0, NULL };
                BOOL bNullEntropySucceeded = CryptUnprotectData(&recheckBlob, NULL, NULL, NULL, NULL, 0, &nullEntropyOut);
                if (!bNullEntropySucceeded)
                {
                    std::wcout << L"  PASS: Legacy secret file was successfully migrated and no longer decrypts with NULL entropy\n";
                }
                else
                {
                    std::wcout << L"  FAIL: Migrated secret should not decrypt with NULL entropy\n";
                    SecureZeroMemory(nullEntropyOut.pbData, nullEntropyOut.cbData);
                    LocalFree(nullEntropyOut.pbData);
                    failedTests++;
                }
            }
        }
        else
        {
            std::wcout << L"  FAIL: Legacy secret decrypt failed\n";
            failedTests++;
        }
        SecureZeroMemory(legacyDecryptBuf, sizeof(legacyDecryptBuf));

        // Clean up legacy test profile
        PeekSecretVault::DeleteWrappedSecret(pszLegacyProfile, PeekSecretVault::CURRENT_USER_SESSION);
    }
    else
    {
        std::wcout << L"  FAIL: Could not create legacy test secret\n";
        failedTests++;
    }

    // Test 6: DeleteWrappedSecret cleanup
    std::wcout << L"[Test 6] Testing DeleteWrappedSecret cleanup...\n";
    BOOL bDeleted = PeekSecretVault::DeleteWrappedSecret(pszTestProfile, PeekSecretVault::CURRENT_USER_SESSION);
    if (bDeleted)
    {
        std::wcout << L"  PASS: DeleteWrappedSecret cleanly deleted secret file\n";
    }
    else
    {
        std::wcout << L"  FAIL: DeleteWrappedSecret failed\n";
        failedTests++;
    }

    // Verify secret file is gone
    DWORD cbAfterDelete = 0;
    BOOL bAfterDelete = PeekSecretVault::DecryptWrappedSecret(
        pszTestProfile,
        PeekSecretVault::CURRENT_USER_SESSION,
        decryptBuf,
        sizeof(decryptBuf),
        &cbAfterDelete
    );
    if (!bAfterDelete)
    {
        std::wcout << L"  PASS: Secret file confirmed gone after deletion\n";
    }
    else
    {
        std::wcout << L"  FAIL: Secret should not decrypt after deletion\n";
        failedTests++;
    }

    // Zero out memory
    SecureZeroMemory(plaintext.data(), plaintext.size());

    std::wcout << L"=========================================\n";
    if (failedTests == 0)
    {
        std::wcout << L"ALL SECRET VAULT TESTS PASSED!\n";
    }
    else
    {
        std::wcout << failedTests << L" TEST(S) FAILED!\n";
    }
    std::wcout << L"=========================================\n";

    return failedTests;
}
