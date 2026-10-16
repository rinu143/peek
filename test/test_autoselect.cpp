// Peek Face Engine - Lock Screen Verification Test
#include "PeekProvider.h"
#include "PeekCredential.h"
#include "PeekPipeClient.h"
#include <windows.h>
#include <iostream>
#include <cassert>
#include <string>
#include <vector>
#include <atomic>
#include <chrono>
#include <thread>

HINSTANCE g_hinst = NULL;
void DllAddRef() {}
void DllRelease() {}

class MockCredentialProviderUser : public ICredentialProviderUser
{
public:
    MockCredentialProviderUser(const std::wstring& sid, const std::wstring& username, const std::wstring& domain)
        : m_ref(1), m_sid(sid), m_username(username), m_domain(domain) {}

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override
    {
        if (riid == IID_IUnknown || riid == __uuidof(ICredentialProviderUser))
        {
            *ppv = static_cast<ICredentialProviderUser*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&m_ref); }
    IFACEMETHODIMP_(ULONG) Release() override
    {
        ULONG r = InterlockedDecrement(&m_ref);
        if (r == 0) delete this;
        return r;
    }

    // ICredentialProviderUser
    IFACEMETHODIMP GetSid(LPWSTR* ppszSid) override
    {
        return CoTaskMemAllocString(m_sid.c_str(), ppszSid);
    }
    IFACEMETHODIMP GetProviderID(GUID* pProviderID) override
    {
        if (!pProviderID) return E_POINTER;
        *pProviderID = GUID_NULL;
        return S_OK;
    }
    IFACEMETHODIMP GetStringValue(REFPROPERTYKEY key, LPWSTR* ppsz) override
    {
        if (key == PKEY_Identity_UserName)
        {
            return CoTaskMemAllocString(m_username.c_str(), ppsz);
        }
        else if (key == PKEY_Identity_DomainName)
        {
            return CoTaskMemAllocString(m_domain.c_str(), ppsz);
        }
        else if (key == PKEY_Identity_QualifiedUserName)
        {
            std::wstring qual = m_domain + L"\\" + m_username;
            return CoTaskMemAllocString(qual.c_str(), ppsz);
        }
        return E_INVALIDARG;
    }
    IFACEMETHODIMP GetValue(REFPROPERTYKEY, PROPVARIANT*) override { return E_NOTIMPL; }

private:
    LONG m_ref;
    std::wstring m_sid;
    std::wstring m_username;
    std::wstring m_domain;
};

class MockCredentialProviderUserArray : public ICredentialProviderUserArray
{
public:
    MockCredentialProviderUserArray() : m_ref(1) {}
    ~MockCredentialProviderUserArray()
    {
        for (auto u : m_users) u->Release();
    }

    void AddUser(MockCredentialProviderUser* user)
    {
        user->AddRef();
        m_users.push_back(user);
    }

    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override
    {
        if (riid == IID_IUnknown || riid == __uuidof(ICredentialProviderUserArray))
        {
            *ppv = static_cast<ICredentialProviderUserArray*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&m_ref); }
    IFACEMETHODIMP_(ULONG) Release() override
    {
        ULONG r = InterlockedDecrement(&m_ref);
        if (r == 0) delete this;
        return r;
    }

    IFACEMETHODIMP SetProviderFilter(REFGUID) override { return S_OK; }
    IFACEMETHODIMP GetAccountOptions(CREDENTIAL_PROVIDER_ACCOUNT_OPTIONS*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetCount(DWORD* pdwCount) override
    {
        if (!pdwCount) return E_POINTER;
        *pdwCount = static_cast<DWORD>(m_users.size());
        return S_OK;
    }
    IFACEMETHODIMP GetAt(DWORD dwIndex, ICredentialProviderUser** ppUser) override
    {
        if (!ppUser) return E_POINTER;
        if (dwIndex >= m_users.size()) return E_INVALIDARG;
        *ppUser = m_users[dwIndex];
        (*ppUser)->AddRef();
        return S_OK;
    }

private:
    LONG m_ref;
    std::vector<ICredentialProviderUser*> m_users;
};

static std::wstring CreateTempTestDirectory()
{
    wchar_t tempPath[MAX_PATH];
    GetTempPathW(MAX_PATH, tempPath);
    std::wstring dir = tempPath;
    dir += L"PeekAutoSelectTest_";
    dir += std::to_wstring(GetTickCount64());
    CreateDirectoryW(dir.c_str(), NULL);
    return dir;
}

static void DeleteTempTestDirectory(const std::wstring& dir)
{
    std::wstring search = dir + L"\\*.*";
    WIN32_FIND_DATAW fd;
    HANDLE hFind = FindFirstFileW(search.c_str(), &fd);
    if (hFind != INVALID_HANDLE_VALUE)
    {
        do
        {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
            {
                std::wstring file = dir + L"\\" + fd.cFileName;
                DeleteFileW(file.c_str());
            }
        } while (FindNextFileW(hFind, &fd));
        FindClose(hFind);
    }
    RemoveDirectoryW(dir.c_str());
}

int main()
{
    std::wcout << L"==========================================================\n";
    std::wcout << L"Running Peek Lock-Screen & User-Array Verification Tests\n";
    std::wcout << L"==========================================================\n";

    int failed = 0;
    std::wstring tempDir = CreateTempTestDirectory();
    SetEnvironmentVariableW(L"PEEK_PROFILES_DIR", tempDir.c_str());

    std::wstring testPipeName = L"\\\\.\\pipe\\PeekTestPipe_" + std::to_wstring(GetCurrentProcessId());
    SetEnvironmentVariableW(L"PEEK_PIPE_NAME_OVERRIDE", testPipeName.c_str());

    // -------------------------------------------------------------
    // Test 1: Usage scenario handling (CPUS_LOGON & CPUS_CREDUI -> E_NOTIMPL)
    // -------------------------------------------------------------
    std::wcout << L"[Test 1] Testing SetUsageScenario restrictions...\n";
    {
        PeekProvider provider;
        HRESULT hrLogon = provider.SetUsageScenario(CPUS_LOGON, 0);
        HRESULT hrCredUi = provider.SetUsageScenario(CPUS_CREDUI, 0);
        HRESULT hrChangePw = provider.SetUsageScenario(CPUS_CHANGE_PASSWORD, 0);
        HRESULT hrUnlock = provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);

        if (hrLogon == E_NOTIMPL && hrCredUi == E_NOTIMPL && hrChangePw == E_NOTIMPL && hrUnlock == S_OK)
        {
            std::wcout << L"  PASS: CPUS_LOGON/CREDUI/CHANGE_PASSWORD return E_NOTIMPL, UNLOCK returns S_OK\n";
        }
        else
        {
            std::wcout << L"  FAIL: Unexpected HRESULT in SetUsageScenario\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 2: Fail-soft when user array is missing (null)
    // -------------------------------------------------------------
    std::wcout << L"[Test 2] Testing UNLOCK with missing (null) user array (fail soft)...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);

        DWORD count = 999;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
        if (SUCCEEDED(hr) && count == 0 && defaultIndex == CREDENTIAL_PROVIDER_NO_DEFAULT && autoLogon == FALSE)
        {
            std::wcout << L"  PASS: Fail-soft with count=0 when user array is missing\n";
        }
        else
        {
            std::wcout << L"  FAIL: Expected count=0. Got count=" << count 
                       << L", defaultIndex=" << defaultIndex << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 3: Fail-soft when user array is empty (count = 0)
    // -------------------------------------------------------------
    std::wcout << L"[Test 3] Testing UNLOCK with empty user array (count = 0)...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);

        MockCredentialProviderUserArray* pEmptyArray = new MockCredentialProviderUserArray();
        provider.SetUserArray(pEmptyArray);

        DWORD count = 999;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
        if (SUCCEEDED(hr) && count == 0 && defaultIndex == CREDENTIAL_PROVIDER_NO_DEFAULT && autoLogon == FALSE)
        {
            std::wcout << L"  PASS: Fail-soft with count=0 when user array is empty\n";
        }
        else
        {
            std::wcout << L"  FAIL: Expected count=0. Got count=" << count 
                       << L", defaultIndex=" << defaultIndex << L"\n";
            failed++;
        }

        pEmptyArray->Release();
    }

    // -------------------------------------------------------------
    // Test 4: UNLOCK with valid user array (locked user at index 0)
    // -------------------------------------------------------------
    std::wcout << L"[Test 4] Testing UNLOCK with valid user array populating locked user SID, user, domain...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);

        MockCredentialProviderUserArray* pArray = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser = new MockCredentialProviderUser(
            L"S-1-5-21-12345678-1001",
            L"TestUser",
            L"TestDomain"
        );
        pArray->AddUser(pUser);
        pUser->Release();

        provider.SetUserArray(pArray);

        DWORD count = 0;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
        if (SUCCEEDED(hr) && count == 1 && defaultIndex == CREDENTIAL_PROVIDER_NO_DEFAULT && autoLogon == FALSE)
        {
            ICredentialProviderCredential* pCred = nullptr;
            HRESULT hrGet = provider.GetCredentialAt(0, &pCred);
            if (SUCCEEDED(hrGet) && pCred)
            {
                ICredentialProviderCredential2* pCred2 = nullptr;
                if (SUCCEEDED(pCred->QueryInterface(IID_ICredentialProviderCredential2, reinterpret_cast<void**>(&pCred2))) && pCred2)
                {
                    PWSTR pszSid = nullptr;
                    HRESULT hrSid = pCred2->GetUserSid(&pszSid);
                    if (SUCCEEDED(hrSid) && pszSid && std::wstring(pszSid) == L"S-1-5-21-12345678-1001")
                    {
                        std::wcout << L"  PASS: Locked user SID correctly propagated to credential: " << pszSid << L"\n";
                        CoTaskMemFree(pszSid);
                    }
                    else
                    {
                        std::wcout << L"  FAIL: GetUserSid did not return expected SID. hr=" << hrSid << L"\n";
                        failed++;
                    }
                    pCred2->Release();
                }
                else
                {
                    std::wcout << L"  FAIL: QueryInterface for ICredentialProviderCredential2 failed\n";
                    failed++;
                }
                pCred->Release();
            }
            else
            {
                std::wcout << L"  FAIL: GetCredentialAt(0) failed\n";
                failed++;
            }
        }
        else
        {
            std::wcout << L"  FAIL: Expected count=1, default=NO_DEFAULT. Got count=" << count 
                       << L", default=" << defaultIndex << L"\n";
            failed++;
        }

        pArray->Release();
    }

    // -------------------------------------------------------------
    // Test 5: Recreating credential when user array changes
    // -------------------------------------------------------------
    std::wcout << L"[Test 5] Testing credential recreation when user array changes...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);

        MockCredentialProviderUserArray* pArray1 = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser1 = new MockCredentialProviderUser(L"S-1-5-21-AAA", L"UserA", L"DomainA");
        pArray1->AddUser(pUser1);
        pUser1->Release();
        provider.SetUserArray(pArray1);

        DWORD count = 0;
        DWORD defaultIndex = 0;
        BOOL autoLogon = FALSE;
        provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);

        // Switch to a new user array
        MockCredentialProviderUserArray* pArray2 = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser2 = new MockCredentialProviderUser(L"S-1-5-21-BBB", L"UserB", L"DomainB");
        pArray2->AddUser(pUser2);
        pUser2->Release();
        provider.SetUserArray(pArray2);

        provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);

        ICredentialProviderCredential* pCred = nullptr;
        provider.GetCredentialAt(0, &pCred);
        ICredentialProviderCredential2* pCred2 = nullptr;
        pCred->QueryInterface(IID_ICredentialProviderCredential2, reinterpret_cast<void**>(&pCred2));

        PWSTR pszSid = nullptr;
        pCred2->GetUserSid(&pszSid);
        bool updated = (pszSid && std::wstring(pszSid) == L"S-1-5-21-BBB");
        if (pszSid) CoTaskMemFree(pszSid);
        pCred2->Release();
        pCred->Release();

        if (updated)
        {
            std::wcout << L"  PASS: Credential successfully recreated with new user context (S-1-5-21-BBB)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Credential was not recreated with new user SID\n";
            failed++;
        }

        pArray1->Release();
        pArray2->Release();
    }

    // -------------------------------------------------------------
    // Test 6: Fast fail-open when engine is stopped (NO 2.5s HANG verification)
    // -------------------------------------------------------------
    std::wcout << L"[Test 6] Testing SetSelected fail-open with engine offline (no hang verification)...\n";
    {
        PeekCredential credential;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO, CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo") },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User") },
            { PFI_STATUS_TEXT, CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status") },
        };
        credential.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT);

        auto start = std::chrono::steady_clock::now();
        BOOL autoLogon = TRUE;
        credential.SetSelected(&autoLogon);

        // Wait for worker thread to complete connect attempt
        Sleep(250);

        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();

        PWSTR pszStatus = nullptr;
        credential.GetStringValue(PFI_STATUS_TEXT, &pszStatus);
        std::wstring statusStr = pszStatus ? pszStatus : L"";
        if (pszStatus) CoTaskMemFree(pszStatus);

        bool statusMatched = (statusStr == L"Peek engine offline. Sign in with password or PIN.");
        bool noHang = (elapsed < 1000); // Should complete in under 500ms, definitely not 2500ms

        if (statusMatched && noHang && autoLogon == FALSE)
        {
            std::wcout << L"  PASS: Offline engine failed open in " << elapsed 
                       << L"ms (<1000ms, no hang) with expected fallback status text\n";
        }
        else
        {
            std::wcout << L"  FAIL: statusMatched=" << statusMatched << L", noHang=" << noHang 
                       << L" (" << elapsed << L"ms), autoLogon=" << autoLogon << L"\n";
            std::wcout << L"        Status text: " << statusStr << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 7: Dual-gate security invariant verification
    // -------------------------------------------------------------
    std::wcout << L"[Test 7] Testing security invariant: GetSerialization when not authenticated...\n";
    {
        PeekCredential credential;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO, CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo") },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User") },
            { PFI_STATUS_TEXT, CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status") },
        };
        credential.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT);

        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE resp = CPGSR_RETURN_CREDENTIAL_FINISHED;
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cpcs = {};
        HRESULT hr = credential.GetSerialization(&resp, &cpcs, nullptr, nullptr);

        if (SUCCEEDED(hr) && resp == CPGSR_NO_CREDENTIAL_FINISHED && !credential.IsAuthenticated())
        {
            std::wcout << L"  PASS: GetSerialization returns CPGSR_NO_CREDENTIAL_FINISHED; IsAuthenticated is false\n";
        }
        else
        {
            std::wcout << L"  FAIL: Invariant violation in GetSerialization\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 8: ICredentialProviderSetUserArray QueryInterface verification
    // -------------------------------------------------------------
    std::wcout << L"[Test 8] Testing ICredentialProviderSetUserArray QueryInterface...\n";
    {
        PeekProvider provider;
        ICredentialProviderSetUserArray* pSetUserArray = nullptr;
        HRESULT hr = provider.QueryInterface(IID_ICredentialProviderSetUserArray, reinterpret_cast<void**>(&pSetUserArray));
        if (SUCCEEDED(hr) && pSetUserArray)
        {
            std::wcout << L"  PASS: PeekProvider successfully implements ICredentialProviderSetUserArray\n";
            pSetUserArray->Release();
        }
        else
        {
            std::wcout << L"  FAIL: QueryInterface for ICredentialProviderSetUserArray failed (hr=" << hr << L")\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 9: SetSerialization workstation unlock return value
    // -------------------------------------------------------------
    std::wcout << L"[Test 9] Testing SetSerialization returns S_OK for unlock scenario...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cpcs = {};
        HRESULT hr = provider.SetSerialization(&cpcs);
        if (hr == S_OK)
        {
            std::wcout << L"  PASS: SetSerialization returns S_OK (LogonUI will not drop provider on unlock)\n";
        }
        else
        {
            std::wcout << L"  FAIL: SetSerialization did not return S_OK (hr=" << hr << L")\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 10: PeekCredential GetUserSid implementation (S_OK vs S_FALSE)
    // -------------------------------------------------------------
    std::wcout << L"[Test 10] Testing PeekCredential::GetUserSid with and without SID...\n";
    {
        PeekCredential credWithSid;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO, CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo") },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User") },
            { PFI_STATUS_TEXT, CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status") },
        };
        credWithSid.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT, L"TestUser", L"TestDomain", L"S-1-5-21-12345678-1001");

        PWSTR pszSid = nullptr;
        HRESULT hrWithSid = credWithSid.GetUserSid(&pszSid);
        bool match = (pszSid && std::wstring(pszSid) == L"S-1-5-21-12345678-1001");
        if (pszSid) CoTaskMemFree(pszSid);

        PeekCredential credNoSid;
        credNoSid.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT);
        PWSTR pszEmptySid = reinterpret_cast<PWSTR>(0x12345678); // Poison pointer to verify nullptr assignment
        HRESULT hrNoSid = credNoSid.GetUserSid(&pszEmptySid);

        if (SUCCEEDED(hrWithSid) && match && hrNoSid == S_FALSE && pszEmptySid == nullptr)
        {
            std::wcout << L"  PASS: GetUserSid returns initialized SID with S_OK, and S_FALSE with nullptr when empty\n";
        }
        else
        {
            std::wcout << L"  FAIL: GetUserSid behavior incorrect: hrWithSid=" << hrWithSid 
                       << L", match=" << match << L", hrNoSid=" << hrNoSid 
                       << L", pszEmptySid=" << pszEmptySid << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 11: Tile field states for deselected tile visibility
    // -------------------------------------------------------------
    std::wcout << L"[Test 11] Testing GetFieldState for deselected tile visibility (CPFS_DISPLAY_IN_BOTH)...\n";
    {
        PeekCredential credential;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO, CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo") },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User") },
            { PFI_STATUS_TEXT, CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status") },
        };
        credential.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT);

        CREDENTIAL_PROVIDER_FIELD_STATE stateLogo = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_STATE stateName = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_STATE stateStatus = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE istate;

        credential.GetFieldState(PFI_LOGO, &stateLogo, &istate);
        credential.GetFieldState(PFI_DISPLAY_NAME, &stateName, &istate);
        credential.GetFieldState(PFI_STATUS_TEXT, &stateStatus, &istate);

        if (stateLogo == CPFS_DISPLAY_IN_BOTH &&
            stateName == CPFS_DISPLAY_IN_BOTH &&
            stateStatus == CPFS_DISPLAY_IN_SELECTED_TILE)
        {
            std::wcout << L"  PASS: Logo and DisplayName visible in deselected state (CPFS_DISPLAY_IN_BOTH)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Unexpected field states: Logo=" << stateLogo 
                       << L", Name=" << stateName << L", Status=" << stateStatus << L"\n";
            failed++;
        }
    }

    // Cleanup
    DeleteTempTestDirectory(tempDir);
    SetEnvironmentVariableW(L"PEEK_PROFILES_DIR", NULL);
    SetEnvironmentVariableW(L"PEEK_PIPE_NAME_OVERRIDE", NULL);

    std::wcout << L"==========================================================\n";
    if (failed == 0)
    {
        std::wcout << L"ALL LOCK-SCREEN & USER-ARRAY VERIFICATION TESTS PASSED!\n";
        return 0;
    }
    else
    {
        std::wcout << failed << L" TEST(S) FAILED!\n";
        return 1;
    }
}
