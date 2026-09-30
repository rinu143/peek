// Peek Face Engine - Credential Implementation
#include "PeekCredential.h"
#include "PeekProvider.h"
#include "Serialization.h"
#include "resource.h"
#include "SecretVault.h"

PeekCredential::PeekCredential()
    : m_cRef(1)
    , m_cpus(CPUS_LOGON)
    , m_pcpce(nullptr)
    , m_pProvider(nullptr)
    , m_isAuthenticated(false)
    , m_isSelected(false)
    , m_displayName(L"Peek Facial Recognition")
    , m_statusText(L"Looking for face...")
    , m_hRetryCancelEvent(NULL)
{
    InitializeCriticalSection(&m_cs);
    m_hRetryCancelEvent = CreateEventW(NULL, TRUE, FALSE, NULL);

    // Initialize TileAnimator with a frame update callback that notifies LogonUI via SetFieldBitmap
    m_animator.Initialize(g_hinst, [this](HBITMAP hbmp) {
        EnterCriticalSection(&m_cs);
        ICredentialProviderCredentialEvents* pcpce = m_pcpce;
        if (pcpce) pcpce->AddRef();
        LeaveCriticalSection(&m_cs);

        if (pcpce)
        {
            HBITMAP hCopy = static_cast<HBITMAP>(CopyImage(hbmp, IMAGE_BITMAP, 0, 0, LR_CREATEDIBSECTION));
            pcpce->SetFieldBitmap(this, PFI_LOGO, hCopy ? hCopy : hbmp);
            pcpce->Release();
        }
    });

    DllAddRef();
}

PeekCredential::~PeekCredential()
{
    if (m_hRetryCancelEvent)
    {
        SetEvent(m_hRetryCancelEvent);
        CloseHandle(m_hRetryCancelEvent);
        m_hRetryCancelEvent = NULL;
    }

    m_pipeClient.Cancel();
    m_animator.Shutdown();

    if (m_pcpce)
    {
        m_pcpce->Release();
        m_pcpce = nullptr;
    }
    DeleteCriticalSection(&m_cs);
    DllRelease();
}

void PeekCredential::SetProvider(PeekProvider* pProvider)
{
    EnterCriticalSection(&m_cs);
    m_pProvider = pProvider;
    LeaveCriticalSection(&m_cs);
}

bool PeekCredential::IsAuthenticated() const
{
    EnterCriticalSection(&const_cast<CRITICAL_SECTION&>(m_cs));
    bool auth = m_isAuthenticated;
    LeaveCriticalSection(&const_cast<CRITICAL_SECTION&>(m_cs));
    return auth;
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
    m_isSelected = false;
    if (m_hRetryCancelEvent)
    {
        SetEvent(m_hRetryCancelEvent);
    }
    m_pipeClient.Cancel();
    if (m_pcpce)
    {
        m_pcpce->Release();
        m_pcpce = nullptr;
    }
    LeaveCriticalSection(&m_cs);
    m_animator.SetState(PeekUiState::IDLE);
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
    if (m_hRetryCancelEvent)
    {
        ResetEvent(m_hRetryCancelEvent);
    }
    LeaveCriticalSection(&m_cs);

    // Initial state presentation: searching animation and status text
    m_animator.SetState(PeekUiState::SEARCHING);

    if (m_pcpce)
    {
        m_pcpce->SetFieldString(this, PFI_STATUS_TEXT, m_statusText.c_str());
    }

    // Launch background asynchronous pipe authentication
    Logger::LogInfo("Starting background authentication with StartAuthAsync");
    m_pipeClient.StartAuthAsync([this](const PeekAuthResult& result) {
        this->OnEngineStateUpdate(result);
    }, 12.0f);

    return S_OK;
}

HRESULT PeekCredential::SetDeselected()
{
    EnterCriticalSection(&m_cs);
    m_isSelected = false;
    if (m_hRetryCancelEvent)
    {
        SetEvent(m_hRetryCancelEvent);
    }
    m_pipeClient.Cancel();
    LeaveCriticalSection(&m_cs);
    m_animator.SetState(PeekUiState::IDLE);
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
        // 1. Return current 32bpp ARGB frame from TileAnimator if available
        HBITMAP hFrame = m_animator.GetCurrentFrame();
        if (hFrame)
        {
            *phbmp = static_cast<HBITMAP>(CopyImage(hFrame, IMAGE_BITMAP, 0, 0, LR_CREATEDIBSECTION));
            if (*phbmp) return S_OK;
        }

        // 2. Fail-soft fallback to static embedded bitmap if TileAnimator has no frames
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
                    
                    // Directly package standard Kerberos/Negotiate interactive logon with the retrieved password.
                    // The redundant LogonUserW pre-check is removed so Winlogon is the single authority on password validity.
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

#ifndef STATUS_LOGON_FAILURE
#define STATUS_LOGON_FAILURE ((NTSTATUS)0xC000006DL)
#endif
#ifndef STATUS_WRONG_PASSWORD
#define STATUS_WRONG_PASSWORD ((NTSTATUS)0xC000006AL)
#endif
#ifndef STATUS_PASSWORD_EXPIRED
#define STATUS_PASSWORD_EXPIRED ((NTSTATUS)0xC0000071L)
#endif

HRESULT PeekCredential::ReportResult(
    NTSTATUS ntsStatus,
    NTSTATUS ntsSubstatus,
    PWSTR* /*ppszOptionalStatusText*/,
    CREDENTIAL_PROVIDER_STATUS_ICON* /*pcpsiOptionalStatusIcon*/
)
{
    EnterCriticalSection(&m_cs);
    std::wstring profileId = m_profileId;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus = m_cpus;
    LeaveCriticalSection(&m_cs);

    // If Winlogon reports bad credentials (STATUS_LOGON_FAILURE, STATUS_WRONG_PASSWORD,
    // or STATUS_PASSWORD_EXPIRED), delete the stale stored secret for this profile/session
    // so a stale linked password doesn't keep silently failing on subsequent unlock attempts.
    if (ntsStatus == STATUS_LOGON_FAILURE ||
        ntsStatus == STATUS_WRONG_PASSWORD ||
        ntsStatus == STATUS_PASSWORD_EXPIRED ||
        ntsSubstatus == STATUS_WRONG_PASSWORD ||
        ntsSubstatus == STATUS_LOGON_FAILURE ||
        ntsSubstatus == STATUS_PASSWORD_EXPIRED)
    {
        if (!profileId.empty() && cpus == CPUS_UNLOCK_WORKSTATION)
        {
            Logger::LogInfo(L"ReportResult received authentication failure from Winlogon; deleting stale secret");
            DWORD sessionId = WTSGetActiveConsoleSessionId();
            PeekSecretVault::DeleteWrappedSecret(profileId.c_str(), sessionId);
        }
    }

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

void PeekCredential::NotifyCredentialsReady()
{
    EnterCriticalSection(&m_cs);
    PeekProvider* pProvider = m_pProvider;
    LeaveCriticalSection(&m_cs);

    if (pProvider)
    {
        pProvider->NotifyCredentialsChanged();
    }
}

void PeekCredential::OnEngineStateUpdate(const PeekAuthResult& result)
{
    switch (result.state)
    {
    case PeekIPCState::CONNECTING:
        UpdateStatusText(L"Connecting to Peek Face Engine...");
        m_animator.SetState(PeekUiState::IDLE);
        break;

    case PeekIPCState::ENGINE_READY:
    case PeekIPCState::CAMERA_STARTING:
        UpdateStatusText(L"Acquiring camera device...");
        m_animator.SetState(PeekUiState::SEARCHING);
        break;

    case PeekIPCState::SEARCHING:
        UpdateStatusText(L"Looking for face...");
        m_animator.SetState(PeekUiState::SEARCHING);
        break;

    case PeekIPCState::FACE_FOUND:
        UpdateStatusText(L"Face detected — verifying...");
        m_animator.SetState(PeekUiState::FACE_FOUND);
        break;

    case PeekIPCState::VERIFYING:
        UpdateStatusText(result.detail.empty() ? L"Verifying biometric match..." : result.detail);
        m_animator.SetState(PeekUiState::VERIFYING);
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
        m_animator.SetState(PeekUiState::LIVENESS);
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
            std::wstring enrolledName = m_displayName;
            std::wstring rawUser = m_username;
            LeaveCriticalSection(&m_cs);

            // Dynamic Greeting Copy:
            // "when AUTHENTICATED arrives, set status text to
            //  L"Hello, " + m_displayName + L"!" if m_displayName is non-empty and not
            //  the placeholder "Peek Facial Recognition"/raw username fallback; if no
            //  real enrolled display name is available, use "Welcome back" — never a
            //  hardcoded example name."
            std::wstring greeting;
            if (!enrolledName.empty() &&
                enrolledName != L"Peek Facial Recognition" &&
                enrolledName != rawUser)
            {
                greeting = L"Hello, " + enrolledName + L"!";
            }
            else
            {
                greeting = L"Welcome back";
            }

            UpdateStatusText(greeting);
            m_animator.SetState(PeekUiState::SUCCESS);

            // SECURITY INVARIANT ENFORCEMENT & TIMING:
            // The SUCCESS presentation delay (~750ms) applies strictly AFTER the
            // authentication dual-gate has verified isAuthorizedToUnlock == true and
            // m_isAuthenticated == true under m_cs lock.
            //
            // NOTE ON INVARIANT ENFORCEMENT:
            // The debug assert() previously placed here was not load-bearing (assert()
            // compiles away to a no-op under NDEBUG in production Release builds).
            // The real, load-bearing invariant enforcement is (and remains) the check in GetSerialization:
            //   `if (!isAuth) return CPGSR_NO_CREDENTIAL_FINISHED;`
            // which guarantees credentials can NEVER be serialized or returned unless
            // the face engine strictly authenticated the user and authorized unlock.
            // As defense-in-depth, we also perform an explicit runtime check here to
            // safely abort signaling if authorization state was somehow violated:
            EnterCriticalSection(&m_cs);
            bool isAuthConfirmed = m_isAuthenticated;
            LeaveCriticalSection(&m_cs);

            if (!result.isAuthorizedToUnlock || !isAuthConfirmed)
            {
                Logger::LogError("Security invariant violation: AUTHENTICATED reached without confirmed unlock authorization; aborting");
                return;
            }

            Sleep(750);

            Logger::LogInfo("AUTHENTICATED received - signaling CredentialsChanged");
            NotifyCredentialsReady();
        }
        break;

    case PeekIPCState::AUTH_FAILED:
        // Calm failure treatment: sad icon + "Try again" phrasing, no alarming visuals
        UpdateStatusText(L"Authentication failed. Try again or sign in with password/PIN.");
        m_animator.SetState(PeekUiState::FAILURE);
        QueueRetry();
        break;

    case PeekIPCState::TIMEOUT:
        // Calm failure treatment: sad icon + "Try again" phrasing
        UpdateStatusText(L"Timed out. Try again or sign in with password.");
        m_animator.SetState(PeekUiState::FAILURE);
        QueueRetry();
        break;

    case PeekIPCState::DISCONNECTED:
    case PeekIPCState::ERROR_STATE:
    default:
        // Fail-open: calm failure icon, inform user to sign in with password/PIN
        UpdateStatusText(L"Peek engine offline. Sign in with password or PIN.");
        m_animator.SetState(PeekUiState::FAILURE);
        break;
    }
}

void PeekCredential::QueueRetry()
{
    EnterCriticalSection(&m_cs);
    bool canRetry = m_isSelected && !m_isAuthenticated;
    LeaveCriticalSection(&m_cs);
    if (!canRetry) return;

    // Launch asynchronous retry thread so the pipe client worker thread can cleanly exit
    AddRef();
    HANDLE hRetry = CreateThread(nullptr, 0, [](LPVOID param) -> DWORD {
        PeekCredential* pThis = static_cast<PeekCredential*>(param);
        pThis->RunRetrySequence();
        pThis->Release();
        return 0;
    }, this, 0, nullptr);

    if (hRetry)
    {
        CloseHandle(hRetry);
    }
    else
    {
        Release();
    }
}

void PeekCredential::RunRetrySequence()
{
    // 1. Hold calm failure presentation for cooldown period (~1200ms)
    if (WaitForSingleObject(m_hRetryCancelEvent, 1200) != WAIT_TIMEOUT)
    {
        return; // cancelled or deselected
    }

    EnterCriticalSection(&m_cs);
    bool stillActive = m_isSelected && !m_isAuthenticated;
    LeaveCriticalSection(&m_cs);
    if (!stillActive) return;

    // 2. Explicit RETRY beat between FAILURE and resetting to SEARCHING
    UpdateStatusText(L"Try again...");
    m_animator.SetState(PeekUiState::RETRY);

    if (WaitForSingleObject(m_hRetryCancelEvent, 600) != WAIT_TIMEOUT)
    {
        return; // cancelled or deselected
    }

    EnterCriticalSection(&m_cs);
    stillActive = m_isSelected && !m_isAuthenticated;
    LeaveCriticalSection(&m_cs);
    if (!stillActive) return;

    // 3. Return to SEARCHING state and restart pipe authentication
    UpdateStatusText(L"Looking for face...");
    m_animator.SetState(PeekUiState::SEARCHING);

    m_pipeClient.StartAuthAsync([this](const PeekAuthResult& res) {
        this->OnEngineStateUpdate(res);
    }, 12.0f);
}
