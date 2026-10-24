// Peek Face Engine - Credential Provider Main Class Implementation
#include "PeekProvider.h"
#include <sddl.h>

static const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR s_rgcpfd[PFI_COUNT] =
{
    { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Peek Biometrics"),   CPFG_CREDENTIAL_PROVIDER_LOGO },
    { PFI_LABEL,        CPFT_SMALL_TEXT, const_cast<PWSTR>(L"PEEK"), CPFG_CREDENTIAL_PROVIDER_LABEL },
    { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User"),              GUID_NULL },
    { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"),            GUID_NULL },
};

static void LogFieldTableOnce()
{
    static bool s_logged = false;
    if (s_logged) return;
    s_logged = true;

    Logger::LogInfo("=== Peek Credential Provider Field Table ===");
    for (DWORD i = 0; i < PFI_COUNT; i++)
    {
        wchar_t szGuid[64] = {};
        StringFromGUID2(s_rgcpfd[i].guidFieldType, szGuid, ARRAYSIZE(szGuid));
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"Field %u: ID=%u, cpft=%d, label='%s', guidFieldType=%s",
            i,
            s_rgcpfd[i].dwFieldID,
            s_rgcpfd[i].cpft,
            s_rgcpfd[i].pszLabel ? s_rgcpfd[i].pszLabel : L"(null)",
            szGuid);
        Logger::LogInfo(szBuf);
    }
    Logger::LogInfo("============================================");
}

static HRESULT FieldDescriptorCoTaskAlloc(
    const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR& rcpfd,
    CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd
)
{
    if (!ppcpfd) return E_POINTER;
    *ppcpfd = nullptr;

    CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR* pcpfd = static_cast<CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR*>(
        CoTaskMemAlloc(sizeof(CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR))
    );
    if (!pcpfd) return E_OUTOFMEMORY;

    pcpfd->dwFieldID = rcpfd.dwFieldID;
    pcpfd->cpft = rcpfd.cpft;
    pcpfd->guidFieldType = rcpfd.guidFieldType;

    HRESULT hr = CoTaskMemAllocString(rcpfd.pszLabel, &pcpfd->pszLabel);
    if (FAILED(hr))
    {
        CoTaskMemFree(pcpfd);
        return hr;
    }

    *ppcpfd = pcpfd;
    return S_OK;
}

PeekProvider::PeekProvider()
    : m_cRef(1)
    , m_cpus(CPUS_LOGON)
    , m_dwFlags(0)
    , m_pcpe(nullptr)
    , m_upAdviseContext(0)
    , m_pCredential(nullptr)
    , m_pUserArray(nullptr)
{
    DllAddRef();
    LogFieldTableOnce();
}

PeekProvider::~PeekProvider()
{
    ReleaseCredential();
    ReleaseUserArray();
    if (m_pcpe)
    {
        m_pcpe->Release();
        m_pcpe = nullptr;
    }
    DllRelease();
}

void PeekProvider::ReleaseCredential()
{
    if (m_pCredential)
    {
        m_pCredential->SetProvider(nullptr);
        m_pCredential->Release();
        m_pCredential = nullptr;
    }
}

void PeekProvider::ReleaseUserArray()
{
    if (m_pUserArray)
    {
        m_pUserArray->Release();
        m_pUserArray = nullptr;
    }
}

void PeekProvider::NotifyCredentialsChanged()
{
    if (m_pcpe)
    {
        m_pcpe->CredentialsChanged(m_upAdviseContext);
    }
}

// --- IUnknown ---

HRESULT PeekProvider::QueryInterface(REFIID riid, void** ppv)
{
    if (!ppv) return E_POINTER;
    *ppv = nullptr;

    std::wstring iidStr = GuidToString(riid);
    HRESULT hr = E_NOINTERFACE;

    if (riid == IID_IUnknown || riid == IID_ICredentialProvider)
    {
        *ppv = static_cast<ICredentialProvider*>(this);
        AddRef();
        hr = S_OK;
        Logger::LogInfo(L"PeekProvider::QueryInterface: accepted IID=" + iidStr + L" (ICredentialProvider) -> S_OK");
    }
    else if (riid == IID_ICredentialProviderSetUserArray)
    {
        *ppv = static_cast<ICredentialProviderSetUserArray*>(this);
        AddRef();
        hr = S_OK;
        Logger::LogInfo(L"PeekProvider::QueryInterface: accepted IID=" + iidStr + L" (ICredentialProviderSetUserArray) -> S_OK");
    }
    else
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::QueryInterface: rejected IID=%s -> E_NOINTERFACE (0x%08X)", iidStr.c_str(), hr);
        Logger::LogInfo(szBuf);
    }

    return hr;
}

ULONG PeekProvider::AddRef()
{
    return InterlockedIncrement(&m_cRef);
}

ULONG PeekProvider::Release()
{
    LONG cRef = InterlockedDecrement(&m_cRef);
    if (cRef == 0)
    {
        delete this;
    }
    return cRef;
}

// --- ICredentialProvider ---

HRESULT PeekProvider::SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, DWORD dwFlags)
{
    m_cpus = cpus;
    m_dwFlags = dwFlags;

    const wchar_t* pszScenario = L"UNKNOWN";
    switch (cpus)
    {
    case CPUS_LOGON:              pszScenario = L"CPUS_LOGON"; break;
    case CPUS_UNLOCK_WORKSTATION: pszScenario = L"CPUS_UNLOCK_WORKSTATION"; break;
    case CPUS_CHANGE_PASSWORD:    pszScenario = L"CPUS_CHANGE_PASSWORD"; break;
    case CPUS_CREDUI:             pszScenario = L"CPUS_CREDUI"; break;
    default:                      break;
    }

    HRESULT hr = E_NOTIMPL;
    switch (cpus)
    {
    case CPUS_UNLOCK_WORKSTATION:
    case CPUS_LOGON:
        hr = S_OK;
        break;

    case CPUS_CREDUI:
        // Before first interactive sign-in the user's profile/DPAPI master key
        // is not reliably available. Do not advertise a credential tile here.
        hr = E_NOTIMPL;
        break;

    case CPUS_CHANGE_PASSWORD:
    default:
        // Biometrics cannot perform password change; gracefully decline scenario
        hr = E_NOTIMPL;
        break;
    }

    wchar_t szBuf[256] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"PeekProvider::SetUsageScenario: scenario=%u (%s), flags=0x%08X -> hr=0x%08X",
        cpus, pszScenario, dwFlags, hr);
    Logger::LogInfo(szBuf);

    // Run process token diagnostics once per LogonUI load
    static INIT_ONCE s_diagOnce = INIT_ONCE_STATIC_INIT;
    InitOnceExecuteOnce(&s_diagOnce, [](PINIT_ONCE, PVOID, PVOID*) -> BOOL {
        PeekDiagnostics::RunProcessTokenDiagnostics(L"PeekProvider::SetUsageScenario");
        return TRUE;
    }, NULL, NULL);

    return hr;
}

HRESULT PeekProvider::SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* /*pcpcs*/)
{
    // Reverted to E_NOTIMPL matching standard Microsoft Credential Provider samples.
    // LogonUI calls SetSerialization for prepopulating tiles in CredUI or remote scenarios.
    Logger::LogInfo("PeekProvider::SetSerialization called -> E_NOTIMPL");
    return E_NOTIMPL;
}

HRESULT PeekProvider::Advise(ICredentialProviderEvents* pcpe, UINT_PTR upAdviseContext)
{
    wchar_t szBuf[256] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::Advise: pcpe=%p, upAdviseContext=0x%p", pcpe, (void*)upAdviseContext);
    Logger::LogInfo(szBuf);

    if (m_pcpe)
    {
        m_pcpe->Release();
    }
    m_pcpe = pcpe;
    m_upAdviseContext = upAdviseContext;
    if (m_pcpe)
    {
        m_pcpe->AddRef();
    }
    Logger::LogInfo("PeekProvider::Advise -> S_OK");
    return S_OK;
}

HRESULT PeekProvider::UnAdvise()
{
    Logger::LogInfo("PeekProvider::UnAdvise called");
    if (m_pcpe)
    {
        m_pcpe->Release();
        m_pcpe = nullptr;
    }
    m_upAdviseContext = 0;
    ReleaseUserArray();
    Logger::LogInfo("PeekProvider::UnAdvise -> S_OK");
    return S_OK;
}

HRESULT PeekProvider::GetFieldDescriptorCount(DWORD* pdwCount)
{
    if (!pdwCount)
    {
        Logger::LogError("PeekProvider::GetFieldDescriptorCount: pdwCount is null -> E_POINTER");
        return E_POINTER;
    }
    LogFieldTableOnce();
    *pdwCount = PFI_COUNT;
    wchar_t szBuf[128] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::GetFieldDescriptorCount: count=%u -> S_OK", *pdwCount);
    Logger::LogInfo(szBuf);
    return S_OK;
}

HRESULT PeekProvider::GetFieldDescriptorAt(DWORD dwIndex, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd)
{
    if (!ppcpfd)
    {
        Logger::LogError("PeekProvider::GetFieldDescriptorAt: ppcpfd is null -> E_POINTER");
        return E_POINTER;
    }
    *ppcpfd = nullptr;

    if (dwIndex >= PFI_COUNT)
    {
        wchar_t szBuf[128] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::GetFieldDescriptorAt: dwIndex=%u >= PFI_COUNT(%u) -> E_INVALIDARG", dwIndex, PFI_COUNT);
        Logger::LogError(szBuf);
        return E_INVALIDARG;
    }

    HRESULT hr = FieldDescriptorCoTaskAlloc(s_rgcpfd[dwIndex], ppcpfd);
    wchar_t szBuf[256] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"PeekProvider::GetFieldDescriptorAt: dwIndex=%u, label='%s', cpft=%d -> hr=0x%08X",
        dwIndex,
        s_rgcpfd[dwIndex].pszLabel ? s_rgcpfd[dwIndex].pszLabel : L"(null)",
        s_rgcpfd[dwIndex].cpft,
        hr);
    Logger::LogInfo(szBuf);
    return hr;
}

// --- ICredentialProviderSetUserArray ---

HRESULT PeekProvider::SetUserArray(ICredentialProviderUserArray* users)
{
    Logger::LogInfo("PeekProvider::SetUserArray called");

    ReleaseUserArray();

    if (users)
    {
        m_pUserArray = users;
        m_pUserArray->AddRef();

        DWORD dwCount = 0;
        if (SUCCEEDED(users->GetCount(&dwCount)) && dwCount > 0)
        {
            ICredentialProviderUser* pUser = nullptr;
            if (SUCCEEDED(users->GetAt(0, &pUser)) && pUser)
            {
                LPWSTR pszSid = nullptr;
                if (SUCCEEDED(pUser->GetSid(&pszSid)) && pszSid)
                {
                    std::wstring logMsg = L"PeekProvider::SetUserArray: locked user SID = ";
                    logMsg += pszSid;
                    Logger::LogInfo(logMsg);
                    CoTaskMemFree(pszSid);
                }
                pUser->Release();
            }
        }
    }
    else
    {
        Logger::LogInfo("PeekProvider::SetUserArray: users is null");
    }

    // Recreate the credential if the user array changes
    ReleaseCredential();

    return S_OK;
}

bool PeekProvider::GetConsoleUserSid(std::wstring& outSid)
{
    outSid.clear();

#ifdef PEEK_TEST_BUILD
    if (s_hasTestConsoleUserSid)
    {
        if (s_testConsoleUserSid == L"FAIL" || s_testConsoleUserSid == L"NONE")
        {
            Logger::LogInfo(L"PeekProvider::GetConsoleUserSid: [TEST] Forced session user query failure (s_testConsoleUserSid)");
            return false;
        }
        outSid = s_testConsoleUserSid;
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::GetConsoleUserSid: [TEST] Overriding console SID='%s'", outSid.c_str());
        Logger::LogInfo(szBuf);
        return true;
    }
    else
    {
        wchar_t szEnvSid[256] = {};
        if (GetEnvironmentVariableW(L"PEEK_TEST_CONSOLE_USER_SID", szEnvSid, ARRAYSIZE(szEnvSid)) > 0)
        {
            if (wcscmp(szEnvSid, L"FAIL") == 0 || wcscmp(szEnvSid, L"NONE") == 0)
            {
                Logger::LogInfo(L"PeekProvider::GetConsoleUserSid: [TEST] Forced session user query failure (PEEK_TEST_CONSOLE_USER_SID)");
                return false;
            }
            outSid = szEnvSid;
            wchar_t szBuf[256] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::GetConsoleUserSid: [TEST] Overriding console SID='%s'", outSid.c_str());
            Logger::LogInfo(szBuf);
            return true;
        }
    }
#endif

    DWORD sessionId = 0xFFFFFFFF;
    if (!ProcessIdToSessionId(GetCurrentProcessId(), &sessionId))
    {
        DWORD dwErr = GetLastError();
        sessionId = WTSGetActiveConsoleSessionId();
        wchar_t szBuf[128] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::GetConsoleUserSid: ProcessIdToSessionId failed (err=%u), fallback to WTSGetActiveConsoleSessionId=%u",
            dwErr, sessionId);
        Logger::LogInfo(szBuf);
    }
    else
    {
        wchar_t szBuf[128] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::GetConsoleUserSid: ProcessIdToSessionId succeeded, sessionId=%u",
            sessionId);
        Logger::LogInfo(szBuf);
    }

#ifdef PEEK_TEST_BUILD
    if (s_hasTestSessionId)
    {
        sessionId = s_testSessionId;
        wchar_t szBuf[128] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::GetConsoleUserSid: [TEST] Overriding sessionId=0x%08X", sessionId);
        Logger::LogInfo(szBuf);
    }
    else
    {
        wchar_t szEnvSession[32] = {};
        if (GetEnvironmentVariableW(L"PEEK_TEST_CONSOLE_SESSION_ID", szEnvSession, ARRAYSIZE(szEnvSession)) > 0)
        {
            if (wcscmp(szEnvSession, L"NONE") == 0 || wcscmp(szEnvSession, L"0xFFFFFFFF") == 0)
            {
                sessionId = 0xFFFFFFFF;
            }
            else
            {
                sessionId = static_cast<DWORD>(wcstoul(szEnvSession, nullptr, 0));
            }
            wchar_t szBuf[128] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::GetConsoleUserSid: [TEST] Overriding sessionId=0x%08X", sessionId);
            Logger::LogInfo(szBuf);
        }
    }

    if (s_hasTestSessionUserName)
    {
        if (s_testSessionUserName.empty())
        {
            Logger::LogInfo(L"PeekProvider::GetConsoleUserSid: [TEST] Overriding session user name with empty string -> fail closed (no tile)");
            return false;
        }
    }
#endif

    if (sessionId == 0xFFFFFFFF)
    {
        Logger::LogInfo(L"PeekProvider::GetConsoleUserSid: sessionId is 0xFFFFFFFF (no active session) -> fail closed (no tile)");
        return false;
    }

    // Query session user name, domain name, and connection state via WTSQuerySessionInformationW
    LPWSTR pszUserName = nullptr;
    LPWSTR pszDomainName = nullptr;
    WTS_CONNECTSTATE_CLASS* pConnectState = nullptr;
    DWORD dwBytes = 0;

    BOOL bStateOk = WTSQuerySessionInformationW(
        WTS_CURRENT_SERVER_HANDLE,
        sessionId,
        WTSConnectState,
        reinterpret_cast<LPWSTR*>(&pConnectState),
        &dwBytes
    );

    BOOL bUserOk = WTSQuerySessionInformationW(
        WTS_CURRENT_SERVER_HANDLE,
        sessionId,
        WTSUserName,
        &pszUserName,
        &dwBytes
    );

    BOOL bDomainOk = WTSQuerySessionInformationW(
        WTS_CURRENT_SERVER_HANDLE,
        sessionId,
        WTSDomainName,
        &pszDomainName,
        &dwBytes
    );

    int connectStateVal = (bStateOk && pConnectState) ? static_cast<int>(*pConnectState) : -1;
    wchar_t szBufInfo[512] = {};
    swprintf_s(szBufInfo, ARRAYSIZE(szBufInfo),
        L"PeekProvider::GetConsoleUserSid: session=%u, userOk=%d ('%s'), domainOk=%d ('%s'), stateOk=%d (state=%d)",
        sessionId,
        bUserOk, (bUserOk && pszUserName) ? pszUserName : L"",
        bDomainOk, (bDomainOk && pszDomainName) ? pszDomainName : L"",
        bStateOk, connectStateVal
    );
    Logger::LogInfo(szBufInfo);

    // Fail closed if user name is empty (cold boot / sign-in screen / switch-user logon session)
    if (!bUserOk || !pszUserName || pszUserName[0] == L'\0')
    {
        Logger::LogInfo(L"PeekProvider::GetConsoleUserSid: Session user name is empty (cold boot / sign-in screen / switch-user logon session) -> fail closed (no tile)");
        if (pszUserName) WTSFreeMemory(pszUserName);
        if (pszDomainName) WTSFreeMemory(pszDomainName);
        if (pConnectState) WTSFreeMemory(pConnectState);
        return false;
    }

    // Construct account name for SID lookup: "DOMAIN\user" (or fallback "user")
    std::wstring accountName;
    if (bDomainOk && pszDomainName && pszDomainName[0] != L'\0')
    {
        accountName = pszDomainName;
        accountName += L"\\";
    }
    accountName += pszUserName;

    // Resolve account name to binary SID via LookupAccountNameW
    DWORD cbSid = 0;
    DWORD cchReferencedDomain = 0;
    SID_NAME_USE snu;
    SetLastError(ERROR_SUCCESS);
    LookupAccountNameW(NULL, accountName.c_str(), NULL, &cbSid, NULL, &cchReferencedDomain, &snu);
    DWORD dwErrLookup = GetLastError();

    bool bSidResolved = false;
    std::vector<BYTE> sidBuffer;

    if (dwErrLookup == ERROR_INSUFFICIENT_BUFFER && cbSid > 0)
    {
        sidBuffer.resize(cbSid);
        std::vector<WCHAR> refDomain(cchReferencedDomain > 0 ? cchReferencedDomain : 1);
        if (LookupAccountNameW(NULL, accountName.c_str(), sidBuffer.data(), &cbSid, refDomain.data(), &cchReferencedDomain, &snu))
        {
            bSidResolved = true;
        }
        else
        {
            DWORD dwErr2 = GetLastError();
            wchar_t szBuf[256] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf),
                L"PeekProvider::GetConsoleUserSid: LookupAccountNameW for '%s' failed on second call, error=%u",
                accountName.c_str(), dwErr2);
            Logger::LogError(szBuf);
        }
    }
    else
    {
        // Fallback: try username without domain prefix if domain was prefixed
        if (bDomainOk && pszDomainName && pszDomainName[0] != L'\0')
        {
            cbSid = 0;
            cchReferencedDomain = 0;
            SetLastError(ERROR_SUCCESS);
            LookupAccountNameW(NULL, pszUserName, NULL, &cbSid, NULL, &cchReferencedDomain, &snu);
            if (GetLastError() == ERROR_INSUFFICIENT_BUFFER && cbSid > 0)
            {
                sidBuffer.resize(cbSid);
                std::vector<WCHAR> refDomain(cchReferencedDomain > 0 ? cchReferencedDomain : 1);
                if (LookupAccountNameW(NULL, pszUserName, sidBuffer.data(), &cbSid, refDomain.data(), &cchReferencedDomain, &snu))
                {
                    bSidResolved = true;
                    Logger::LogInfo(L"PeekProvider::GetConsoleUserSid: Fallback LookupAccountNameW for username alone succeeded");
                }
            }
        }

        if (!bSidResolved)
        {
            wchar_t szBuf[256] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf),
                L"PeekProvider::GetConsoleUserSid: LookupAccountNameW for '%s' failed, error=%u -> fail closed",
                accountName.c_str(), dwErrLookup);
            Logger::LogError(szBuf);
        }
    }

    // Always free WTS memory buffers
    if (pszUserName) WTSFreeMemory(pszUserName);
    if (pszDomainName) WTSFreeMemory(pszDomainName);
    if (pConnectState) WTSFreeMemory(pConnectState);

    // Convert resolved binary SID to string SID
    if (bSidResolved)
    {
        LPWSTR pszSidString = nullptr;
        if (ConvertSidToStringSidW(reinterpret_cast<PSID>(sidBuffer.data()), &pszSidString) && pszSidString)
        {
            outSid = pszSidString;
            LocalFree(pszSidString);
            wchar_t szBuf[256] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf),
                L"PeekProvider::GetConsoleUserSid: Successfully resolved session user '%s' to SID='%s'",
                accountName.c_str(), outSid.c_str());
            Logger::LogInfo(szBuf);
            return true;
        }
        else
        {
            DWORD dwErrConv = GetLastError();
            wchar_t szBuf[128] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf),
                L"PeekProvider::GetConsoleUserSid: ConvertSidToStringSidW failed, error=%u", dwErrConv);
            Logger::LogError(szBuf);
            return false;
        }
    }

    return false;
}

HRESULT PeekProvider::ResolveLockedUser(DWORD* pdwUserIndex, bool* pbLockedSession)
{
    if (!pdwUserIndex || !pbLockedSession)
    {
        return E_POINTER;
    }
    *pdwUserIndex = 0;
    *pbLockedSession = false;

    if (!m_pUserArray)
    {
        Logger::LogInfo(L"PeekProvider::ResolveLockedUser: m_pUserArray is null -> S_FALSE");
        return S_FALSE;
    }

    DWORD dwUserCount = 0;
    HRESULT hr = m_pUserArray->GetCount(&dwUserCount);
    if (FAILED(hr) || dwUserCount == 0)
    {
        wchar_t szBuf[128] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::ResolveLockedUser: dwUserCount=0 or GetCount failed (hr=0x%08X) -> S_FALSE", hr);
        Logger::LogInfo(szBuf);
        return S_FALSE;
    }

    if (m_cpus == CPUS_UNLOCK_WORKSTATION)
    {
        DWORD chosenIndex = 0;
        if (dwUserCount > 1)
        {
            std::wstring consoleSid;
            if (GetConsoleUserSid(consoleSid) && !consoleSid.empty())
            {
                for (DWORD i = 0; i < dwUserCount; i++)
                {
                    ICredentialProviderUser* pUser = nullptr;
                    if (SUCCEEDED(m_pUserArray->GetAt(i, &pUser)) && pUser)
                    {
                        LPWSTR pszSid = nullptr;
                        if (SUCCEEDED(pUser->GetSid(&pszSid)) && pszSid)
                        {
                            bool match = (_wcsicmp(pszSid, consoleSid.c_str()) == 0);
                            wchar_t szBuf[256] = {};
                            swprintf_s(szBuf, ARRAYSIZE(szBuf),
                                L"PeekProvider::ResolveLockedUser (UNLOCK): array user %u SID='%s' vs console SID='%s' -> match=%d",
                                i, pszSid, consoleSid.c_str(), match);
                            Logger::LogInfo(szBuf);
                            CoTaskMemFree(pszSid);
                            if (match)
                            {
                                chosenIndex = i;
                                pUser->Release();
                                break;
                            }
                        }
                        pUser->Release();
                    }
                }
            }
        }

        *pdwUserIndex = chosenIndex;
        *pbLockedSession = true;
        wchar_t szBuf[128] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::ResolveLockedUser (UNLOCK): selected user index %u, lockedSession=true -> S_OK", chosenIndex);
        Logger::LogInfo(szBuf);
        return S_OK;
    }
    else if (m_cpus == CPUS_LOGON)
    {
        std::wstring consoleSid;
        if (!GetConsoleUserSid(consoleSid) || consoleSid.empty())
        {
            Logger::LogInfo(L"PeekProvider::ResolveLockedUser (LOGON): no console user session found -> S_FALSE (tile will not be offered)");
            return S_FALSE;
        }

        DWORD matchedIndex = (DWORD)-1;
        for (DWORD i = 0; i < dwUserCount; i++)
        {
            ICredentialProviderUser* pUser = nullptr;
            if (SUCCEEDED(m_pUserArray->GetAt(i, &pUser)) && pUser)
            {
                LPWSTR pszSid = nullptr;
                if (SUCCEEDED(pUser->GetSid(&pszSid)) && pszSid)
                {
                    bool match = (_wcsicmp(pszSid, consoleSid.c_str()) == 0);
                    wchar_t szBuf[256] = {};
                    swprintf_s(szBuf, ARRAYSIZE(szBuf),
                        L"PeekProvider::ResolveLockedUser (LOGON): array user %u SID='%s' vs console SID='%s' -> match=%d",
                        i, pszSid, consoleSid.c_str(), match);
                    Logger::LogInfo(szBuf);
                    CoTaskMemFree(pszSid);
                    if (match)
                    {
                        matchedIndex = i;
                        pUser->Release();
                        break;
                    }
                }
                pUser->Release();
            }
        }

        if (matchedIndex == (DWORD)-1)
        {
            wchar_t szBuf[256] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf),
                L"PeekProvider::ResolveLockedUser (LOGON): no array user matches console SID='%s' -> S_FALSE (tile will not be offered)",
                consoleSid.c_str());
            Logger::LogInfo(szBuf);
            return S_FALSE;
        }

        *pdwUserIndex = matchedIndex;
        *pbLockedSession = true;
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::ResolveLockedUser (LOGON): matched user index %u for console SID='%s', lockedSession=true -> S_OK",
            matchedIndex, consoleSid.c_str());
        Logger::LogInfo(szBuf);
        return S_OK;
    }

    return E_NOTIMPL;
}

HRESULT PeekProvider::GetCredentialCount(
    DWORD* pdwCount,
    DWORD* pdwDefault,
    BOOL* pbAutoLogonWithDefault
)
{
    if (!pdwCount || !pdwDefault || !pbAutoLogonWithDefault)
    {
        Logger::LogError("PeekProvider::GetCredentialCount: null pointer arg -> E_POINTER");
        return E_POINTER;
    }

    *pdwCount = 0;
    *pdwDefault = CREDENTIAL_PROVIDER_NO_DEFAULT;
    *pbAutoLogonWithDefault = FALSE;

    if (m_cpus != CPUS_UNLOCK_WORKSTATION && m_cpus != CPUS_LOGON)
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::GetCredentialCount: early return count=0, default=%u, autoLogon=%d (reason: unsupported scenario %u) -> S_OK",
            *pdwDefault, *pbAutoLogonWithDefault, m_cpus);
        Logger::LogInfo(szBuf);
        return S_OK;
    }

    if (!m_pUserArray)
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::GetCredentialCount: early return count=0, default=%u, autoLogon=%d (reason: m_pUserArray is null) -> S_OK",
            *pdwDefault, *pbAutoLogonWithDefault);
        Logger::LogInfo(szBuf);
        return S_OK;
    }

    DWORD userIndex = 0;
    bool bLockedSession = false;
    HRESULT hr = ResolveLockedUser(&userIndex, &bLockedSession);
    if (hr != S_OK || !bLockedSession)
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::GetCredentialCount: early return count=0, default=%u, autoLogon=%d (reason: ResolveLockedUser returned hr=0x%08X, lockedSession=%d) -> S_OK",
            *pdwDefault, *pbAutoLogonWithDefault, hr, bLockedSession);
        Logger::LogInfo(szBuf);
        return S_OK;
    }

    ICredentialProviderUser* pUser = nullptr;
    hr = m_pUserArray->GetAt(userIndex, &pUser);
    if (FAILED(hr) || !pUser)
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::GetCredentialCount: early return count=0, default=%u, autoLogon=%d (reason: failed to get user %u hr=0x%08X) -> S_OK",
            *pdwDefault, *pbAutoLogonWithDefault, userIndex, hr);
        Logger::LogInfo(szBuf);
        return S_OK;
    }

    std::wstring sid;
    std::wstring username;
    std::wstring domain;

    // SID via GetSid (CoTaskMemFree when done)
    LPWSTR pszSid = nullptr;
    if (SUCCEEDED(pUser->GetSid(&pszSid)) && pszSid)
    {
        sid = pszSid;
        CoTaskMemFree(pszSid);
    }

    // username via GetStringValue(PKEY_Identity_UserName)
    LPWSTR pszUser = nullptr;
    if (SUCCEEDED(pUser->GetStringValue(PKEY_Identity_UserName, &pszUser)) && pszUser)
    {
        username = pszUser;
        CoTaskMemFree(pszUser);
    }

    // domain via GetStringValue(PKEY_Identity_DomainName)
    LPWSTR pszDomain = nullptr;
    if (SUCCEEDED(pUser->GetStringValue(PKEY_Identity_DomainName, &pszDomain)) && pszDomain)
    {
        domain = pszDomain;
        CoTaskMemFree(pszDomain);
    }

    // Fallback if domain is empty: query PKEY_Identity_QualifiedUserName ("DOMAIN\user")
    if (domain.empty())
    {
        LPWSTR pszQualified = nullptr;
        if (SUCCEEDED(pUser->GetStringValue(PKEY_Identity_QualifiedUserName, &pszQualified)) && pszQualified)
        {
            std::wstring qualified = pszQualified;
            size_t slashPos = qualified.find(L'\\');
            if (slashPos != std::wstring::npos)
            {
                domain = qualified.substr(0, slashPos);
                if (username.empty())
                {
                    username = qualified.substr(slashPos + 1);
                }
            }
            CoTaskMemFree(pszQualified);
        }
    }

    // If username contains "DOMAIN\user", split it
    size_t slashPos = username.find(L'\\');
    if (slashPos != std::wstring::npos)
    {
        if (domain.empty())
        {
            domain = username.substr(0, slashPos);
        }
        username = username.substr(slashPos + 1);
    }

    // If domain or username are still empty, query account info from binary SID
    if ((domain.empty() || username.empty()) && !sid.empty())
    {
        PSID pBinarySid = nullptr;
        if (ConvertStringSidToSidW(sid.c_str(), &pBinarySid))
        {
            wchar_t szName[256] = {};
            wchar_t szDomain[256] = {};
            DWORD cchName = ARRAYSIZE(szName);
            DWORD cchDomain = ARRAYSIZE(szDomain);
            SID_NAME_USE snu;
            if (LookupAccountSidW(NULL, pBinarySid, szName, &cchName, szDomain, &cchDomain, &snu))
            {
                if (username.empty()) username = szName;
                if (domain.empty()) domain = szDomain;
            }
            LocalFree(pBinarySid);
        }
    }

    pUser->Release();

    // Recreate the credential if the user context changed
    if (m_pCredential && (m_userSid != sid || m_userName != username || m_userDomain != domain))
    {
        ReleaseCredential();
    }

    m_userSid = sid;
    m_userName = username;
    m_userDomain = domain;

    // Allocate single Peek credential tile if not already created
    if (!m_pCredential)
    {
        m_pCredential = new (std::nothrow) PeekCredential();
        if (!m_pCredential)
        {
            *pdwCount = 0;
            Logger::LogError("PeekProvider::GetCredentialCount: out of memory allocating PeekCredential -> E_OUTOFMEMORY");
            return E_OUTOFMEMORY;
        }

        m_pCredential->SetProvider(this);
        hr = m_pCredential->Initialize(
            m_cpus,
            s_rgcpfd,
            PFI_COUNT,
            m_userName.empty() ? nullptr : m_userName.c_str(),
            m_userDomain.empty() ? nullptr : m_userDomain.c_str(),
            m_userSid.empty() ? nullptr : m_userSid.c_str(),
            bLockedSession
        );
        if (FAILED(hr))
        {
            ReleaseCredential();
            *pdwCount = 0;
            wchar_t szBuf[128] = {};
            swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekProvider::GetCredentialCount: credential Initialize failed -> hr=0x%08X", hr);
            Logger::LogError(szBuf);
            return hr;
        }
    }
    else
    {
        m_pCredential->SetProvider(this);
        m_pCredential->SetLockedSession(bLockedSession);
        m_pCredential->SetUserContext(
            m_userName.empty() ? nullptr : m_userName.c_str(),
            m_userDomain.empty() ? nullptr : m_userDomain.c_str(),
            m_userSid.empty() ? nullptr : m_userSid.c_str()
        );
    }

    // Constraint: Do not make Peek the default tile in this change (separate item).
    *pdwDefault = CREDENTIAL_PROVIDER_NO_DEFAULT;
    *pbAutoLogonWithDefault = FALSE;

    if (m_pCredential->IsAuthenticated())
    {
        *pdwDefault = 0;
        *pbAutoLogonWithDefault = TRUE;
    }

    *pdwCount = 1;

    wchar_t szBuf[512] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"PeekProvider::GetCredentialCount: count=%u, default=%u, autoLogon=%d, user='%s', domain='%s', SID='%s' -> S_OK",
        *pdwCount, *pdwDefault, *pbAutoLogonWithDefault,
        m_userName.c_str(), m_userDomain.c_str(), m_userSid.c_str());
    Logger::LogInfo(szBuf);

    return S_OK;
}

HRESULT PeekProvider::GetCredentialAt(DWORD dwIndex, ICredentialProviderCredential** ppcpc)
{
    if (!ppcpc)
    {
        Logger::LogError("PeekProvider::GetCredentialAt: ppcpc is null -> E_POINTER");
        return E_POINTER;
    }
    *ppcpc = nullptr;

    if (dwIndex != 0 || !m_pCredential)
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"PeekProvider::GetCredentialAt: dwIndex=%u, m_pCredential=%p -> E_INVALIDARG",
            dwIndex, m_pCredential);
        Logger::LogError(szBuf);
        return E_INVALIDARG;
    }

    HRESULT hr = m_pCredential->QueryInterface(IID_ICredentialProviderCredential, reinterpret_cast<void**>(ppcpc));
    wchar_t szBuf[256] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"PeekProvider::GetCredentialAt: dwIndex=%u, ppcpc=%p -> hr=0x%08X",
        dwIndex, *ppcpc, hr);
    Logger::LogInfo(szBuf);
    return hr;
}
