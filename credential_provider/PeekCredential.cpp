// Peek Face Engine - Credential Implementation
#include "PeekCredential.h"
#include "PeekProvider.h"
#include "Serialization.h"
#include "resource.h"
#include "SecretVault.h"

PeekCredential::PeekCredential()
    : m_cRef(1)
    , m_cpus(CPUS_LOGON)
    , m_lockedSession(false)
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
    PCWSTR pszSid,
    bool bLockedSession
)
{
    m_cpus = cpus;
    m_lockedSession = bLockedSession;
    if (pszUsername) m_username = pszUsername;
    if (pszDomain) m_domain = pszDomain;
    if (pszSid) m_sid = pszSid;

    if (!m_username.empty())
    {
        m_displayName = m_username;
    }

    return S_OK;
}

void PeekCredential::SetLockedSession(bool bLockedSession)
{
    EnterCriticalSection(&m_cs);
    m_lockedSession = bLockedSession;
    LeaveCriticalSection(&m_cs);
}

bool PeekCredential::IsLockedSession() const
{
    EnterCriticalSection(&const_cast<CRITICAL_SECTION&>(m_cs));
    bool locked = m_lockedSession;
    LeaveCriticalSection(&const_cast<CRITICAL_SECTION&>(m_cs));
    return locked;
}

void PeekCredential::SetUserContext(
    PCWSTR pszUsername,
    PCWSTR pszDomain,
    PCWSTR pszSid
)
{
    EnterCriticalSection(&m_cs);
    if (pszUsername && *pszUsername)
    {
        m_username = pszUsername;
        if (m_displayName == L"Peek Facial Recognition" || m_displayName.empty())
        {
            m_displayName = pszUsername;
        }
    }
    if (pszDomain && *pszDomain)
    {
        m_domain = pszDomain;
    }
    if (pszSid && *pszSid)
    {
        m_sid = pszSid;
    }
    LeaveCriticalSection(&m_cs);
}

// --- IUnknown ---

HRESULT PeekCredential::QueryInterface(REFIID riid, void** ppv)
{
    if (!ppv) return E_POINTER;
    *ppv = nullptr;

    std::wstring iidStr = GuidToString(riid);
    HRESULT hr = E_NOINTERFACE;

    if (riid == IID_IUnknown || riid == IID_ICredentialProviderCredential)
    {
        *ppv = static_cast<ICredentialProviderCredential*>(this);
        AddRef();
        hr = S_OK;
        Logger::LogInfo(L"PeekCredential::QueryInterface: accepted IID=" + iidStr + L" (ICredentialProviderCredential) -> S_OK");
    }
    else if (riid == IID_ICredentialProviderCredential2)
    {
        *ppv = static_cast<ICredentialProviderCredential2*>(this);
        AddRef();
        hr = S_OK;
        Logger::LogInfo(L"PeekCredential::QueryInterface: accepted IID=" + iidStr + L" (ICredentialProviderCredential2) -> S_OK");
    }
    else
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::QueryInterface: rejected IID=%s -> E_NOINTERFACE (0x%08X)", iidStr.c_str(), hr);
        Logger::LogInfo(szBuf);
    }

    return hr;
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
    wchar_t szBuf[128] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::Advise: pcpce=%p", pcpce);
    Logger::LogInfo(szBuf);

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
    Logger::LogInfo("PeekCredential::Advise -> S_OK");
    return S_OK;
}

HRESULT PeekCredential::UnAdvise()
{
    Logger::LogInfo("PeekCredential::UnAdvise called");
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
    Logger::LogInfo("PeekCredential::UnAdvise -> S_OK");
    return S_OK;
}

HRESULT PeekCredential::SetSelected(BOOL* pbAutoLogon)
{
    if (pbAutoLogon)
    {
        *pbAutoLogon = FALSE;
    }

    wchar_t szBuf[128] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::SetSelected called (pbAutoLogon=%p -> %d)",
        pbAutoLogon, pbAutoLogon ? *pbAutoLogon : 0);
    Logger::LogInfo(szBuf);

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
    Logger::LogInfo("PeekCredential::SetSelected: starting background authentication via StartAuthAsync");
    m_pipeClient.StartAuthAsync([this](const PeekAuthResult& result) {
        this->OnEngineStateUpdate(result);
    }, 12.0f);

    Logger::LogInfo("PeekCredential::SetSelected -> S_OK");
    return S_OK;
}

HRESULT PeekCredential::SetDeselected()
{
    Logger::LogInfo("PeekCredential::SetDeselected called");
    EnterCriticalSection(&m_cs);
    m_isSelected = false;
    if (m_hRetryCancelEvent)
    {
        SetEvent(m_hRetryCancelEvent);
    }
    m_pipeClient.Cancel();
    LeaveCriticalSection(&m_cs);
    m_animator.SetState(PeekUiState::IDLE);
    Logger::LogInfo("PeekCredential::SetDeselected -> S_OK");
    return S_OK;
}

HRESULT PeekCredential::GetFieldState(
    DWORD dwFieldID,
    CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs,
    CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis
)
{
    if (!pcpfs || !pcpfis)
    {
        Logger::LogError("PeekCredential::GetFieldState: null pointer -> E_POINTER");
        return E_POINTER;
    }

    if (dwFieldID >= PFI_COUNT)
    {
        wchar_t szBuf[128] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::GetFieldState: dwFieldID=%u >= PFI_COUNT(%u) -> E_INVALIDARG", dwFieldID, PFI_COUNT);
        Logger::LogError(szBuf);
        return E_INVALIDARG;
    }

    *pcpfis = CPFIS_NONE;

    switch (dwFieldID)
    {
    case PFI_LOGO:
    case PFI_LABEL:
    case PFI_DISPLAY_NAME:
        *pcpfs = CPFS_DISPLAY_IN_BOTH;
        break;

    case PFI_STATUS_TEXT:
        *pcpfs = CPFS_DISPLAY_IN_SELECTED_TILE;
        break;

    default:
        *pcpfs = CPFS_HIDDEN;
        break;
    }

    wchar_t szBuf[256] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"PeekCredential::GetFieldState: fieldID=%u, state=%d, istate=%d -> S_OK",
        dwFieldID, *pcpfs, *pcpfis);
    Logger::LogInfo(szBuf);

    return S_OK;
}

HRESULT PeekCredential::GetStringValue(DWORD dwFieldID, PWSTR* ppsz)
{
    if (!ppsz)
    {
        Logger::LogError("PeekCredential::GetStringValue: ppsz is null -> E_POINTER");
        return E_POINTER;
    }
    *ppsz = nullptr;

    EnterCriticalSection(&m_cs);
    HRESULT hr = S_OK;

    switch (dwFieldID)
    {
    case PFI_LABEL:
        hr = CoTaskMemAllocString(L"Peek - Face Unlock", ppsz);
        break;

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

    std::wstring valStr = (ppsz && *ppsz) ? *ppsz : L"(null)";
    LeaveCriticalSection(&m_cs);

    wchar_t szBuf[512] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"PeekCredential::GetStringValue: fieldID=%u, val='%s' -> hr=0x%08X",
        dwFieldID, valStr.c_str(), hr);
    Logger::LogInfo(szBuf);

    return hr;
}

HRESULT PeekCredential::GetBitmapValue(DWORD dwFieldID, HBITMAP* phbmp)
{
    if (!phbmp)
    {
        Logger::LogError("PeekCredential::GetBitmapValue: phbmp is null -> E_POINTER");
        return E_POINTER;
    }
    *phbmp = nullptr;

    HRESULT hr = S_OK;
    if (dwFieldID == PFI_LOGO)
    {
        // 1. Return current 32bpp ARGB frame from TileAnimator if available
        HBITMAP hFrame = m_animator.GetCurrentFrame();
        if (hFrame)
        {
            *phbmp = static_cast<HBITMAP>(CopyImage(hFrame, IMAGE_BITMAP, 0, 0, LR_CREATEDIBSECTION));
            if (*phbmp) hr = S_OK;
        }

        if (!*phbmp)
        {
            // 2. Fail-soft fallback to static embedded bitmap if TileAnimator has no frames
            *phbmp = LoadBitmapW(g_hinst, MAKEINTRESOURCEW(IDB_PEEK_LOGO));
            hr = (*phbmp != NULL) ? S_OK : HRESULT_FROM_WIN32(GetLastError());
        }
    }
    else
    {
        hr = E_INVALIDARG;
    }

    wchar_t szBuf[256] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"PeekCredential::GetBitmapValue: fieldID=%u, hbmp=%p -> hr=0x%08X",
        dwFieldID, *phbmp, hr);
    Logger::LogInfo(szBuf);

    return hr;
}

HRESULT PeekCredential::GetCheckboxValue(DWORD /*dwFieldID*/, BOOL* /*pbChecked*/, PWSTR* /*ppszLabel*/)
{
    return E_NOTIMPL;
}

HRESULT PeekCredential::GetSubmitButtonValue(DWORD dwFieldID, DWORD* /*pdwAdjacentTo*/)
{
    wchar_t szBuf[128] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::GetSubmitButtonValue: fieldID=%u -> E_NOTIMPL", dwFieldID);
    Logger::LogInfo(szBuf);
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
    bool isLocked = m_lockedSession;
    std::wstring user = m_username;
    std::wstring domain = m_domain;
    std::wstring sid = m_sid;
    std::wstring profileId = m_profileId;
    LeaveCriticalSection(&m_cs);

    // NON-NEGOTIABLE SECURITY INVARIANT:
    // Only return credentials if authentication strictly succeeded via the engine
    // AND it is a locked session scenario with valid SID and profile ID.
    if (!isAuth || !isLocked || sid.empty() || profileId.empty())
    {
        *pcpgsr = CPGSR_NO_CREDENTIAL_FINISHED;
        return S_OK;
    }

    Logger::LogInfo(L"GetSerialization for locked session scenario - attempting to retrieve password from LSA secret store");

    std::wstring password;
    if (PeekSecretVault::ReadPasswordSecret(sid, profileId, password))
    {
        Logger::LogInfo(L"Successfully retrieved and validated secret from LSA store for bound profile");

        // Stale-secret handling: still validate with LogonUserW before submitting.
        DWORD dwLogonErr = ERROR_SUCCESS;
        auto validator = PeekSecretVault::GetPasswordValidator();
        bool bValid = validator ? validator(user.c_str(), domain.c_str(), password.c_str(), &dwLogonErr) : false;

        if (bValid)
        {
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

            if (SUCCEEDED(hr))
            {
                Logger::LogInfo(L"Successfully packaged Kerberos logon serialization");
                *pcpgsr = CPGSR_RETURN_CREDENTIAL_FINISHED;
                return S_OK;
            }
        }
        else
        {
            // Delete the LSA secret ONLY when the failure is ERROR_LOGON_FAILURE (wrong password).
            // Do NOT delete for lockout, disabled account, expired/must-change password, restricted hours,
            // or any other error; fail open instead. Never submit the same failed credential twice.
            if (dwLogonErr == ERROR_LOGON_FAILURE)
            {
                Logger::LogInfo(L"LogonUserW rejected stored password with ERROR_LOGON_FAILURE; deleting stale LSA secret");
                PeekSecretVault::DeletePasswordSecret(sid);
            }
            else
            {
                wchar_t szErrBuf[128] = {};
                swprintf_s(szErrBuf, ARRAYSIZE(szErrBuf), L"LogonUserW failed with error %u (not ERROR_LOGON_FAILURE); preserving LSA secret and failing open", dwLogonErr);
                Logger::LogInfo(szErrBuf);
            }

            if (!password.empty())
            {
                SecureZeroMemory(&password[0], password.size() * sizeof(wchar_t));
            }
        }
    }
    else
    {
        Logger::LogInfo(L"No valid LSA secret found or profile binding mismatch - failing open to standard authentication");
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

HRESULT PeekCredential::ReportResult(
    NTSTATUS ntsStatus,
    NTSTATUS ntsSubstatus,
    PWSTR* /*ppszOptionalStatusText*/,
    CREDENTIAL_PROVIDER_STATUS_ICON* /*pcpsiOptionalStatusIcon*/
)
{
    EnterCriticalSection(&m_cs);
    std::wstring sid = m_sid;
    bool isLocked = m_lockedSession;
    LeaveCriticalSection(&m_cs);

    // If Winlogon reports wrong password, delete the stale stored LSA secret for this SID.
    // Do NOT delete for lockout, disabled account, expired/must-change password, or any other error.
    if (ntsStatus == STATUS_LOGON_FAILURE ||
        ntsStatus == STATUS_WRONG_PASSWORD ||
        ntsSubstatus == STATUS_WRONG_PASSWORD ||
        ntsSubstatus == STATUS_LOGON_FAILURE)
    {
        if (!sid.empty() && isLocked)
        {
            Logger::LogInfo(L"ReportResult received authentication failure from Winlogon; deleting stale LSA secret");
            PeekSecretVault::DeletePasswordSecret(sid);
        }
    }

    wchar_t szBuf[128] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::ReportResult: ntsStatus=0x%08X, ntsSubstatus=0x%08X -> S_OK", ntsStatus, ntsSubstatus);
    Logger::LogInfo(szBuf);

    return S_OK;
}

// --- ICredentialProviderCredential2 ---

HRESULT PeekCredential::GetUserSid(PWSTR* ppszSid)
{
    if (!ppszSid)
    {
        Logger::LogError("PeekCredential::GetUserSid: ppszSid is null -> E_POINTER");
        return E_POINTER;
    }
    *ppszSid = nullptr;

    EnterCriticalSection(&m_cs);
    std::wstring sid = m_sid;
    LeaveCriticalSection(&m_cs);

    if (!sid.empty())
    {
        HRESULT hr = CoTaskMemAllocString(sid.c_str(), ppszSid);
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::GetUserSid: SID='%s' -> hr=0x%08X", sid.c_str(), hr);
        Logger::LogInfo(szBuf);
        return hr;
    }

    Logger::LogInfo("PeekCredential::GetUserSid: SID is empty -> S_FALSE");
    return S_FALSE;
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
