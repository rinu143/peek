// Peek Face Engine - Credential Provider Main Class Implementation
#include "PeekProvider.h"
#include <sddl.h>

static const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR s_rgcpfd[PFI_COUNT] =
{
    { PFI_LOGO,         CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Peek Biometrics") },
    { PFI_DISPLAY_NAME, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"User") },
    { PFI_STATUS_TEXT,  CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status") },
};

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

    if (riid == IID_IUnknown || riid == IID_ICredentialProvider)
    {
        *ppv = static_cast<ICredentialProvider*>(this);
        AddRef();
        return S_OK;
    }
    else if (riid == IID_ICredentialProviderSetUserArray)
    {
        *ppv = static_cast<ICredentialProviderSetUserArray*>(this);
        AddRef();
        return S_OK;
    }

    return E_NOINTERFACE;
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

    std::string logMsg = "PeekProvider::SetUsageScenario: scenario=" + std::to_string(cpus) +
                         ", flags=" + std::to_string(dwFlags);
    Logger::LogInfo(logMsg.c_str());

    switch (cpus)
    {
    case CPUS_UNLOCK_WORKSTATION:
        return S_OK;

    case CPUS_LOGON:
    case CPUS_CREDUI:
        // Before first interactive sign-in the user's profile/DPAPI master key
        // is not reliably available. Do not advertise a credential tile here.
        return E_NOTIMPL;

    case CPUS_CHANGE_PASSWORD:
    default:
        // Biometrics cannot perform password change; gracefully decline scenario
        return E_NOTIMPL;
    }
}

HRESULT PeekProvider::SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* /*pcpcs*/)
{
    // Return S_OK so LogonUI retains the provider for the workstation unlock scenario
    return S_OK;
}

HRESULT PeekProvider::Advise(ICredentialProviderEvents* pcpe, UINT_PTR upAdviseContext)
{
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
    return S_OK;
}

HRESULT PeekProvider::UnAdvise()
{
    if (m_pcpe)
    {
        m_pcpe->Release();
        m_pcpe = nullptr;
    }
    m_upAdviseContext = 0;
    ReleaseUserArray();
    return S_OK;
}

HRESULT PeekProvider::GetFieldDescriptorCount(DWORD* pdwCount)
{
    if (!pdwCount) return E_POINTER;
    *pdwCount = PFI_COUNT;
    return S_OK;
}

HRESULT PeekProvider::GetFieldDescriptorAt(DWORD dwIndex, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd)
{
    if (!ppcpfd) return E_POINTER;
    *ppcpfd = nullptr;

    if (dwIndex >= PFI_COUNT)
    {
        return E_INVALIDARG;
    }

    return FieldDescriptorCoTaskAlloc(s_rgcpfd[dwIndex], ppcpfd);
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

HRESULT PeekProvider::GetCredentialCount(
    DWORD* pdwCount,
    DWORD* pdwDefault,
    BOOL* pbAutoLogonWithDefault
)
{
    if (!pdwCount || !pdwDefault || !pbAutoLogonWithDefault) return E_POINTER;

    *pdwCount = 0;
    *pdwDefault = CREDENTIAL_PROVIDER_NO_DEFAULT;
    *pbAutoLogonWithDefault = FALSE;

    if (m_cpus != CPUS_UNLOCK_WORKSTATION)
    {
        std::string logMsg = "PeekProvider::GetCredentialCount: count=0, default=" +
                             std::to_string(*pdwDefault) + " (non-unlock scenario)";
        Logger::LogInfo(logMsg.c_str());
        return S_OK;
    }

    if (!m_pUserArray)
    {
        std::string logMsg = "PeekProvider::GetCredentialCount: count=0, default=" +
                             std::to_string(*pdwDefault) + " (m_pUserArray is null)";
        Logger::LogInfo(logMsg.c_str());
        return S_OK;
    }

    DWORD dwUserCount = 0;
    HRESULT hr = m_pUserArray->GetCount(&dwUserCount);
    if (FAILED(hr) || dwUserCount == 0)
    {
        std::string logMsg = "PeekProvider::GetCredentialCount: count=0, default=" +
                             std::to_string(*pdwDefault) + " (user count is 0)";
        Logger::LogInfo(logMsg.c_str());
        return S_OK;
    }

    ICredentialProviderUser* pUser = nullptr;
    hr = m_pUserArray->GetAt(0, &pUser);
    if (FAILED(hr) || !pUser)
    {
        std::string logMsg = "PeekProvider::GetCredentialCount: count=0, default=" +
                             std::to_string(*pdwDefault) + " (failed to get user 0)";
        Logger::LogInfo(logMsg.c_str());
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
            return E_OUTOFMEMORY;
        }

        m_pCredential->SetProvider(this);
        hr = m_pCredential->Initialize(
            m_cpus,
            s_rgcpfd,
            PFI_COUNT,
            m_userName.empty() ? nullptr : m_userName.c_str(),
            m_userDomain.empty() ? nullptr : m_userDomain.c_str(),
            m_userSid.empty() ? nullptr : m_userSid.c_str()
        );
        if (FAILED(hr))
        {
            ReleaseCredential();
            *pdwCount = 0;
            return hr;
        }
    }
    else
    {
        m_pCredential->SetProvider(this);
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

    std::string logMsg = "PeekProvider::GetCredentialCount: count=" + std::to_string(*pdwCount) +
                         ", default=" + std::to_string(*pdwDefault);
    Logger::LogInfo(logMsg.c_str());

    return S_OK;
}

HRESULT PeekProvider::GetCredentialAt(DWORD dwIndex, ICredentialProviderCredential** ppcpc)
{
    std::string logMsg = "PeekProvider::GetCredentialAt: dwIndex=" + std::to_string(dwIndex);
    Logger::LogInfo(logMsg.c_str());

    if (!ppcpc) return E_POINTER;
    *ppcpc = nullptr;

    if (dwIndex != 0 || !m_pCredential)
    {
        return E_INVALIDARG;
    }

    return m_pCredential->QueryInterface(IID_ICredentialProviderCredential, reinterpret_cast<void**>(ppcpc));
}
