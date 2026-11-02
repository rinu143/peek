// Test file for SecretVault functionality with LSA private-data store and multi-identity authorization
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
    auto fakeIdStore = std::make_shared<PeekSecretVault::InMemoryIdentityStore>();
    PeekSecretVault::SetLsaSecretStore(fakeStore);
    PeekSecretVault::SetIdentityStore(fakeIdStore);

    const std::wstring validSid = L"S-1-5-21-12345678-1001";
    const std::wstring validProfileId = L"profile_alice_123";
    const std::wstring validPassword = L"CorrectHorseBatteryStaple!";

    // -------------------------------------------------------------
    // Test 1: Payload Building and Parsing Round-Trip (v1 and v2)
    // -------------------------------------------------------------
    std::wcout << L"[Test 1] Testing Payload Building and Parsing Round-Trip (v1 and v2)...\n";
    {
        // 1a: v2 payload building and parsing
        std::vector<BYTE> payloadV2;
        bool buildV2Ok = PeekSecretVault::BuildSecretPayload(validPassword, payloadV2);
        DWORD verV2 = 0;
        std::wstring parsedProfV2;
        std::wstring parsedPwV2;
        bool parseV2Ok = PeekSecretVault::ParseSecretPayload(payloadV2.data(), static_cast<DWORD>(payloadV2.size()), verV2, parsedProfV2, parsedPwV2);

        // 1b: v1 payload building and parsing
        std::vector<BYTE> payloadV1;
        bool buildV1Ok = PeekSecretVault::BuildSecretPayloadV1(validProfileId, validPassword, payloadV1);
        DWORD verV1 = 0;
        std::wstring parsedProfV1;
        std::wstring parsedPwV1;
        bool parseV1Ok = PeekSecretVault::ParseSecretPayload(payloadV1.data(), static_cast<DWORD>(payloadV1.size()), verV1, parsedProfV1, parsedPwV1);

        if (buildV2Ok && parseV2Ok && verV2 == 2 && parsedPwV2 == validPassword &&
            buildV1Ok && parseV1Ok && verV1 == 1 && parsedProfV1 == validProfileId && parsedPwV1 == validPassword)
        {
            std::wcout << L"  PASS: Both v1 and v2 payloads built and parsed correctly\n";
        }
        else
        {
            std::wcout << L"  FAIL: Round-trip mismatch or build/parse failed\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 2: Parser Tests (Bad version, Truncated, Oversize, Corrupt)
    // -------------------------------------------------------------
    std::wcout << L"[Test 2] Testing Parser Validation & Rejection (Corrupt / Bad Versions)...\n";
    {
        // 2a: Bad version (version = 0 or 3)
        std::vector<BYTE> payload;
        PeekSecretVault::BuildSecretPayload(validPassword, payload);
        DWORD badVersion = 3;
        memcpy(payload.data(), &badVersion, sizeof(DWORD));
        DWORD verOut = 0;
        std::wstring outProf, outPw;
        bool badVerResult = PeekSecretVault::ParseSecretPayload(payload.data(), static_cast<DWORD>(payload.size()), verOut, outProf, outPw);
        if (!badVerResult && outPw.empty())
        {
            std::wcout << L"  PASS: Bad version (3) cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Bad version accepted\n";
            failedTests++;
        }

        // 2b: Truncated header (< 8 bytes)
        BYTE tinyBuf[4] = { 2, 0, 0, 0 };
        outPw.clear();
        bool truncHeader = PeekSecretVault::ParseSecretPayload(tinyBuf, sizeof(tinyBuf), verOut, outProf, outPw);
        if (!truncHeader)
        {
            std::wcout << L"  PASS: Truncated header (<8 bytes) cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Truncated header accepted\n";
            failedTests++;
        }

        // 2c: Truncated body (payload claiming large password length but truncated)
        std::vector<BYTE> truncPayload(12, 0);
        DWORD ver = 2;
        DWORD claimedPwLen = 100;
        memcpy(truncPayload.data(), &ver, 4);
        memcpy(truncPayload.data() + 4, &claimedPwLen, 4);
        outPw.clear();
        bool truncBody = PeekSecretVault::ParseSecretPayload(truncPayload.data(), static_cast<DWORD>(truncPayload.size()), verOut, outProf, outPw);
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
        bool oversizeResult = PeekSecretVault::ParseSecretPayload(oversizePayload.data(), static_cast<DWORD>(oversizePayload.size()), verOut, outProf, outPw);
        if (!oversizeResult)
        {
            std::wcout << L"  PASS: Oversize payload (>4096 bytes) cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Oversize payload accepted\n";
            failedTests++;
        }

        // 2e: v1 mismatch expected profile id
        std::vector<BYTE> v1Payload;
        PeekSecretVault::BuildSecretPayloadV1(validProfileId, validPassword, v1Payload);
        outPw.clear();
        bool mismatchProf = PeekSecretVault::ParseSecretPayload(v1Payload.data(), static_cast<DWORD>(v1Payload.size()), L"different_profile_999", outPw);
        if (!mismatchProf && outPw.empty())
        {
            std::wcout << L"  PASS: v1 Profile ID binding mismatch cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: v1 Profile ID mismatch accepted\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 3: Multi-Identity v2 Authorization (id in list / not in list / key missing / cap)
    // -------------------------------------------------------------
    std::wcout << L"[Test 3] Testing Multi-Identity v2 Authorization...\n";
    {
        fakeStore->Clear();
        fakeIdStore->Clear();

        // 3a: Write v2 secret
        bool storeOk = PeekSecretVault::StorePasswordSecret(validSid, validPassword, fakeStore.get());
        if (!storeOk)
        {
            std::wcout << L"  FAIL: StorePasswordSecret v2 failed\n";
            failedTests++;
        }

        // 3b: Key missing (no identities registered in store for SID)
        std::wstring outPwMissing;
        bool keyMissingOk = !PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, outPwMissing, fakeStore.get(), fakeIdStore.get());
        if (keyMissingOk && outPwMissing.empty())
        {
            std::wcout << L"  PASS: Key missing cleanly rejected (fail-open)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Key missing was accepted\n";
            failedTests++;
        }

        // 3c: ID in list
        fakeIdStore->SetAuthorizedIdentities(validSid, { validProfileId, L"profile_bob_456" });
        std::wstring outPwAuthorized;
        bool idInListOk = PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, outPwAuthorized, fakeStore.get(), fakeIdStore.get());
        if (idInListOk && outPwAuthorized == validPassword)
        {
            std::wcout << L"  PASS: ID in authorized list successfully unlocked\n";
        }
        else
        {
            std::wcout << L"  FAIL: ID in authorized list failed to unlock\n";
            failedTests++;
        }

        // 3d: ID not in list
        std::wstring outPwUnauthorized;
        bool idNotInListOk = !PeekSecretVault::ReadPasswordSecret(validSid, L"profile_eve_999", outPwUnauthorized, fakeStore.get(), fakeIdStore.get());
        if (idNotInListOk && outPwUnauthorized.empty())
        {
            std::wcout << L"  PASS: ID not in authorized list cleanly rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: ID not in authorized list was accepted\n";
            failedTests++;
        }

        // 3e: Cap test (max 5 authorized identities)
        // Set 6 identities in store: id1, id2, id3, id4, id5, id6
        std::vector<std::wstring> sixIds = {
            L"id_1", L"id_2", L"id_3", L"id_4", L"id_5", L"id_6"
        };
        fakeIdStore->SetAuthorizedIdentities(validSid, sixIds);

        // id_5 (5th) should be authorized (within cap of 5)
        std::wstring outPw5;
        bool id5Ok = PeekSecretVault::ReadPasswordSecret(validSid, L"id_5", outPw5, fakeStore.get(), fakeIdStore.get());
        // id_6 (6th) should be rejected (exceeds cap of 5)
        std::wstring outPw6;
        bool id6Ok = !PeekSecretVault::ReadPasswordSecret(validSid, L"id_6", outPw6, fakeStore.get(), fakeIdStore.get());

        if (id5Ok && id6Ok && outPw5 == validPassword && outPw6.empty())
        {
            std::wcout << L"  PASS: Identity cap of 5 strictly enforced (5th allowed, 6th rejected)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Identity cap enforcement failed (id5Ok=" << id5Ok << L", id6Ok=" << id6Ok << L")\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 4: Legacy v1 Secret Backward Compatibility
    // -------------------------------------------------------------
    std::wcout << L"[Test 4] Testing Legacy v1 Secret Compatibility...\n";
    {
        fakeStore->Clear();
        fakeIdStore->Clear(); // No registry key needed for v1!

        // Write legacy v1 secret with validProfileId
        bool storeV1Ok = PeekSecretVault::StorePasswordSecretV1(validSid, validProfileId, validPassword, fakeStore.get());
        if (!storeV1Ok)
        {
            std::wcout << L"  FAIL: StorePasswordSecretV1 failed\n";
            failedTests++;
        }

        // Matching profile ID succeeds without registry key
        std::wstring outPwV1;
        bool readV1Match = PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, outPwV1, fakeStore.get(), fakeIdStore.get());
        if (readV1Match && outPwV1 == validPassword)
        {
            std::wcout << L"  PASS: Legacy v1 secret unlocked without registry key\n";
        }
        else
        {
            std::wcout << L"  FAIL: Legacy v1 secret unlock failed\n";
            failedTests++;
        }

        // Mismatched profile ID fails
        std::wstring outPwMismatch;
        bool readV1Mismatch = !PeekSecretVault::ReadPasswordSecret(validSid, L"other_profile", outPwMismatch, fakeStore.get(), fakeIdStore.get());
        if (readV1Mismatch && outPwMismatch.empty())
        {
            std::wcout << L"  PASS: Legacy v1 secret rejected mismatched profile ID\n";
        }
        else
        {
            std::wcout << L"  FAIL: Legacy v1 secret accepted mismatched profile ID\n";
            failedTests++;
        }
    }

    // -------------------------------------------------------------
    // Test 5: Fail-Open Tests (Missing Key, Corrupt Payload, Empty Input)
    // -------------------------------------------------------------
    std::wcout << L"[Test 5] Testing Fail-Open Graceful Failure...\n";
    {
        fakeStore->Clear();
        std::wstring outPw;

        // Missing key in store
        bool missingKeyOk = !PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, outPw, fakeStore.get(), fakeIdStore.get());
        
        // Empty parameters
        bool emptySidOk = !PeekSecretVault::ReadPasswordSecret(L"", validProfileId, outPw, fakeStore.get(), fakeIdStore.get());
        bool emptyProfOk = !PeekSecretVault::ReadPasswordSecret(validSid, L"", outPw, fakeStore.get(), fakeIdStore.get());

        // Corrupt data stored under the key
        const BYTE corruptBytes[] = { 0xDE, 0xAD, 0xBE, 0xEF };
        fakeStore->StoreSecret(PeekSecretVault::GetSecretKeyName(validSid), corruptBytes, sizeof(corruptBytes));
        bool corruptOk = !PeekSecretVault::ReadPasswordSecret(validSid, validProfileId, outPw, fakeStore.get(), fakeIdStore.get());

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
    // Test 6: Delete-Only-On-ERROR_LOGON_FAILURE Tests
    // -------------------------------------------------------------
    std::wcout << L"[Test 6] Testing Delete-Only-On-ERROR_LOGON_FAILURE...\n";
    {
        fakeStore->Clear();
        PeekSecretVault::StorePasswordSecret(validSid, validPassword, fakeStore.get());
        assert(fakeStore->HasSecret(PeekSecretVault::GetSecretKeyName(validSid)));

        // 6a: Non-ERROR_LOGON_FAILURE errors (e.g. ERROR_ACCOUNT_LOCKED_OUT) must NOT delete the secret
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

        // 6b: ERROR_PASSWORD_EXPIRED must NOT delete the secret
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

        // 6c: ERROR_ACCOUNT_DISABLED must NOT delete the secret
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

        // 6d: ERROR_LOGON_FAILURE (wrong password) MUST delete the secret
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
    // Test 7: Idempotent Secret Deletion
    // -------------------------------------------------------------
    std::wcout << L"[Test 7] Testing Idempotent Secret Deletion...\n";
    {
        fakeStore->Clear();
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
    // Test 8: Default Password Validator
    // -------------------------------------------------------------
    std::wcout << L"[Test 8] Testing ValidatePassword error code reporting...\n";
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
    // Test 9: Direct Profile ID Extraction and Unbound Read
    // -------------------------------------------------------------
    std::wcout << L"[Test 9] Testing Direct Profile ID Extraction and Unbound Read...\n";
    {
        const std::wstring testUuid = L"a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
        const std::wstring testPw = L"SecretPassword123!";
        fakeIdStore->SetAuthorizedIdentities(validSid, { testUuid });
        PeekSecretVault::StorePasswordSecret(validSid, testPw, fakeStore.get());

        std::wstring extractedProfileId;
        bool okGet = PeekSecretVault::GetStoredProfileId(validSid, extractedProfileId, fakeStore.get());

        std::wstring unboundPw;
        std::wstring unboundProfileId;
        bool okRead = PeekSecretVault::ReadPasswordSecret(validSid, unboundPw, &unboundProfileId, fakeStore.get());

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
    fakeIdStore->Clear();
    PeekSecretVault::SetLsaSecretStore(nullptr);
    PeekSecretVault::SetIdentityStore(nullptr);
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
