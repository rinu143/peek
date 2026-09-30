// Peek Face Engine - Credential Provider Main Class Implementation
#include "PeekProvider.h"

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
{
    DllAddRef();
}

PeekProvider::~PeekProvider()
{
    ReleaseCredential();
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
    return E_NOTIMPL;
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

HRESULT PeekProvider::GetCredentialCount(
    DWORD* pdwCount,
    DWORD* pdwDefault,
    BOOL* pbAutoLogonWithDefault
)
{
    if (!pdwCount || !pdwDefault || !pbAutoLogonWithDefault) return E_POINTER;

    *pdwDefault = CREDENTIAL_PROVIDER_NO_DEFAULT;
    *pbAutoLogonWithDefault = FALSE;

    if (m_cpus == CPUS_CHANGE_PASSWORD)
    {
        *pdwCount = 0;
        return S_OK;
    }

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
        HRESULT hr = m_pCredential->Initialize(m_cpus, s_rgcpfd, PFI_COUNT);
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
    }

    if (m_pCredential->IsAuthenticated())
    {
        *pdwDefault = 0;
        *pbAutoLogonWithDefault = TRUE;
    }

    *pdwCount = 1;
    return S_OK;
}

HRESULT PeekProvider::GetCredentialAt(DWORD dwIndex, ICredentialProviderCredential** ppcpc)
{
    if (!ppcpc) return E_POINTER;
    *ppcpc = nullptr;

    if (dwIndex != 0 || !m_pCredential)
    {
        return E_INVALIDARG;
    }

    return m_pCredential->QueryInterface(IID_ICredentialProviderCredential, reinterpret_cast<void**>(ppcpc));
}
