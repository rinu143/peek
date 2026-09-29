// Peek Face Engine - Credential Implementation
#include "PeekCredential.h"
#include "Serialization.h"
#include "resource.h"
#include "SecretVault.h"

PeekCredential::PeekCredential()
    : m_cRef(1)
    , m_cpus(CPUS_LOGON)
    , m_pcpce(nullptr)
    , m_isAuthenticated(false)
    , m_isSelected(false)
    , m_displayName(L"Peek Facial Recognition")
    , m_statusText(L"Looking for face...")
{
    InitializeCriticalSection(&m_cs);
    DllAddRef();
}

PeekCredential::~PeekCredential()
{
    m_pipeClient.Cancel();
    if (m_pcpce)
    {
        m_pcpce->Release();
        m_pcpce = nullptr;
    }
    DeleteCriticalSection(&m_cs);
    DllRelease();
}

HRESULT PeekCredential::Initialize(
    CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
    const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR* /*rgcpfd*/,
    DWORD /*dwFieldCount*/,
    PCWSTR pszUsername,
    PCWSTR pszDomain,
    PCWSTR pszSid
)
{
    m_cpus = cpus;
    if (pszUsername) m_username = pszUsername;
    if (pszDomain) m_domain = pszDomain;
    if (pszSid) m_sid = pszSid;

    if (!m_username.empty())
    {
        m_displayName = m_username;
    }

    return S_OK;
}

// --- IUnknown ---

HRESULT PeekCredential::QueryInterface(REFIID riid, void** ppv)
{
    if (!ppv) return E_POINTER;
    *ppv = nullptr;

    if (riid == IID_IUnknown || riid == IID_ICredentialProviderCredential)
    {
        *ppv = static_cast<ICredentialProviderCredential*>(this);
    }
    else if (riid == IID_ICredentialProviderCredential2)
    {
        *ppv = static_cast<ICredentialProviderCredential2*>(this);
    }
    else
    {
        return E_NOINTERFACE;
    }

    AddRef();
    return S_OK;
}

ULONG PeekCredential::AddRef()
{
    return InterlockedIncrement(&m_cRef);
}

ULONG PeekCredential::Release()
{
    LONG cRef = InterlockedDecrement(&m_cRef);
    if (cRef == 0)
    {
        delete this;
    }
    return cRef;
}

// --- ICredentialProviderCredential ---

HRESULT PeekCredential::Advise(ICredentialProviderCredentialEvents* pcpce)
{
    EnterCriticalSection(&m_cs);
    if (m_pcpce)
    {
        m_pcpce->Release();
    }
    m_pcpce = pcpce;
    if (m_pcpce)
    {
        m_pcpce->AddRef();
    }
    LeaveCriticalSection(&m_cs);
    return S_OK;
}

HRESULT PeekCredential::UnAdvise()
{
    EnterCriticalSection(&m_cs);
    m_pipeClient.Cancel();
    if (m_pcpce)
    {
        m_pcpce->Release();
        m_pcpce = nullptr;
    }
    LeaveCriticalSection(&m_cs);
    return S_OK;
}

HRESULT PeekCredential::SetSelected(BOOL* pbAutoLogon)
{
    Logger::LogInfo("PeekCredential::SetSelected called");
    
    if (pbAutoLogon)
    {
        *pbAutoLogon = FALSE;
    }

    EnterCriticalSection(&m_cs);
    m_isSelected = true;
    m_isAuthenticated = false;
    m_statusText = L"Initializing camera...";
    LeaveCriticalSection(&m_cs);

    // Notify LogonUI of initial status text
    if (m_pcpce)
    {
        m_pcpce->SetFieldString(this, PFI_STATUS_TEXT, m_statusText.c_str());
    }

    // Launch background asynchronous pipe authentication
    Logger::LogInfo("Starting background authentication with StartAuthAsync");
    Logger::LogInfo("About to call m_pipeClient.StartAuthAsync");
    m_pipeClient.StartAuthAsync([this](const PeekAuthResult& result) {
        this->OnEngineStateUpdate(result);
    }, 12.0f);
    Logger::LogInfo("m_pipeClient.StartAuthAsync completed");

    return S_OK;
}

HRESULT PeekCredential::SetDeselected()
{
    EnterCriticalSection(&m_cs);
    m_isSelected = false;
    m_pipeClient.Cancel();
    LeaveCriticalSection(&m_cs);
    return S_OK;
}

HRESULT PeekCredential::GetFieldState(
    DWORD dwFieldID,
    CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs,
    CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis
)
{
    if (!pcpfs || !pcpfis) return E_POINTER;

    *pcpfs = CPFS_DISPLAY_IN_SELECTED_TILE;
    *pcpfis = CPFIS_NONE;

    if (dwFieldID >= PFI_COUNT)
    {
        *pcpfs = CPFS_HIDDEN;
    }

    return S_OK;
}

HRESULT PeekCredential::GetStringValue(DWORD dwFieldID, PWSTR* ppsz)
{
    if (!ppsz) return E_POINTER;
    *ppsz = nullptr;

    EnterCriticalSection(&m_cs);
    HRESULT hr = S_OK;

    switch (dwFieldID)
    {
    case PFI_DISPLAY_NAME:
        hr = CoTaskMemAllocString(m_displayName.c_str(), ppsz);
        break;

    case PFI_STATUS_TEXT:
        hr = CoTaskMemAllocString(m_statusText.c_str(), ppsz);
        break;

    default:
        hr = E_INVALIDARG;
        break;
    }

    LeaveCriticalSection(&m_cs);
    return hr;
}

HRESULT PeekCredential::GetBitmapValue(DWORD dwFieldID, HBITMAP* phbmp)
{
    if (!phbmp) return E_POINTER;
    *phbmp = nullptr;

    if (dwFieldID == PFI_LOGO)
    {
        // Load embedded Peek tile bitmap if present
        *phbmp = LoadBitmapW(g_hinst, MAKEINTRESOURCEW(IDB_PEEK_LOGO));
        return S_OK;
    }

    return E_INVALIDARG;
}

HRESULT PeekCredential::GetCheckboxValue(DWORD /*dwFieldID*/, BOOL* /*pbChecked*/, PWSTR* /*ppszLabel*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::GetSubmitButtonValue(DWORD /*dwFieldID*/, DWORD* /*pdwAdjacentTo*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::GetComboBoxValueCount(DWORD /*dwFieldID*/, DWORD* /*pcItems*/, DWORD* /*pdwSelectedItem*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::GetComboBoxValueAt(DWORD /*dwFieldID*/, DWORD /*dwItem*/, PWSTR* /*ppszItem*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::SetStringValue(DWORD /*dwFieldID*/, PCWSTR /*psz*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::SetCheckboxValue(DWORD /*dwFieldID*/, BOOL /*bChecked*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::SetComboBoxSelectedValue(DWORD /*dwFieldID*/, DWORD /*dwSelectedItem*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::CommandLinkClicked(DWORD /*dwFieldID*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::GetSerialization(
    CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs,
    PWSTR* ppszOptionalStatusText,
    CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon
)
{
    if (!pcpgsr || !pcpcs) return E_POINTER;
    *pcpgsr = CPGSR_NO_CREDENTIAL_FINISHED;
    ZeroMemory(pcpcs, sizeof(*pcpcs));

    if (ppszOptionalStatusText) *ppszOptionalStatusText = nullptr;
    if (pcpsiOptionalStatusIcon) *pcpsiOptionalStatusIcon = CPSI_NONE;

    EnterCriticalSection(&m_cs);
    bool isAuth = m_isAuthenticated;
    std::wstring user = m_username;
    std::wstring domain = m_domain;
    std::wstring profileId = m_profileId;
    LeaveCriticalSection(&m_cs);

    // NON-NEGOTIABLE SECURITY INVARIANT:
    // Only return credentials if authentication strictly succeeded via the engine.
    if (!isAuth)
    {
        *pcpgsr = CPGSR_NO_CREDENTIAL_FINISHED;
        return S_OK;
    }

    // For unlock scenarios, try to retrieve password from secret vault
    if (m_cpus == CPUS_UNLOCK_WORKSTATION)
    {
        Logger::LogInfo(L"GetSerialization for unlock scenario - attempting to retrieve password from secret vault");

        // Try to decrypt a wrapped secret file that should contain the password
        BYTE buffer[1024] = {};
        DWORD cbActual = 0;
        DWORD sessionId = WTSGetActiveConsoleSessionId();
        
        if (!profileId.empty() && PeekSecretVault::DecryptWrappedSecret(profileId.c_str(), sessionId, buffer, sizeof(buffer), &cbActual))
        {
            Logger::LogInfo(L"Successfully decrypted secret from vault");
            
            // Secret plaintext is [DWORD UTF-16LE byte length][UTF-16LE bytes], never a C string.
            if (cbActual >= sizeof(DWORD))
            {
                DWORD passwordBytes = 0;
                memcpy(&passwordBytes, buffer, sizeof(passwordBytes));
                if (passwordBytes <= cbActual - sizeof(DWORD) &&
                    passwordBytes % sizeof(wchar_t) == 0)
                {
                    std::wstring password(reinterpret_cast<LPCWSTR>(buffer + sizeof(DWORD)), passwordBytes / sizeof(wchar_t));
                // Try to validate the password using LogonUser
                if (PeekSecretVault::ValidateWindowsPassword(user.c_str(), domain.c_str(), 
                    password.c_str()))
                {
                    Logger::LogInfo(L"Password validation successful - proceeding with serialization");
                    
                    // Package standard Kerberos/Negotiate interactive logon with the retrieved password
                    HRESULT hr = PeekSerialization::PackageKerbLogon(
                        domain.c_str(),
                        user.c_str(),
                        password.c_str(),
                        pcpcs
                    );
                    if (!password.empty())
                    {
                        SecureZeroMemory(&password[0], password.size() * sizeof(wchar_t));
                    }
                    SecureZeroMemory(buffer, sizeof(buffer));
                    if (SUCCEEDED(hr))
                    {
                        *pcpgsr = CPGSR_RETURN_CREDENTIAL_FINISHED;
                        return S_OK;
                    }
                }
                else
                {
                    Logger::LogInfo(L"Wrapped secret validation failed; deleting stale secret");
                    PeekSecretVault::DeleteWrappedSecret(profileId.c_str(), sessionId);
                }
                if (!password.empty())
                {
                    SecureZeroMemory(&password[0], password.size() * sizeof(wchar_t));
                }
                SecureZeroMemory(buffer, sizeof(buffer));
                }
            }
        }
        SecureZeroMemory(buffer, sizeof(buffer));
        
        Logger::LogInfo(L"Failed to get valid password from secret vault - falling back to standard authentication");
    }

    // Never serialize an empty password. Standard providers remain available
    // for fail-open password/PIN sign-in when no valid linked secret exists.
    *pcpgsr = CPGSR_NO_CREDENTIAL_FINISHED;
    return S_OK;
}

HRESULT PeekCredential::ReportResult(
    NTSTATUS /*ntsStatus*/,
    NTSTATUS /*ntsSubstatus*/,
    PWSTR* /*ppszOptionalStatusText*/,
    CREDENTIAL_PROVIDER_STATUS_ICON* /*pcpsiOptionalStatusIcon*/
)
{
    return S_OK;
}

// --- ICredentialProviderCredential2 ---

HRESULT PeekCredential::GetUserSid(PWSTR* ppszSid)
{
    if (!ppszSid) return E_POINTER;
    *ppszSid = nullptr;

    if (!m_sid.empty())
    {
        return CoTaskMemAllocString(m_sid.c_str(), ppszSid);
    }
    return E_NOTIMPL;
}

void PeekCredential::UpdateStatusText(const std::wstring& newText)
{
    EnterCriticalSection(&m_cs);
    m_statusText = newText;
    ICredentialProviderCredentialEvents* pcpce = m_pcpce;
    if (pcpce) pcpce->AddRef();
    LeaveCriticalSection(&m_cs);

    if (pcpce)
    {
        pcpce->SetFieldString(this, PFI_STATUS_TEXT, newText.c_str());
        pcpce->Release();
    }
}

void PeekCredential::OnEngineStateUpdate(const PeekAuthResult& result)
{
    switch (result.state)
    {
    case PeekIPCState::CONNECTING:
        UpdateStatusText(L"Connecting to Peek Face Engine...");
        break;

    case PeekIPCState::ENGINE_READY:
    case PeekIPCState::CAMERA_STARTING:
        UpdateStatusText(L"Acquiring camera device...");
        break;

    case PeekIPCState::SEARCHING:
        UpdateStatusText(L"Looking for face...");
        break;

    case PeekIPCState::FACE_FOUND:
        UpdateStatusText(L"Face detected — verifying...");
        break;

    case PeekIPCState::VERIFYING:
        UpdateStatusText(result.detail.empty() ? L"Verifying biometric match..." : result.detail);
        break;

    case PeekIPCState::LIVENESS_CHECK:
        if (!result.promptText.empty())
        {
            UpdateStatusText(L"Action Required: " + result.promptText);
        }
        else
        {
            UpdateStatusText(L"Checking liveness...");
        }
        break;

    case PeekIPCState::AUTHENTICATED:
        if (result.isAuthorizedToUnlock)
        {
            EnterCriticalSection(&m_cs);
            m_isAuthenticated = true;
            if (!result.displayName.empty())
            {
                m_displayName = result.displayName;
            }
            if (!result.profileId.empty())
            {
                m_profileId = result.profileId;
            }
            LeaveCriticalSection(&m_cs);

            UpdateStatusText(L"Verified! Signing in...");
            
            // Log that we're about to signal Windows logon process
            Logger::LogInfo("AUTHENTICATED received - About to call CredentialsChanged");

            // Signal LogonUI that credentials are confirmed and ready
            EnterCriticalSection(&m_cs);
            ICredentialProviderCredentialEvents* pcpce = m_pcpce;
            if (pcpce) pcpce->AddRef();
            LeaveCriticalSection(&m_cs);

            if (pcpce)
            {
                // Complete the authentication process by signaling that this credential is ready
                // This should trigger Windows to proceed with the unlock
                Logger::LogInfo("Calling CredentialsChanged to signal Windows logon");
                pcpce->CredentialsChanged(this);
                pcpce->Release();
                Logger::LogInfo("CredentialsChanged completed successfully");
            }
        }
        break;

    case PeekIPCState::AUTH_FAILED:
        UpdateStatusText(L"Authentication failed. Please use password/PIN.");
        break;

    case PeekIPCState::TIMEOUT:
        UpdateStatusText(L"Timed out. Select tile to retry or sign in with password.");
        break;

    case PeekIPCState::DISCONNECTED:
    case PeekIPCState::ERROR_STATE:
    default:
        // Fail-open: inform user clearly to sign in with password/PIN
        UpdateStatusText(L"Peek engine offline. Sign in with password or PIN.");
        break;
    }
}
