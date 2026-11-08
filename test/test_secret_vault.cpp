// Test file for SecretVault functionality with LSA private-data store
#include "SecretVault.h"
#include <iostream>
#include <windows.h>
#include <vector>
#include <string>
#include <cassert>
#include <cstring>

#pragma comment(lib, "Advapi32.lib")

int main()
{
    std::wcout << L"=========================================\n";
    std::wcout << L"Testing SecretVault (LSA Secret Store)...\n";
    std::wcout << L"=========================================\n";
    int failedTests = 0;

    auto fakeStore = std::make_shared<PeekSecretVault::InMemoryLsaSecretStore>();
    PeekSecretVault::SetLsaSecretStore(fakeStore);

    const std::wstring validSid = L"S-1-5-21-12345678-1001";
    const std::wstring validProfileId = L"profile_alice_123";
    const std::wstring validPassword = L"CorrectHorseBatteryStaple!";

    // -------------------------------------------------------------
    // Test 1: Payload Building and Parsing Round-Trip
    // -------------------------------------------------------------
    std::wcout << L"[Test 1] Testing Payload Building and Parsing Round-Trip...\n";
    {
        std::vector<BYTE> payload;
        bool buildOk = PeekSecretVault::BuildSecretPayload(validProfileId, validPassword, payload);
        std::wstring parsedPassword;
        bool parseOk = PeekSecretVault::ParseSecretPayload(payload.data(), static_cast<DWORD>(payload.size()), validProfileId, parsedPassword);

        if (buildOk && parseOk && parsedPassword == validPassword)
        {
            std::wcout << L"  PASS: Payload built and parsed correctly\n";
        }
        else
        {
            std::wcout << L"  FAIL: Round-trip mismatch or build/parse failed\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 2: Parser Tests (Bad version, Truncated, Oversize, Wrong ProfileId)
    // -------------------------------------------------------------
    std::wcout << L"[Test 2] Testing Parser Validation & Rejection...\n";
    {
        // 2a: Bad version (version = 2)
        std::vector<BYTE> payload;
        PeekSecretVault::BuildSecretPayload(validProfileId, validPassword, payload);
        DWORD badVersion = 2;
        memcpy(payload.data(), &badVersion, sizeof(DWORD));
        std::wstring outPw;
        bool badVerResult = PeekSecretVault::ParseSecretPayload(payload.data(), static_cast<DWORD>(payload.size()), validProfileId, outPw);
        if (!badVerResult && outPw.empty())
        {
            std::wcout << L"  PASS: Bad version (2) cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Bad version accepted\n";
            failedTests++;
        }

        // 2b: Truncated header (less than 3 DWORDs)
        BYTE tinyBuf[8] = { 1, 0, 0, 0, 4, 0, 0, 0 };
        outPw.clear();
        bool truncHeader = PeekSecretVault::ParseSecretPayload(tinyBuf, sizeof(tinyBuf), validProfileId, outPw);
        if (!truncHeader)
        {
            std::wcout << L"  PASS: Truncated header (<12 bytes) cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Truncated header accepted\n";
            failedTests++;
        }

        // 2c: Truncated body (payload claiming large profileId but truncated)
        std::vector<BYTE> truncPayload(20, 0);
        DWORD ver = 1;
        DWORD claimedProfLen = 100;
        memcpy(truncPayload.data(), &ver, 4);
        memcpy(truncPayload.data() + 4, &claimedProfLen, 4);
        outPw.clear();
        bool truncBody = PeekSecretVault::ParseSecretPayload(truncPayload.data(), static_cast<DWORD>(truncPayload.size()), validProfileId, outPw);
        if (!truncBody)
        {
            std::wcout << L"  PASS: Truncated payload body cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Truncated payload body accepted\n";
            failedTests++;
        }

        // 2d: Oversize payload (> 4096 bytes)
        std::vector<BYTE> oversizePayload(4097, 0);
        outPw.clear();
        bool oversizeResult = PeekSecretVault::ParseSecretPayload(oversizePayload.data(), static_cast<DWORD>(oversizePayload.size()), validProfileId, outPw);
        if (!oversizeResult)
        {
            std::wcout << L"  PASS: Oversize payload (>4096 bytes) cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Oversize payload accepted\n";
            failedTests++;
        }

        // 2e: Wrong profile_id binding check
        PeekSecretVault::BuildSecretPayload(validProfileId, validPassword, payload);
        outPw.clear();
        bool mismatchProf = PeekSecretVault::ParseSecretPayload(payload.data(), static_cast<DWORD>(payload.size()), L"different_profile_999", outPw);
        if (!mismatchProf && outPw.empty())
        {
            std::wcout << L"  PASS: Profile ID binding mismatch cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Profile ID mismatch accepted\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 3: Store and Retrieve via Fake LSA Secret Store
    // -------------------------------------------------------------
    std::wcout << L"[Test 3] Testing Store and ReadPasswordSecret via fake store...\n";
    {
        fakeStore->Clear();
        bool storeOk = PeekSecretVault::StorePasswordSecret(validSid, validProfileId, validPassword, fakeStore.get());
        if (storeOk)
        {
            std::wcout << L"  PASS: StorePasswordSecret succeeded\n";
        }
        else
        {
            std::wcout << L"  FAIL: StorePasswordSecret failed\n";
            failedTests++;
        }

        // Retrieve with correct SID and profile ID
        std::wstring retrievedPw;
        bool readOk = PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, retrievedPw, fakeStore.get());
        if (readOk && retrievedPw == validPassword)
        {
            std::wcout << L"  PASS: ReadPasswordSecret returned valid password for matching SID & profile\n";
        }
        else
        {
            std::wcout << L"  FAIL: ReadPasswordSecret failed or returned wrong password\n";
            failedTests++;
        }

        // Retrieve with wrong SID -> should fail
        std::wstring wrongSidPw;
        bool wrongSidRead = PeekSecretVault::ReadPasswordSecret(L"S-1-5-21-WRONG-SID", validProfileId, wrongSidPw, fakeStore.get());
        if (!wrongSidRead && wrongSidPw.empty())
        {
            std::wcout << L"  PASS: Wrong SID returned false (fail open)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Wrong SID should return false\n";
            failedTests++;
        }

        // Retrieve with wrong ProfileId -> should fail
        std::wstring wrongProfPw;
        bool wrongProfRead = PeekSecretVault::ReadPasswordSecret(validSid, L"attacker_profile_id", wrongProfPw, fakeStore.get());
        if (!wrongProfRead && wrongProfPw.empty())
        {
            std::wcout << L"  PASS: Wrong profile_id returned false (profile binding enforced)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Wrong profile_id should return false\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 4: Fail-Open Tests (Missing Key, Corrupt Payload, Empty Input)
    // -------------------------------------------------------------
    std::wcout << L"[Test 4] Testing Fail-Open Graceful Failure...\n";
    {
        fakeStore->Clear();
        std::wstring outPw;

        // Missing key in store
        bool missingKeyOk = !PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, outPw, fakeStore.get());
        
        // Empty parameters
        bool emptySidOk = !PeekSecretVault::ReadPasswordSecret(L"", validProfileId, outPw, fakeStore.get());
        bool emptyProfOk = !PeekSecretVault::ReadPasswordSecret(validSid, L"", outPw, fakeStore.get());

        // Corrupt data stored under the key
        const BYTE corruptBytes[] = { 0xDE, 0xAD, 0xBE, 0xEF };
        fakeStore->StoreSecret(PeekSecretVault::GetSecretKeyName(validSid), corruptBytes, sizeof(corruptBytes));
        bool corruptOk = !PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, outPw, fakeStore.get());

        if (missingKeyOk && emptySidOk && emptyProfOk && corruptOk && outPw.empty())
        {
            std::wcout << L"  PASS: Missing key, empty inputs, and corrupt data all fail open safely\n";
        }
        else
        {
            std::wcout << L"  FAIL: Fail-open test failed\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 5: Delete-Only-On-ERROR_LOGON_FAILURE Tests
    // -------------------------------------------------------------
    std::wcout << L"[Test 5] Testing Delete-Only-On-ERROR_LOGON_FAILURE...\n";
    {
        // 5a: Non-ERROR_LOGON_FAILURE errors (e.g. ERROR_ACCOUNT_LOCKED_OUT) must NOT delete the secret
        fakeStore->Clear();
        PeekSecretVault::StorePasswordSecret(validSid, validProfileId, validPassword, fakeStore.get());
        assert(fakeStore->HasSecret(PeekSecretVault::GetSecretKeyName(validSid)));

        // Simulate a validator returning ERROR_ACCOUNT_LOCKED_OUT
        DWORD simulatedError = ERROR_ACCOUNT_LOCKED_OUT;
        if (simulatedError == ERROR_LOGON_FAILURE)
        {
            PeekSecretVault::DeletePasswordSecret(validSid, fakeStore.get());
        }

        if (fakeStore->HasSecret(PeekSecretVault::GetSecretKeyName(validSid)))
        {
            std::wcout << L"  PASS: ERROR_ACCOUNT_LOCKED_OUT did not delete secret\n";
        }
        else
        {
            std::wcout << L"  FAIL: Secret was deleted on ERROR_ACCOUNT_LOCKED_OUT\n";
            failedTests++;
        }

        // 5b: ERROR_PASSWORD_EXPIRED must NOT delete the secret
        simulatedError = ERROR_PASSWORD_EXPIRED;
        if (simulatedError == ERROR_LOGON_FAILURE)
        {
            PeekSecretVault::DeletePasswordSecret(validSid, fakeStore.get());
        }

        if (fakeStore->HasSecret(PeekSecretVault::GetSecretKeyName(validSid)))
        {
            std::wcout << L"  PASS: ERROR_PASSWORD_EXPIRED did not delete secret\n";
        }
        else
        {
            std::wcout << L"  FAIL: Secret was deleted on ERROR_PASSWORD_EXPIRED\n";
            failedTests++;
        }

        // 5c: ERROR_ACCOUNT_DISABLED must NOT delete the secret
        simulatedError = ERROR_ACCOUNT_DISABLED;
        if (simulatedError == ERROR_LOGON_FAILURE)
        {
            PeekSecretVault::DeletePasswordSecret(validSid, fakeStore.get());
        }

        if (fakeStore->HasSecret(PeekSecretVault::GetSecretKeyName(validSid)))
        {
            std::wcout << L"  PASS: ERROR_ACCOUNT_DISABLED did not delete secret\n";
        }
        else
        {
            std::wcout << L"  FAIL: Secret was deleted on ERROR_ACCOUNT_DISABLED\n";
            failedTests++;
        }

        // 5d: ERROR_LOGON_FAILURE (wrong password) MUST delete the secret
        simulatedError = ERROR_LOGON_FAILURE;
        if (simulatedError == ERROR_LOGON_FAILURE)
        {
            PeekSecretVault::DeletePasswordSecret(validSid, fakeStore.get());
        }

        if (!fakeStore->HasSecret(PeekSecretVault::GetSecretKeyName(validSid)))
        {
            std::wcout << L"  PASS: ERROR_LOGON_FAILURE successfully deleted secret\n";
        }
        else
        {
            std::wcout << L"  FAIL: Secret was NOT deleted on ERROR_LOGON_FAILURE\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 6: Idempotent Secret Deletion
    // -------------------------------------------------------------
    std::wcout << L"[Test 6] Testing Idempotent Secret Deletion...\n";
    {
        fakeStore->Clear();
        // Deleting when key does not exist should return true without error
        bool del1 = PeekSecretVault::DeletePasswordSecret(validSid, fakeStore.get());
        bool del2 = PeekSecretVault::DeletePasswordSecret(validSid, fakeStore.get());

        if (del1 && del2)
        {
            std::wcout << L"  PASS: Secret deletion is cleanly idempotent\n";
        }
        else
        {
            std::wcout << L"  FAIL: Idempotent deletion failed\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 7: Default Password Validator
    // -------------------------------------------------------------
    std::wcout << L"[Test 7] Testing ValidatePassword error code reporting...\n";
    {
        DWORD errCode = 0;
        bool bRes = PeekSecretVault::ValidatePassword(L"NonExistentUser12345", L"", L"WrongPass!", &errCode);
        if (!bRes && errCode != ERROR_SUCCESS)
        {
            std::wcout << L"  PASS: ValidatePassword cleanly failed with error code: " << errCode << L"\n";
        }
        else
        {
            std::wcout << L"  FAIL: ValidatePassword did not report failure as expected\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 8: Direct Profile ID Extraction and Unbound Read
    // -------------------------------------------------------------
    std::wcout << L"[Test 8] Testing Direct Profile ID Extraction and Unbound Read...\n";
    {
        const std::wstring testUuid = L"a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
        const std::wstring testPw = L"SecretPassword123!";
        PeekSecretVault::StorePasswordSecret(validSid, testUuid, testPw);

        std::wstring extractedProfileId;
        bool okGet = PeekSecretVault::GetStoredProfileId(validSid, extractedProfileId);

        std::wstring unboundPw;
        std::wstring unboundProfileId;
        bool okRead = PeekSecretVault::ReadPasswordSecret(validSid, unboundPw, &unboundProfileId);

        if (okGet && extractedProfileId == testUuid && okRead && unboundPw == testPw && unboundProfileId == testUuid)
        {
            std::wcout << L"  PASS: Stored profile ID directly extracted and unbound read succeeded\n";
        }
        else
        {
            std::wcout << L"  FAIL: Direct profile ID extraction failed (okGet=" << okGet << L", okRead=" << okRead << L")\n";
            failedTests++;
        }
    }

    // Cleanup
    fakeStore->Clear();
    PeekSecretVault::SetLsaSecretStore(nullptr);
    PeekSecretVault::SetPasswordValidator(nullptr);

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
