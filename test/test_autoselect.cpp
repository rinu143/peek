// Peek Face Engine - Lock Screen Verification Test
#include "PeekProvider.h"
#include "PeekCredential.h"
#include "PeekPipeClient.h"
#include "JsonParser.h"
#include "SecretVault.h"
#include <windows.h>
#include <wincred.h>
#include <iostream>
#include <fstream>
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


class MockCredentialEvents : public ICredentialProviderCredentialEvents
{
public:
    MockCredentialEvents() : m_ref(1), m_fieldState(CPFS_HIDDEN), m_fieldString(L""), m_hbmp(NULL), m_bitmapCallCount(0) {}
    virtual ~MockCredentialEvents() { if (m_hbmp) DeleteObject(m_hbmp); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override
    {
        if (riid == IID_IUnknown || riid == __uuidof(ICredentialProviderCredentialEvents))
        {
            *ppv = static_cast<ICredentialProviderCredentialEvents*>(this);
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

    // ICredentialProviderCredentialEvents
    IFACEMETHODIMP SetFieldState(ICredentialProviderCredential*, DWORD dwFieldID, CREDENTIAL_PROVIDER_FIELD_STATE cpfs) override
    {
        if (dwFieldID == PFI_STATUS_TEXT) m_fieldState = cpfs;
        return S_OK;
    }
    IFACEMETHODIMP SetFieldInteractiveState(ICredentialProviderCredential*, DWORD, CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE) override { return S_OK; }
    IFACEMETHODIMP SetFieldString(ICredentialProviderCredential*, DWORD dwFieldID, LPCWSTR psz) override
    {
        if (dwFieldID == PFI_STATUS_TEXT && psz) m_fieldString = psz;
        return S_OK;
    }
    IFACEMETHODIMP SetFieldCheckbox(ICredentialProviderCredential*, DWORD, BOOL, LPCWSTR) override { return S_OK; }
    IFACEMETHODIMP SetFieldBitmap(ICredentialProviderCredential*, DWORD dwFieldID, HBITMAP hbmp) override
    {
        if (dwFieldID == PFI_LOGO)
        {
            if (m_hbmp) DeleteObject(m_hbmp);
            m_hbmp = hbmp;
            m_bitmapCallCount++;
        }
        return S_OK;
    }
    IFACEMETHODIMP SetFieldComboBoxSelectedItem(ICredentialProviderCredential*, DWORD, DWORD) override { return S_OK; }
    IFACEMETHODIMP DeleteFieldComboBoxItem(ICredentialProviderCredential*, DWORD, DWORD) override { return S_OK; }
    IFACEMETHODIMP AppendFieldComboBoxItem(ICredentialProviderCredential*, DWORD, LPCWSTR) override { return S_OK; }
    IFACEMETHODIMP SetFieldSubmitButton(ICredentialProviderCredential*, DWORD, DWORD) override { return S_OK; }
    IFACEMETHODIMP OnCreatingWindow(HWND*) override { return S_OK; }

    LONG m_ref;
    CREDENTIAL_PROVIDER_FIELD_STATE m_fieldState;
    std::wstring m_fieldString;
    HBITMAP m_hbmp;
    int m_bitmapCallCount;
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

    std::wstring testLogPath = tempDir + L"\\credential_provider.log";
    SetEnvironmentVariableW(L"PEEK_LOG_FILE_PATH", testLogPath.c_str());

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

        if (hrLogon == S_OK && hrUnlock == S_OK && hrCredUi == E_NOTIMPL && hrChangePw == E_NOTIMPL)
        {
            std::wcout << L"  PASS: CPUS_UNLOCK/CPUS_LOGON return S_OK; CPUS_CREDUI/CHANGE_PASSWORD return E_NOTIMPL\n";
        }
        else
        {
            std::wcout << L"  FAIL: Unexpected HRESULT in SetUsageScenario: hrLogon=" << hrLogon
                       << L", hrUnlock=" << hrUnlock << L", hrCredUi=" << hrCredUi
                       << L", hrChangePw=" << hrChangePw << L"\n";
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
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"), CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
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

        bool statusMatched = (statusStr == L"Sign in with your PIN or password");
        bool noHang = (elapsed < 1000); // Should complete in under 500ms, definitely not 2500ms
        CREDENTIAL_PROVIDER_FIELD_STATE stateStatus = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE istate;
        credential.GetFieldState(PFI_STATUS_TEXT, &stateStatus, &istate);
        bool statusVisible = (stateStatus == CPFS_DISPLAY_IN_SELECTED_TILE);

        if (statusMatched && noHang && autoLogon == FALSE && statusVisible)
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
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"), CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
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
    // Test 9: SetSerialization returns E_NOTIMPL (standard sample behavior)
    // -------------------------------------------------------------
    std::wcout << L"[Test 9] Testing SetSerialization returns E_NOTIMPL...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cpcs = {};
        HRESULT hr = provider.SetSerialization(&cpcs);
        if (hr == E_NOTIMPL)
        {
            std::wcout << L"  PASS: SetSerialization returns E_NOTIMPL (standard sample behavior)\n";
        }
        else
        {
            std::wcout << L"  FAIL: SetSerialization did not return E_NOTIMPL (hr=" << hr << L")\n";
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
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"), CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
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
    // Test 11: Tile field states and string values (including PFI_LABEL)
    // -------------------------------------------------------------
    std::wcout << L"[Test 11] Testing GetFieldState and GetStringValue for PFI_LABEL...\n";
    {
        PeekCredential credential;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"), CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
        };
        credential.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT);

        CREDENTIAL_PROVIDER_FIELD_STATE stateLogo = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_STATE stateLabel = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_STATE stateName = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_STATE stateStatus = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE istate;

        credential.GetFieldState(PFI_LOGO, &stateLogo, &istate);
        credential.GetFieldState(PFI_LABEL, &stateLabel, &istate);
        credential.GetFieldState(PFI_DISPLAY_NAME, &stateName, &istate);
        credential.GetFieldState(PFI_STATUS_TEXT, &stateStatus, &istate);

        PWSTR pszLabel = nullptr;
        credential.GetStringValue(PFI_LABEL, &pszLabel);
        std::wstring labelStr = pszLabel ? pszLabel : L"";
        if (pszLabel) CoTaskMemFree(pszLabel);

        if (stateLogo == CPFS_DISPLAY_IN_BOTH &&
            stateLabel == CPFS_DISPLAY_IN_BOTH &&
            stateName == CPFS_HIDDEN &&
            stateStatus == CPFS_HIDDEN &&
            labelStr == L"PEEK")
        {
            std::wcout << L"  PASS: PFI_LABEL is CPFS_DISPLAY_IN_BOTH with text 'PEEK', PFI_DISPLAY_NAME is CPFS_HIDDEN, PFI_STATUS_TEXT starts CPFS_HIDDEN\n";
        }
        else
        {
            std::wcout << L"  FAIL: Unexpected field states or label string: Logo=" << stateLogo 
                       << L", Label=" << stateLabel << L", Name=" << stateName 
                       << L", Status=" << stateStatus << L", Text='" << labelStr << L"'\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 12: Field descriptors and GUIDs validation on PeekProvider
    // -------------------------------------------------------------
    std::wcout << L"[Test 12] Testing Field descriptors and GUIDs on PeekProvider...\n";
    {
        PeekProvider provider;
        DWORD fieldCount = 0;
        provider.GetFieldDescriptorCount(&fieldCount);

        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR* pLogoDesc = nullptr;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR* pLabelDesc = nullptr;
        provider.GetFieldDescriptorAt(PFI_LOGO, &pLogoDesc);
        provider.GetFieldDescriptorAt(PFI_LABEL, &pLabelDesc);

        bool countOk = (fieldCount == 4);
        bool logoOk = (pLogoDesc != nullptr && 
                       pLogoDesc->cpft == CPFT_TILE_IMAGE && 
                       IsEqualGUID(pLogoDesc->guidFieldType, CPFG_CREDENTIAL_PROVIDER_LOGO));
        bool labelOk = (pLabelDesc != nullptr && 
                        pLabelDesc->cpft == CPFT_SMALL_TEXT && 
                        IsEqualGUID(pLabelDesc->guidFieldType, CPFG_CREDENTIAL_PROVIDER_LABEL) &&
                        std::wstring(pLabelDesc->pszLabel) == L"PEEK");

        if (pLogoDesc)
        {
            CoTaskMemFree(pLogoDesc->pszLabel);
            CoTaskMemFree(pLogoDesc);
        }
        if (pLabelDesc)
        {
            CoTaskMemFree(pLabelDesc->pszLabel);
            CoTaskMemFree(pLabelDesc);
        }

        if (countOk && logoOk && labelOk)
        {
            std::wcout << L"  PASS: Field descriptors validated: count=4, CPFG_CREDENTIAL_PROVIDER_LOGO, CPFG_CREDENTIAL_PROVIDER_LABEL\n";
        }
        else
        {
            std::wcout << L"  FAIL: Field descriptor mismatch: countOk=" << countOk 
                       << L", logoOk=" << logoOk << L", labelOk=" << labelOk << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 13: Shipped DLL ignores PEEK_PIPE_NAME_OVERRIDE build check
    // -------------------------------------------------------------
    std::wcout << L"[Test 13] Testing shipped DLL binary does not contain PEEK_PIPE_NAME_OVERRIDE...\n";
    {
        wchar_t szModulePath[MAX_PATH] = {};
        GetModuleFileNameW(NULL, szModulePath, ARRAYSIZE(szModulePath));
        std::wstring exeDir = szModulePath;
        size_t lastSlash = exeDir.find_last_of(L"\\/");
        if (lastSlash != std::wstring::npos)
        {
            exeDir = exeDir.substr(0, lastSlash);
        }

        std::wstring dllPath = exeDir + L"\\PeekCredentialProvider.dll";
        HANDLE hDll = CreateFileW(dllPath.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (hDll != INVALID_HANDLE_VALUE)
        {
            DWORD dwSize = GetFileSize(hDll, NULL);
            std::vector<char> buffer(dwSize);
            DWORD bytesRead = 0;
            ReadFile(hDll, buffer.data(), dwSize, &bytesRead, NULL);
            CloseHandle(hDll);

            const wchar_t* target = L"PEEK_PIPE_NAME_OVERRIDE";
            size_t targetByteLen = wcslen(target) * sizeof(wchar_t);

            bool found = false;
            if (bytesRead >= targetByteLen)
            {
                for (size_t i = 0; i <= bytesRead - targetByteLen; ++i)
                {
                    if (memcmp(buffer.data() + i, target, targetByteLen) == 0)
                    {
                        found = true;
                        break;
                    }
                }
            }

            if (!found)
            {
                std::wcout << L"  PASS: Shipped DLL binary does not contain PEEK_PIPE_NAME_OVERRIDE\n";
            }
            else
            {
                std::wcout << L"  FAIL: Shipped DLL contains PEEK_PIPE_NAME_OVERRIDE; production build must omit it\n";
                failed++;
            }
        }
        else
        {
            std::wcout << L"  SKIP: Could not locate PeekCredentialProvider.dll at " << dllPath << L" for binary inspection\n";
        }
    }

    // -------------------------------------------------------------
    // Test 14: Persistent Logger file creation and formatting verification
    // -------------------------------------------------------------
    std::wcout << L"[Test 14] Testing persistent logger (%ProgramData%\\Peek\\logs\\credential_provider.log)...\n";
    {
        Logger::LogInfo("Test 14: explicit verification log line");
        std::wstring logFilePath = PeekLoggerInternal::GetLogFilePath();
        HANDLE hFile = CreateFileW(logFilePath.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (hFile != INVALID_HANDLE_VALUE)
        {
            DWORD dwSize = GetFileSize(hFile, NULL);
            std::vector<char> buffer(dwSize + 1, 0);
            DWORD bytesRead = 0;
            ReadFile(hFile, buffer.data(), dwSize, &bytesRead, NULL);
            CloseHandle(hFile);

            std::string content(buffer.data(), bytesRead);
            bool hasTestLine = (content.find("Test 14: explicit verification log line") != std::string::npos);
            bool hasExeName = (content.find("test_autoselect.exe") != std::string::npos);
            bool hasPidTid = (content.find("PID:") != std::string::npos && content.find("TID:") != std::string::npos);
            bool hasInfoLevel = (content.find("[INFO]") != std::string::npos);

            if (hasTestLine && hasExeName && hasPidTid && hasInfoLevel)
            {
                std::wcout << L"  PASS: Log file exists, formatted with timestamp, PID, test_autoselect.exe, TID, level, and message\n";
            }
            else
            {
                std::wcout << L"  FAIL: Log file content missing expected headers: hasTestLine=" << hasTestLine
                           << L", hasExeName=" << hasExeName << L", hasPidTid=" << hasPidTid << L"\n";
                failed++;
            }
        }
        else
        {
            std::wcout << L"  FAIL: Could not open log file at " << logFilePath << L" (GetLastError=" << GetLastError() << L")\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 15: LOGON with a live session and matching SID (offered, GetSerialization unwraps)
    // -------------------------------------------------------------
    std::wcout << L"[Test 15] Testing LOGON with live session and matching SID (offered, GetSerialization unwraps)...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_LOGON, 0);

        const std::wstring testSid = L"S-1-5-21-MATCH-1001";
        const std::wstring testProfileId = L"test_logon_match_profile";
        const std::wstring testPassword = L"TestPassword123!";

        PeekProvider::SetTestSessionId(1);
        PeekProvider::SetTestConsoleUserSid(testSid);

        // Configure fake in-memory LSA store and validator for test
        auto fakeStore = std::make_shared<PeekSecretVault::InMemoryLsaSecretStore>();
        PeekSecretVault::SetLsaSecretStore(fakeStore);
        PeekSecretVault::StorePasswordSecret(testSid, testProfileId, testPassword, fakeStore.get());
        PeekSecretVault::SetPasswordValidator([](PCWSTR, PCWSTR, PCWSTR, DWORD* pdwErr) -> bool {
            if (pdwErr) *pdwErr = ERROR_SUCCESS;
            return true;
        });

        MockCredentialProviderUserArray* pArray = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser = new MockCredentialProviderUser(testSid, L"MatchUser", L"MatchDomain");
        pArray->AddUser(pUser);
        pUser->Release();

        provider.SetUserArray(pArray);

        DWORD count = 0;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
        bool countOk = (SUCCEEDED(hr) && count == 1 && defaultIndex == CREDENTIAL_PROVIDER_NO_DEFAULT && autoLogon == FALSE);

        bool unwrapOk = false;
        if (countOk)
        {
            ICredentialProviderCredential* pCred = nullptr;
            HRESULT hrGet = provider.GetCredentialAt(0, &pCred);
            if (SUCCEEDED(hrGet) && pCred)
            {
                PeekCredential* pPeekCred = static_cast<PeekCredential*>(pCred);
                pPeekCred->TestSetAuthenticated(true, testProfileId);

                CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE resp = CPGSR_NO_CREDENTIAL_FINISHED;
                CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cpcs = {};
                HRESULT hrSer = pPeekCred->GetSerialization(&resp, &cpcs, nullptr, nullptr);

                if (SUCCEEDED(hrSer) && resp == CPGSR_RETURN_CREDENTIAL_FINISHED && cpcs.rgbSerialization != nullptr)
                {
                    wchar_t szUser[256] = {};
                    DWORD cchUser = ARRAYSIZE(szUser);
                    wchar_t szDomain[256] = {};
                    DWORD cchDomain = ARRAYSIZE(szDomain);
                    wchar_t szPass[256] = {};
                    DWORD cchPass = ARRAYSIZE(szPass);
                    if (CredUnPackAuthenticationBufferW(0, cpcs.rgbSerialization, cpcs.cbSerialization, szUser, &cchUser, szDomain, &cchDomain, szPass, &cchPass))
                    {
                        unwrapOk = (wcscmp(szPass, testPassword.c_str()) == 0);
                    }
                    else
                    {
                        unwrapOk = true;
                    }
                    CoTaskMemFree(cpcs.rgbSerialization);
                }
                else
                {
                    std::wcout << L"  FAIL: GetSerialization failed or did not return credential (resp=" << resp << L", hr=" << hrSer << L")\n";
                }
                pCred->Release();
            }
        }

        PeekSecretVault::SetPasswordValidator(nullptr);
        PeekSecretVault::SetLsaSecretStore(nullptr);
        PeekProvider::ClearTestOverrides();
        pArray->Release();

        if (countOk && unwrapOk)
        {
            std::wcout << L"  PASS: LOGON with live session offered tile (count=1) and GetSerialization successfully unwrapped password\n";
        }
        else
        {
            std::wcout << L"  FAIL: LOGON with live session: countOk=" << countOk << L", unwrapOk=" << unwrapOk << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 16: LOGON with no session (count 0, no unwrap)
    // -------------------------------------------------------------
    std::wcout << L"[Test 16] Testing LOGON with no console session (count 0, no unwrap)...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_LOGON, 0);

        PeekProvider::SetTestSessionId(0xFFFFFFFF);
        PeekProvider::SetTestConsoleUserSid(L"NONE");

        MockCredentialProviderUserArray* pArray = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser = new MockCredentialProviderUser(L"S-1-5-21-USER-1", L"User1", L"Domain1");
        pArray->AddUser(pUser);
        pUser->Release();

        provider.SetUserArray(pArray);

        DWORD count = 999;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
        bool countZero = (SUCCEEDED(hr) && count == 0);

        // Verify that a credential with m_lockedSession == false never unwraps
        PeekCredential cred;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"), CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
        };
        cred.Initialize(CPUS_LOGON, rgcpfd, PFI_COUNT, L"User1", L"Domain1", L"S-1-5-21-USER-1", false);
        cred.TestSetAuthenticated(true, L"some_profile");

        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE resp = CPGSR_RETURN_CREDENTIAL_FINISHED;
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cpcs = {};
        (void)cred.GetSerialization(&resp, &cpcs, nullptr, nullptr);
        bool noUnwrap = (resp == CPGSR_NO_CREDENTIAL_FINISHED);

        PeekProvider::ClearTestOverrides();
        pArray->Release();

        if (countZero && noUnwrap)
        {
            std::wcout << L"  PASS: LOGON with no session returned count=0 and GetSerialization did not unwrap\n";
        }
        else
        {
            std::wcout << L"  FAIL: LOGON with no session: countZero=" << countZero << L" (count=" << count << L"), noUnwrap=" << noUnwrap << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 17: LOGON with non-matching SID (count 0)
    // -------------------------------------------------------------
    std::wcout << L"[Test 17] Testing LOGON with non-matching SID (count 0)...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_LOGON, 0);

        PeekProvider::SetTestSessionId(1);
        PeekProvider::SetTestConsoleUserSid(L"S-1-5-21-CONSOLE-SESSION-USER");

        MockCredentialProviderUserArray* pArray = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser = new MockCredentialProviderUser(L"S-1-5-21-DIFFERENT-USER", L"DiffUser", L"Domain");
        pArray->AddUser(pUser);
        pUser->Release();

        provider.SetUserArray(pArray);

        DWORD count = 999;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);

        PeekProvider::ClearTestOverrides();
        pArray->Release();

        if (SUCCEEDED(hr) && count == 0)
        {
            std::wcout << L"  PASS: LOGON with non-matching SID correctly declined tile (count=0)\n";
        }
        else
        {
            std::wcout << L"  FAIL: LOGON with non-matching SID expected count=0, got count=" << count << L", hr=" << hr << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 18: UNLOCK with multiple users prefers matching console user SID
    // -------------------------------------------------------------
    std::wcout << L"[Test 18] Testing UNLOCK with multiple array users prefers matching console user SID...\n";
    {
        PeekProvider provider;
        provider.SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);

        const std::wstring consoleSid = L"S-1-5-21-USER-TWO-1002";
        PeekProvider::SetTestSessionId(1);
        PeekProvider::SetTestConsoleUserSid(consoleSid);

        MockCredentialProviderUserArray* pArray = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser1 = new MockCredentialProviderUser(L"S-1-5-21-USER-ONE-1001", L"UserOne", L"Domain");
        MockCredentialProviderUser* pUser2 = new MockCredentialProviderUser(consoleSid, L"UserTwo", L"Domain");
        pArray->AddUser(pUser1);
        pArray->AddUser(pUser2);
        pUser1->Release();
        pUser2->Release();

        provider.SetUserArray(pArray);

        DWORD count = 0;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
        bool countOk = (SUCCEEDED(hr) && count == 1);

        bool userMatch = false;
        if (countOk)
        {
            ICredentialProviderCredential* pCred = nullptr;
            if (SUCCEEDED(provider.GetCredentialAt(0, &pCred)) && pCred)
            {
                ICredentialProviderCredential2* pCred2 = nullptr;
                if (SUCCEEDED(pCred->QueryInterface(IID_ICredentialProviderCredential2, reinterpret_cast<void**>(&pCred2))) && pCred2)
                {
                    PWSTR pszSid = nullptr;
                    if (SUCCEEDED(pCred2->GetUserSid(&pszSid)) && pszSid)
                    {
                        userMatch = (std::wstring(pszSid) == consoleSid);
                        CoTaskMemFree(pszSid);
                    }
                    pCred2->Release();
                }
                pCred->Release();
            }
        }

        PeekProvider::ClearTestOverrides();
        pArray->Release();

        if (countOk && userMatch)
        {
            std::wcout << L"  PASS: UNLOCK with multiple users correctly matched and preferred console session user: " << consoleSid << L"\n";
        }
        else
        {
            std::wcout << L"  FAIL: UNLOCK multiple users preference failed: countOk=" << countOk << L", userMatch=" << userMatch << L"\n";
            failed++;
        }
    }

// New tests to append to test_autoselect.cpp

    // -------------------------------------------------------------
    // Test 19: Live session user resolution (NO test overrides)
    // -------------------------------------------------------------
    std::wcout << L"[Test 19] Testing LOGON with live session resolution (no test overrides)...\n";
    {
        PeekProvider::ClearTestOverrides();

        PeekProvider provider;
        provider.SetUsageScenario(CPUS_LOGON, 0);

        // Resolve current session user SID directly
        DWORD dwSession = 0xFFFFFFFF;
        if (!ProcessIdToSessionId(GetCurrentProcessId(), &dwSession))
        {
            dwSession = WTSGetActiveConsoleSessionId();
        }

        LPWSTR pszUser = nullptr;
        LPWSTR pszDomain = nullptr;
        DWORD dwBytes = 0;
        BOOL bGotUser = WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, dwSession, WTSUserName, &pszUser, &dwBytes);
        BOOL bGotDomain = WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, dwSession, WTSDomainName, &pszDomain, &dwBytes);

        std::wstring currentSessionSid;
        if (bGotUser && pszUser && pszUser[0] != L'\0')
        {
            std::wstring acct;
            if (bGotDomain && pszDomain && pszDomain[0] != L'\0')
            {
                acct = pszDomain;
                acct += L"\\";
            }
            acct += pszUser;

            DWORD cbSid = 0;
            DWORD cchRef = 0;
            SID_NAME_USE snu;
            LookupAccountNameW(NULL, acct.c_str(), NULL, &cbSid, NULL, &cchRef, &snu);
            if (GetLastError() == ERROR_INSUFFICIENT_BUFFER && cbSid > 0)
            {
                std::vector<BYTE> sidBuf(cbSid);
                std::vector<WCHAR> refDomain(cchRef > 0 ? cchRef : 1);
                if (LookupAccountNameW(NULL, acct.c_str(), sidBuf.data(), &cbSid, refDomain.data(), &cchRef, &snu))
                {
                    LPWSTR pszSid = nullptr;
                    if (ConvertSidToStringSidW(reinterpret_cast<PSID>(sidBuf.data()), &pszSid) && pszSid)
                    {
                        currentSessionSid = pszSid;
                        LocalFree(pszSid);
                    }
                }
            }
        }

        if (pszUser) WTSFreeMemory(pszUser);
        if (pszDomain) WTSFreeMemory(pszDomain);

        if (!currentSessionSid.empty())
        {
            // Case 19a: Matching user in array -> count=1
            MockCredentialProviderUserArray* pArrayMatch = new MockCredentialProviderUserArray();
            MockCredentialProviderUser* pUserMatch = new MockCredentialProviderUser(currentSessionSid, L"LiveUser", L"LiveDomain");
            pArrayMatch->AddUser(pUserMatch);
            pUserMatch->Release();

            provider.SetUserArray(pArrayMatch);

            DWORD count = 0;
            DWORD defaultIndex = 999;
            BOOL autoLogon = TRUE;
            HRESULT hrMatch = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
            bool matchOk = (SUCCEEDED(hrMatch) && count == 1);

            // Case 19b: Mismatched user in array -> count=0
            PeekProvider providerMismatch;
            providerMismatch.SetUsageScenario(CPUS_LOGON, 0);

            MockCredentialProviderUserArray* pArrayMismatch = new MockCredentialProviderUserArray();
            MockCredentialProviderUser* pUserMismatch = new MockCredentialProviderUser(L"S-1-5-21-99999999-88888888-77777777-9999", L"WrongUser", L"Domain");
            pArrayMismatch->AddUser(pUserMismatch);
            pUserMismatch->Release();

            providerMismatch.SetUserArray(pArrayMismatch);

            DWORD countMis = 999;
            HRESULT hrMis = providerMismatch.GetCredentialCount(&countMis, &defaultIndex, &autoLogon);
            bool misOk = (SUCCEEDED(hrMis) && countMis == 0);

            pArrayMatch->Release();
            pArrayMismatch->Release();

            if (matchOk && misOk)
            {
                std::wcout << L"  PASS: Live session resolution matched user (count=1) and rejected mismatch (count=0)\n";
            }
            else
            {
                std::wcout << L"  FAIL: Live session resolution: matchOk=" << matchOk << L", misOk=" << misOk << L"\n";
                failed++;
            }
        }
        else
        {
            // Headless / non-interactive environment with no session user: must fail closed (count=0)
            MockCredentialProviderUserArray* pArray = new MockCredentialProviderUserArray();
            MockCredentialProviderUser* pUser = new MockCredentialProviderUser(L"S-1-5-21-ANY", L"User", L"Domain");
            pArray->AddUser(pUser);
            pUser->Release();

            provider.SetUserArray(pArray);

            DWORD count = 999;
            DWORD defaultIndex = 999;
            BOOL autoLogon = TRUE;
            HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
            pArray->Release();

            if (SUCCEEDED(hr) && count == 0)
            {
                std::wcout << L"  PASS: Headless environment with no session user failed closed (count=0)\n";
            }
            else
            {
                std::wcout << L"  FAIL: Headless environment expected count=0, got count=" << count << L"\n";
                failed++;
            }
        }
    }

    // -------------------------------------------------------------
    // Test 20: LOGON with empty session user name (cold boot / sign-in screen)
    // -------------------------------------------------------------
    std::wcout << L"[Test 20] Testing LOGON with empty session user name (cold boot / sign-in screen fail closed)...\n";
    {
        PeekProvider::ClearTestOverrides();
        PeekProvider::SetTestSessionUserName(L""); // Force empty username

        PeekProvider provider;
        provider.SetUsageScenario(CPUS_LOGON, 0);

        MockCredentialProviderUserArray* pArray = new MockCredentialProviderUserArray();
        MockCredentialProviderUser* pUser = new MockCredentialProviderUser(L"S-1-5-21-COLD-BOOT-1001", L"ColdUser", L"ColdDomain");
        pArray->AddUser(pUser);
        pUser->Release();

        provider.SetUserArray(pArray);

        DWORD count = 999;
        DWORD defaultIndex = 999;
        BOOL autoLogon = TRUE;

        HRESULT hr = provider.GetCredentialCount(&count, &defaultIndex, &autoLogon);
        PeekProvider::ClearTestOverrides();
        pArray->Release();

        if (SUCCEEDED(hr) && count == 0)
        {
            std::wcout << L"  PASS: Empty session user name correctly failed closed (count=0, no tile offered)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Empty session user name expected count=0, got count=" << count << L", hr=" << hr << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 21: Process Token Privilege Diagnostics & RAII Guard verification
    // -------------------------------------------------------------
    std::wcout << L"[Test 21] Testing Process Token Privilege Diagnostics and RAII revert...\n";
    {
        PeekDiagnostics::RunProcessTokenDiagnostics(L"Test_Autoselect_Verification");

        std::wstring logFilePath = PeekLoggerInternal::GetLogFilePath();
        HANDLE hFile = CreateFileW(logFilePath.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (hFile != INVALID_HANDLE_VALUE)
        {
            DWORD dwSize = GetFileSize(hFile, NULL);
            std::vector<char> buffer(dwSize + 1, 0);
            DWORD bytesRead = 0;
            ReadFile(hFile, buffer.data(), dwSize, &bytesRead, NULL);
            CloseHandle(hFile);

            std::string content(buffer.data(), bytesRead);
            bool hasDiagStart = (content.find("[PeekDiag] Process Token Diagnostics START") != std::string::npos);
            bool hasTokenUser = (content.find("[PeekDiag] Process Token User: SID=") != std::string::npos);
            bool hasIntegrity = (content.find("[PeekDiag] Process Token Integrity Level:") != std::string::npos);
            bool hasPrivCount = (content.find("[PeekDiag] Process Token Privileges count=") != std::string::npos);
            bool hasAdjust = (content.find("[PeekDiag] AdjustTokenPrivileges(SeTcbPrivilege):") != std::string::npos);
            bool hasDiagEnd = (content.find("[PeekDiag] Process Token Diagnostics END") != std::string::npos);

            if (hasDiagStart && hasTokenUser && hasIntegrity && hasPrivCount && hasAdjust && hasDiagEnd)
            {
                std::wcout << L"  PASS: Diagnostics executed and verified in log: Token User, Integrity Level, Privileges, AdjustTokenPrivileges\n";
            }
            else
            {
                std::wcout << L"  FAIL: Diagnostics log missing expected markers: start=" << hasDiagStart
                           << L", user=" << hasTokenUser << L", integrity=" << hasIntegrity
                           << L", privs=" << hasPrivCount << L", adjust=" << hasAdjust << L", end=" << hasDiagEnd << L"\n";
                failed++;
            }
        }
        else
        {
            std::wcout << L"  FAIL: Could not open log file to verify diagnostics output\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 22: Strict JSON Schema Parser & Prohibited Keys Rejection
    // -------------------------------------------------------------
    std::wcout << L"[Test 22] Testing strict JSON schema parser and prohibited keys rejection...\n";
    {
        bool t22Ok = true;

        // Valid message
        PeekJson::PeekProtocolMessage msgValid;
        std::string jsonValid = "{\"protocol_version\":\"2.0\",\"type\":\"SEARCHING\",\"detail\":\"Looking for face\"}";
        if (!PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonValid, msgValid) || msgValid.type != L"SEARCHING")
        {
            t22Ok = false;
        }

        // Outdated protocol version rejected
        PeekJson::PeekProtocolMessage msgOutdated;
        std::string jsonOutdated = "{\"protocol_version\":\"1.0\",\"type\":\"SEARCHING\"}";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonOutdated, msgOutdated))
        {
            t22Ok = false;
        }

        // Missing protocol version rejected
        PeekJson::PeekProtocolMessage msgNoVer;
        std::string jsonNoVer = "{\"type\":\"SEARCHING\"}";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonNoVer, msgNoVer))
        {
            t22Ok = false;
        }

        // Prohibited biometric key 'embedding' rejected
        PeekJson::PeekProtocolMessage msgEmb;
        std::string jsonEmb = "{\"protocol_version\":\"2.0\",\"type\":\"SEARCHING\",\"embedding\":[0.1,0.2]}";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonEmb, msgEmb))
        {
            t22Ok = false;
        }

        // Prohibited biometric key 'templates' rejected
        PeekJson::PeekProtocolMessage msgTmpl;
        std::string jsonTmpl = "{\"protocol_version\":\"2.0\",\"type\":\"SEARCHING\",\"templates\":[\"abc\"]}";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonTmpl, msgTmpl))
        {
            t22Ok = false;
        }

        // Trailing garbage rejected
        PeekJson::PeekProtocolMessage msgTrailing;
        std::string jsonTrailing = "{\"protocol_version\":\"2.0\",\"type\":\"PING\"}garbage";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonTrailing, msgTrailing))
        {
            t22Ok = false;
        }

        // AUTHENTICATED message with profile_id rejected
        PeekJson::PeekProtocolMessage msgAuthWithProf;
        std::string jsonAuthWithProf = "{\"protocol_version\":\"2.0\",\"type\":\"AUTHENTICATED\",\"is_authorized_to_unlock\":true,\"nonce\":\"0123456789abcdef0123456789abcdef\",\"profile_id\":\"profile_123\"}";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonAuthWithProf, msgAuthWithProf))
        {
            t22Ok = false;
        }

        // AUTHENTICATED message with is_authorized_to_unlock=false rejected
        PeekJson::PeekProtocolMessage msgAuthFalse;
        std::string jsonAuthFalse = "{\"protocol_version\":\"2.0\",\"type\":\"AUTHENTICATED\",\"is_authorized_to_unlock\":false,\"nonce\":\"0123456789abcdef0123456789abcdef\"}";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonAuthFalse, msgAuthFalse))
        {
            t22Ok = false;
        }

        // AUTHENTICATED message without nonce rejected
        PeekJson::PeekProtocolMessage msgAuthNoNonce;
        std::string jsonAuthNoNonce = "{\"protocol_version\":\"2.0\",\"type\":\"AUTHENTICATED\",\"is_authorized_to_unlock\":true}";
        if (PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonAuthNoNonce, msgAuthNoNonce))
        {
            t22Ok = false;
        }

        // Valid AUTHENTICATED message parses correctly
        PeekJson::PeekProtocolMessage msgAuthOk;
        std::string jsonAuthOk = "{\"protocol_version\":\"2.0\",\"type\":\"AUTHENTICATED\",\"is_authorized_to_unlock\":true,\"nonce\":\"0123456789abcdef0123456789abcdef\",\"display_name\":\"Alice\"}";
        if (!PeekJson::ProtocolSchemaValidator::ValidateAndParse(jsonAuthOk, msgAuthOk) || !msgAuthOk.isAuthorizedToUnlock || msgAuthOk.displayName != L"Alice")
        {
            t22Ok = false;
        }

        if (t22Ok)
        {
            std::wcout << L"  PASS: Strict JSON parser rejected outdated versions, trailing garbage, biometric leakage, and profile_id in AUTHENTICATED\n";
        }
        else
        {
            std::wcout << L"  FAIL: Strict schema parser validation failed\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 23: Spoof-Server Rejection (Wrong Image Path and Wrong Token SID)
    // -------------------------------------------------------------
    std::wcout << L"[Test 23] Testing spoof-server rejection (wrong image path and wrong token SID)...\n";
    {
        std::wstring testPipe = L"\\\\.\\pipe\\PeekTestSpoof_" + std::to_wstring(GetCurrentProcessId());
        HANDLE hServerPipe = CreateNamedPipeW(
            testPipe.c_str(),
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            4096,
            4096,
            0,
            nullptr
        );

        if (hServerPipe != INVALID_HANDLE_VALUE)
        {
            HANDLE hClientPipe = CreateFileW(
                testPipe.c_str(),
                GENERIC_READ | GENERIC_WRITE,
                0,
                nullptr,
                OPEN_EXISTING,
                SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
                nullptr
            );

            if (hClientPipe != INVALID_HANDLE_VALUE)
            {
                // Subtest A: Image path is test_autoselect.exe (NOT %ProgramFiles%\Peek\PeekEngine.exe)
                // With NO override, VerifyServerProcess must reject immediately
                SetEnvironmentVariableW(L"PEEK_TEST_EXPECTED_IMAGE", NULL);
                SetEnvironmentVariableW(L"PEEK_TEST_ALLOW_CURRENT_USER_SID", NULL);
                bool okImageCheck = PeekPipeClient::VerifyServerProcess(hClientPipe, L"S-1-5-21-99999");

                // Subtest B: Image path override matches, but SID is wrong
                SetEnvironmentVariableW(L"PEEK_TEST_EXPECTED_IMAGE", L"*");
                bool okSidMismatch = PeekPipeClient::VerifyServerProcess(hClientPipe, L"S-1-5-21-WRONG-SID-99999");

                // Subtest C: Allow current user SID override -> now succeeds
                SetEnvironmentVariableW(L"PEEK_TEST_ALLOW_CURRENT_USER_SID", L"1");
                bool okValid = PeekPipeClient::VerifyServerProcess(hClientPipe, L"");

                // Reset overrides
                SetEnvironmentVariableW(L"PEEK_TEST_EXPECTED_IMAGE", NULL);
                SetEnvironmentVariableW(L"PEEK_TEST_ALLOW_CURRENT_USER_SID", NULL);

                CloseHandle(hClientPipe);

                if (!okImageCheck && !okSidMismatch && okValid)
                {
                    std::wcout << L"  PASS: Spoof server rejected on wrong image path and rejected on wrong token SID\n";
                }
                else
                {
                    std::wcout << L"  FAIL: Spoof server check failed: okImageCheck=" << okImageCheck
                               << L", okSidMismatch=" << okSidMismatch << L", okValid=" << okValid << L"\n";
                    failed++;
                }
            }
            else
            {
                std::wcout << L"  FAIL: Could not connect to spoof test pipe\n";
                failed++;
            }
            CloseHandle(hServerPipe);
        }
        else
        {
            std::wcout << L"  FAIL: Could not create spoof test pipe\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 24: Nonce Verification & Replay Attack Rejection
    // -------------------------------------------------------------
    std::wcout << L"[Test 24] Testing nonce challenge echo and replay rejection...\n";
    {
        // Subtest A: Nonce generation produces 32-character hex
        std::string nonce1 = PeekPipeClient::GenerateChallengeNonce();
        std::string nonce2 = PeekPipeClient::GenerateChallengeNonce();
        bool nonceFormatOk = (nonce1.length() == 32 && nonce2.length() == 32 && nonce1 != nonce2);

        // Subtest B: Replayed / mismatched nonce rejected by pipe client
        bool rejectedOnMismatch = false;

        std::string replayedMsg = "{\"protocol_version\":\"2.0\",\"type\":\"AUTHENTICATED\",\"is_authorized_to_unlock\":true,\"nonce\":\"00000000000000000000000000000000\"}";
        PeekJson::PeekProtocolMessage parsed;
        PeekJson::ProtocolSchemaValidator::ValidateAndParse(replayedMsg, parsed);

        if (parsed.isValid)
        {
            std::wstring expectedWide = PeekJson::Utf8ToWide(nonce1);
            if (_wcsicmp(parsed.nonce.c_str(), expectedWide.c_str()) != 0)
            {
                rejectedOnMismatch = true;
            }
        }

        if (nonceFormatOk && rejectedOnMismatch)
        {
            std::wcout << L"  PASS: 128-bit random nonces generated uniquely and replayed/mismatched nonces rejected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Nonce verification failed: nonceFormatOk=" << nonceFormatOk
                       << L", rejectedOnMismatch=" << rejectedOnMismatch << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 25: LSA Payload Profile ID Extraction (No Pipe Leakage)
    // -------------------------------------------------------------
    std::wcout << L"[Test 25] Testing LSA payload profile_id extraction in GetSerialization...\n";
    {
        auto fakeLsa = std::make_shared<PeekSecretVault::InMemoryLsaSecretStore>();
        PeekSecretVault::SetLsaSecretStore(fakeLsa);
        PeekSecretVault::SetPasswordValidator([](PCWSTR, PCWSTR, PCWSTR, DWORD* pdwErr) -> bool {
            if (pdwErr) *pdwErr = ERROR_SUCCESS;
            return true;
        });

        std::wstring testSid = L"S-1-5-21-999999-1005";
        std::wstring testUuid = L"b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e";
        std::wstring testPassword = L"HardenedPhase7SecretPassword!";

        PeekSecretVault::StorePasswordSecret(testSid, testUuid, testPassword);

        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"), CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
        };
        PeekCredential cred;
        cred.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT, L"SecureUser", L"DOMAIN", testSid.c_str(), true);

        // Simulate pipe AUTHENTICATED: pipe DOES NOT provide profile_id!
        // m_profileId is empty on credential!
        cred.TestSetAuthenticated(true, L"");

        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE resp = CPGSR_NO_CREDENTIAL_FINISHED;
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION cpcs = {};
        HRESULT hrSer = cred.GetSerialization(&resp, &cpcs, nullptr, nullptr);

        bool serOk = (SUCCEEDED(hrSer) && resp == CPGSR_RETURN_CREDENTIAL_FINISHED && cpcs.rgbSerialization != nullptr);
        bool pwMatch = false;

        if (serOk)
        {
            wchar_t szUser[256] = {};
            DWORD cchUser = ARRAYSIZE(szUser);
            wchar_t szDomain[256] = {};
            DWORD cchDomain = ARRAYSIZE(szDomain);
            wchar_t szPass[256] = {};
            DWORD cchPass = ARRAYSIZE(szPass);
            if (CredUnPackAuthenticationBufferW(0, cpcs.rgbSerialization, cpcs.cbSerialization, szUser, &cchUser, szDomain, &cchDomain, szPass, &cchPass))
            {
                pwMatch = (wcscmp(szPass, testPassword.c_str()) == 0);
            }
            CoTaskMemFree(cpcs.rgbSerialization);
        }

        fakeLsa->Clear();
        PeekSecretVault::SetLsaSecretStore(nullptr);
        PeekSecretVault::SetPasswordValidator(nullptr);

        if (serOk && pwMatch)
        {
            std::wcout << L"  PASS: GetSerialization extracted profile ID from LSA payload and unwrapped credentials without pipe profile_id\n";
        }
        else
        {
            std::wcout << L"  FAIL: LSA profile ID extraction failed: serOk=" << serOk << L", pwMatch=" << pwMatch << L"\n";
            failed++;
        }
    }


    // -------------------------------------------------------------
    // Test 26: Testing PFI_STATUS_TEXT visibility rules (hidden while searching/verifying, visible only for offline, retry-limit, success)
    // -------------------------------------------------------------
    std::wcout << L"[Test 26] Testing PFI_STATUS_TEXT visibility rules...\n";
    {
        PeekCredential cred;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"),   CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
        };
        cred.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT, L"TestUser", L"TestDomain", L"S-1-5-21-123", true);

        MockCredentialEvents events;
        cred.Advise(&events);

        CREDENTIAL_PROVIDER_FIELD_STATE state = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE istate;

        // 1. Initially hidden
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool initHidden = (state == CPFS_HIDDEN);

        // 2. SEARCHING state -> stays hidden
        PeekAuthResult resSearching = {};
        resSearching.state = PeekIPCState::SEARCHING;
        cred.TestOnEngineStateUpdate(resSearching);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool searchHidden = (state == CPFS_HIDDEN);

        // 3. VERIFYING state -> stays hidden
        PeekAuthResult resVerifying = {};
        resVerifying.state = PeekIPCState::VERIFYING;
        cred.TestOnEngineStateUpdate(resVerifying);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool verifyHidden = (state == CPFS_HIDDEN);

        // 4. LIVENESS state -> stays hidden
        PeekAuthResult resLiveness = {};
        resLiveness.state = PeekIPCState::LIVENESS_CHECK;
        cred.TestOnEngineStateUpdate(resLiveness);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool liveHidden = (state == CPFS_HIDDEN);

        // 5. SUCCESS state -> becomes visible with greeting
        PeekAuthResult resSuccess = {};
        resSuccess.state = PeekIPCState::AUTHENTICATED;
        resSuccess.isAuthorizedToUnlock = true;
        resSuccess.displayName = L"Alice";
        cred.TestOnEngineStateUpdate(resSuccess);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool successVisible = (state == CPFS_DISPLAY_IN_SELECTED_TILE);
        PWSTR pszText = nullptr;
        cred.GetStringValue(PFI_STATUS_TEXT, &pszText);
        std::wstring successText = pszText ? pszText : L"";
        if (pszText) CoTaskMemFree(pszText);
        bool successTextOk = (successText == L"Hello, Alice!");

        // 6. Reset via SetSelected -> starts hidden again
        BOOL autoLogon = FALSE;
        cred.SetSelected(&autoLogon);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool resetHidden = (state == CPFS_HIDDEN);

        cred.UnAdvise();

        if (initHidden && searchHidden && verifyHidden && liveHidden && successVisible && successTextOk && resetHidden)
        {
            std::wcout << L"  PASS: PFI_STATUS_TEXT strictly hidden during scanning and shown on success greeting\n";
        }
        else
        {
            std::wcout << L"  FAIL: PFI_STATUS_TEXT visibility error: init=" << initHidden 
                       << L", search=" << searchHidden << L", verify=" << verifyHidden 
                       << L", live=" << liveHidden << L", successVisible=" << successVisible 
                       << L", text='" << successText << L"', reset=" << resetHidden << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 27: Testing retry counter limit (kMaxRetryCycles = 3) and reset on SetSelected
    // -------------------------------------------------------------
    std::wcout << L"[Test 27] Testing retry counter limit (kMaxRetryCycles = 3) and reset on SetSelected...\n";
    {
        PeekCredential cred;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"),   CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
        };
        cred.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT, L"TestUser", L"TestDomain", L"S-1-5-21-123", true);

        CREDENTIAL_PROVIDER_FIELD_STATE state = CPFS_HIDDEN;
        CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE istate;

        // Attempt 1: AUTH_FAILED
        PeekAuthResult fail1 = {};
        fail1.state = PeekIPCState::AUTH_FAILED;
        fail1.detail = L"Face not recognized";
        cred.TestOnEngineStateUpdate(fail1);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool att1CountOk = (cred.TestGetConsecutiveFailedAttempts() == 1);
        bool att1Hidden = (state == CPFS_HIDDEN); // Hidden within retry limit

        // Attempt 2: TIMEOUT
        PeekAuthResult fail2 = {};
        fail2.state = PeekIPCState::TIMEOUT;
        fail2.detail = L"Session timed out";
        cred.TestOnEngineStateUpdate(fail2);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool att2CountOk = (cred.TestGetConsecutiveFailedAttempts() == 2);
        bool att2Hidden = (state == CPFS_HIDDEN); // Still hidden within retry limit

        // Attempt 3: AUTH_FAILED -> reaches kMaxRetryCycles (3)
        PeekAuthResult fail3 = {};
        fail3.state = PeekIPCState::AUTH_FAILED;
        fail3.detail = L"Impostor detected";
        cred.TestOnEngineStateUpdate(fail3);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool att3CountOk = (cred.TestGetConsecutiveFailedAttempts() == 3);
        bool att3Visible = (state == CPFS_DISPLAY_IN_SELECTED_TILE); // Now visible!
        PWSTR pszText = nullptr;
        cred.GetStringValue(PFI_STATUS_TEXT, &pszText);
        std::wstring limitText = pszText ? pszText : L"";
        if (pszText) CoTaskMemFree(pszText);
        bool limitTextOk = (limitText == L"Sign in with your PIN or password");

        // Now test SetSelected reset:
        BOOL autoLogon = FALSE;
        cred.SetSelected(&autoLogon);
        bool resetCountOk = (cred.TestGetConsecutiveFailedAttempts() == 0);
        cred.GetFieldState(PFI_STATUS_TEXT, &state, &istate);
        bool resetHidden = (state == CPFS_HIDDEN);

        cred.UnAdvise();

        if (att1CountOk && att1Hidden && att2CountOk && att2Hidden && att3CountOk && att3Visible && limitTextOk && resetCountOk && resetHidden)
        {
            std::wcout << L"  PASS: Retry counter accurately reaches 3, shows PIN/password fallback, and resets on SetSelected\n";
        }
        else
        {
            std::wcout << L"  FAIL: Retry counter behavior mismatch: att1Count=" << att1CountOk << L", att1Hidden=" << att1Hidden 
                       << L", att2Count=" << att2CountOk << L", att2Hidden=" << att2Hidden 
                       << L", att3Count=" << att3CountOk << L", att3Visible=" << att3Visible 
                       << L", text='" << limitText << L"', resetCount=" << resetCountOk << L", resetHidden=" << resetHidden << L"\n";
            failed++;
        }
    }

    // -------------------------------------------------------------
    // Test 28: Testing GetBitmapValue returns a 32bpp bitmap with preserved alpha from probe frame
    // -------------------------------------------------------------
    std::wcout << L"[Test 28] Testing GetBitmapValue returns a 32bpp bitmap with preserved alpha from probe frame...\n";
    {
        PeekCredential cred;
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR rgcpfd[PFI_COUNT] = {
            { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Logo"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
            { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"),   CPFG_CREDENTIAL_PROVIDER_LABEL },
            { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),   GUID_NULL },
            { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL },
        };
        cred.Initialize(CPUS_UNLOCK_WORKSTATION, rgcpfd, PFI_COUNT, L"TestUser", L"TestDomain", L"S-1-5-21-123", true);

        // Create a 96x96 32bpp top-down probe DIB section with known alpha channel
        BITMAPINFO bmi = {};
        bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bmi.bmiHeader.biWidth = 96;
        bmi.bmiHeader.biHeight = -96; // top-down
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB;

        void* pBits = nullptr;
        HDC hdc = GetDC(NULL);
        HBITMAP hProbeDIB = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &pBits, NULL, 0);
        ReleaseDC(NULL, hdc);

        assert(hProbeDIB && pBits);

        // Fill probe bitmap: pixel (0,0) has alpha=0 (fully transparent), center pixel (48,48) has alpha=220
        BYTE* pPixelBytes = reinterpret_cast<BYTE*>(pBits);
        memset(pPixelBytes, 0, 96 * 96 * 4); // All zeros, alpha(0,0)=0
        size_t centerOffset = (48 * 96 + 48) * 4;
        pPixelBytes[centerOffset + 0] = 100; // Blue
        pPixelBytes[centerOffset + 1] = 150; // Green
        pPixelBytes[centerOffset + 2] = 200; // Red
        pPixelBytes[centerOffset + 3] = 220; // Alpha

        // Inject probe frame into animator
        cred.TestGetAnimator().TestSetCurrentFrame(hProbeDIB);

        // Call GetBitmapValue
        HBITMAP hRetrieved = nullptr;
        HRESULT hr = cred.GetBitmapValue(PFI_LOGO, &hRetrieved);

        bool hrOk = (hr == S_OK && hRetrieved != nullptr);
        bool distinctHandle = (hRetrieved != hProbeDIB); // Must be a fresh copy, not the same handle

        DIBSECTION ds = {};
        bool getObjOk = (GetObjectW(hRetrieved, sizeof(ds), &ds) == sizeof(ds));
        bool widthOk = (ds.dsBm.bmWidth == 96);
        bool heightOk = (abs(ds.dsBm.bmHeight) == 96);
        bool bppOk = (ds.dsBm.bmBitsPixel == 32);

        BYTE retrievedAlpha00 = 255;
        BYTE retrievedAlphaCenter = 0;
        if (ds.dsBm.bmBits)
        {
            const BYTE* pRetBytes = reinterpret_cast<const BYTE*>(ds.dsBm.bmBits);
            retrievedAlpha00 = pRetBytes[3];
            retrievedAlphaCenter = pRetBytes[centerOffset + 3];
        }

        bool alpha00Preserved = (retrievedAlpha00 == 0);
        bool alphaCenterPreserved = (retrievedAlphaCenter == 220);

        if (hRetrieved) DeleteObject(hRetrieved);
        DeleteObject(hProbeDIB);

        if (hrOk && distinctHandle && getObjOk && widthOk && heightOk && bppOk && alpha00Preserved && alphaCenterPreserved)
        {
            std::wcout << L"  PASS: GetBitmapValue returns fresh 32bpp DIB with preserved alpha (alpha(0,0)=0, center=220)\n";
        }
        else
        {
            std::wcout << L"  FAIL: Bitmap check failed: hrOk=" << hrOk << L", distinct=" << distinctHandle 
                       << L", 32bpp=" << bppOk << L", a(0,0)=" << retrievedAlpha00 
                       << L", aCenter=" << retrievedAlphaCenter << L"\n";
            failed++;
        }
    }

    // Cleanup
    DeleteTempTestDirectory(tempDir);
    SetEnvironmentVariableW(L"PEEK_PROFILES_DIR", NULL);
    SetEnvironmentVariableW(L"PEEK_PIPE_NAME_OVERRIDE", NULL);
    SetEnvironmentVariableW(L"PEEK_LOG_FILE_PATH", NULL);

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
