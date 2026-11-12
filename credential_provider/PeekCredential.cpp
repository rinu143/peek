// Peek Face Engine - Credential Implementation
#include "PeekCredential.h"
#include "PeekProvider.h"
#include "Serialization.h"
#include "resource.h"
#include "SecretVault.h"
#include <gdiplus.h>

#pragma comment(lib, "gdiplus.lib")

static HBITMAP s_cachedLogo = nullptr;
static bool s_logoLoaded = false;
static CRITICAL_SECTION s_logoCs;
static INIT_ONCE s_logoInitOnce = INIT_ONCE_STATIC_INIT;

#ifdef PEEK_TEST_BUILD
static bool s_testSimulateDecodeFailure = false;
#endif

static HBITMAP DecodePngResourceToArgbDib(HINSTANCE hInstance, int resourceId)
{
    HINSTANCE hInst = hInstance ? hInstance : GetModuleHandleW(NULL);
    HRSRC hRes = FindResourceW(hInst, MAKEINTRESOURCEW(resourceId), RT_RCDATA);
    if (!hRes) return nullptr;

    DWORD dwSize = SizeofResource(hInst, hRes);
    if (dwSize == 0) return nullptr;

    HGLOBAL hResLoaded = LoadResource(hInst, hRes);
    if (!hResLoaded) return nullptr;

    void* pResData = LockResource(hResLoaded);
    if (!pResData) return nullptr;

    HGLOBAL hMem = GlobalAlloc(GMEM_MOVEABLE, dwSize);
    if (!hMem) return nullptr;

    void* pMem = GlobalLock(hMem);
    if (!pMem)
    {
        GlobalFree(hMem);
        return nullptr;
    }
    memcpy(pMem, pResData, dwSize);
    GlobalUnlock(hMem);

    IStream* pStream = nullptr;
    HRESULT hr = CreateStreamOnHGlobal(hMem, TRUE, &pStream);
    if (FAILED(hr) || !pStream)
    {
        GlobalFree(hMem);
        return nullptr;
    }

    ULONG_PTR gdiToken = 0;
    Gdiplus::GdiplusStartupInput gdiInput;
    if (Gdiplus::GdiplusStartup(&gdiToken, &gdiInput, NULL) != Gdiplus::Ok)
    {
        pStream->Release();
        return nullptr;
    }

    HBITMAP hDib = nullptr;
    {
        Gdiplus::Bitmap* pBmp = Gdiplus::Bitmap::FromStream(pStream);
        if (pBmp && pBmp->GetLastStatus() == Gdiplus::Ok)
        {
            UINT width = pBmp->GetWidth();
            UINT height = pBmp->GetHeight();
            if (width > 0 && height > 0)
            {
                BITMAPINFO bmi = {};
                bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
                bmi.bmiHeader.biWidth = width;
                bmi.bmiHeader.biHeight = -(LONG)height; // top-down
                bmi.bmiHeader.biPlanes = 1;
                bmi.bmiHeader.biBitCount = 32;
                bmi.bmiHeader.biCompression = BI_RGB;

                void* pBits = nullptr;
                HDC hdc = GetDC(NULL);
                hDib = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &pBits, NULL, 0);
                ReleaseDC(NULL, hdc);

                if (hDib && pBits)
                {
                    Gdiplus::Rect rc(0, 0, width, height);
                    Gdiplus::BitmapData bmpData = {};
                    bmpData.Width = width;
                    bmpData.Height = height;
                    bmpData.Stride = width * 4;
                    bmpData.PixelFormat = PixelFormat32bppPARGB;
                    bmpData.Scan0 = pBits;

                    Gdiplus::Status st = pBmp->LockBits(
                        &rc,
                        Gdiplus::ImageLockModeRead | Gdiplus::ImageLockModeUserInputBuf,
                        PixelFormat32bppPARGB,
                        &bmpData
                    );

                    if (st == Gdiplus::Ok)
                    {
                        pBmp->UnlockBits(&bmpData);
                    }
                    else
                    {
                        DeleteObject(hDib);
                        hDib = nullptr;
                    }
                }
            }
        }
        delete pBmp;
    }

    pStream->Release();
    Gdiplus::GdiplusShutdown(gdiToken);
    return hDib;
}

HBITMAP PeekCredential::CreateTransparentBitmap(int width, int height)
{
    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = width;
    bmi.bmiHeader.biHeight = -(LONG)height; // top-down
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    void* pBits = nullptr;
    HDC hdc = GetDC(NULL);
    HBITMAP hDib = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &pBits, NULL, 0);
    ReleaseDC(NULL, hdc);

    if (hDib && pBits)
    {
        ZeroMemory(pBits, static_cast<size_t>(width) * height * 4);
    }
    return hDib;
}

HBITMAP PeekCredential::GetOrLoadCachedLogo()
{
    InitOnceExecuteOnce(&s_logoInitOnce, [](PINIT_ONCE, PVOID, PVOID*) -> BOOL {
        InitializeCriticalSection(&s_logoCs);
        return TRUE;
    }, NULL, NULL);

    EnterCriticalSection(&s_logoCs);
    if (!s_logoLoaded)
    {
        s_logoLoaded = true;
#ifdef PEEK_TEST_BUILD
        if (s_testSimulateDecodeFailure)
        {
            s_cachedLogo = nullptr;
        }
        else
#endif
        {
            s_cachedLogo = DecodePngResourceToArgbDib(g_hinst, IDR_PNG_LOGO);
        }

        if (s_cachedLogo)
        {
            DIBSECTION ds = {};
            if (GetObjectW(s_cachedLogo, sizeof(ds), &ds) == sizeof(ds) && ds.dsBm.bmBits)
            {
                int w = ds.dsBm.bmWidth;
                int h = abs(ds.dsBm.bmHeight);
                int bpp = ds.dsBm.bmBitsPixel;
                const BYTE* pBits = reinterpret_cast<const BYTE*>(ds.dsBm.bmBits);
                BYTE a00 = (bpp == 32) ? pBits[3] : 0;
                int cx = w / 2;
                int cy = h / 2;
                BYTE aCenter = (bpp == 32) ? pBits[(cy * w + cx) * 4 + 3] : 0;

                wchar_t szLog[384] = {};
                swprintf_s(szLog, ARRAYSIZE(szLog),
                    L"Static logo loaded: w=%d, h=%d, bpp=%d, alpha(0,0)=%u, alpha(center)=%u",
                    w, h, bpp, a00, aCenter);
                Logger::LogInfo(szLog);
            }
        }
        else
        {
            Logger::LogError(L"Static logo decoding failed or unavailable; using fallback");
        }
    }
    HBITMAP hResult = s_cachedLogo;
    LeaveCriticalSection(&s_logoCs);
    return hResult;
}

void PeekCredential::CleanupStaticLogo()
{
    InitOnceExecuteOnce(&s_logoInitOnce, [](PINIT_ONCE, PVOID, PVOID*) -> BOOL {
        InitializeCriticalSection(&s_logoCs);
        return TRUE;
    }, NULL, NULL);

    EnterCriticalSection(&s_logoCs);
    if (s_cachedLogo)
    {
        DeleteObject(s_cachedLogo);
        s_cachedLogo = nullptr;
    }
    s_logoLoaded = false;
    LeaveCriticalSection(&s_logoCs);
}

#ifdef PEEK_TEST_BUILD
void PeekCredential::TestSetSimulateLogoDecodeFailure(bool sim)
{
    InitOnceExecuteOnce(&s_logoInitOnce, [](PINIT_ONCE, PVOID, PVOID*) -> BOOL {
        InitializeCriticalSection(&s_logoCs);
        return TRUE;
    }, NULL, NULL);

    EnterCriticalSection(&s_logoCs);
    s_testSimulateDecodeFailure = sim;
    if (s_cachedLogo)
    {
        DeleteObject(s_cachedLogo);
        s_cachedLogo = nullptr;
    }
    s_logoLoaded = false;
    LeaveCriticalSection(&s_logoCs);
}
#endif

PeekCredential::PeekCredential()
    : m_cRef(1)
    , m_cpus(CPUS_LOGON)
    , m_lockedSession(false)
    , m_pcpce(nullptr)
    , m_pProvider(nullptr)
    , m_isAuthenticated(false)
    , m_isSelected(false)
    , m_displayName(L"")
    , m_statusText(L"")
    , m_statusTextVisible(false)
    , m_consecutiveFailedAttempts(0)
    , m_hRetryCancelEvent(NULL)
{
    InitializeCriticalSection(&m_cs);
    m_hRetryCancelEvent = CreateEventW(NULL, TRUE, FALSE, NULL);

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
        if (m_displayName.empty())
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
    m_consecutiveFailedAttempts = 0;
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
    m_statusTextVisible = false;
    LeaveCriticalSection(&m_cs);

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
    m_consecutiveFailedAttempts = 0; // Re-selecting resets the counter
    m_statusText = L"";
    if (m_hRetryCancelEvent)
    {
        ResetEvent(m_hRetryCancelEvent);
    }
    LeaveCriticalSection(&m_cs);

    // Initial state presentation: "Looking for face..." status text
    SetStatusText(PeekStatusStrings::kSearching);

#ifdef PEEK_TEST_BUILD
    if (m_testDisablePipe)
    {
        Logger::LogInfo("PeekCredential::SetSelected: pipe client disabled for test");
        return S_OK;
    }
#endif

    // Launch background asynchronous pipe authentication
    Logger::LogInfo("PeekCredential::SetSelected: starting background authentication via StartAuthAsync");
    m_sessionId = PeekPipeClient::GenerateChallengeNonce();
    LogLatency(m_sessionId, "SetSelected");
    m_pipeClient.StartAuthAsync([this](const PeekAuthResult& result) {
        this->OnEngineStateUpdate(result);
    }, m_sid, 12.0f, m_sessionId);

    Logger::LogInfo("PeekCredential::SetSelected -> S_OK");
    return S_OK;
}

HRESULT PeekCredential::SetDeselected()
{
    Logger::LogInfo("PeekCredential::SetDeselected called");
    EnterCriticalSection(&m_cs);
    m_isSelected = false;
    m_consecutiveFailedAttempts = 0;
    if (m_hRetryCancelEvent)
    {
        SetEvent(m_hRetryCancelEvent);
    }
    m_pipeClient.Cancel();
    LeaveCriticalSection(&m_cs);

    HideStatusText();
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
        *pcpfs = CPFS_DISPLAY_IN_BOTH;
        break;

    case PFI_DISPLAY_NAME:
        // Requirement 2: Email hidden in every state
        *pcpfs = CPFS_HIDDEN;
        break;

    case PFI_STATUS_TEXT:
        // Requirement 6: Hidden by default, shown only for PIN fallback or success greeting
        EnterCriticalSection(&m_cs);
        *pcpfs = m_statusTextVisible ? CPFS_DISPLAY_IN_SELECTED_TILE : CPFS_HIDDEN;
        LeaveCriticalSection(&m_cs);
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
        // Requirement 1: Title is "PEEK"
        hr = CoTaskMemAllocString(L"PEEK", ppsz);
        break;

    case PFI_DISPLAY_NAME:
        // Requirement 2: No username/email in any visible field
        hr = CoTaskMemAllocString(L"", ppsz);
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

    if (dwFieldID != PFI_LOGO)
    {
        return E_INVALIDARG;
    }

    HBITMAP hCached = GetOrLoadCachedLogo();
    if (hCached)
    {
        *phbmp = DuplicateArgbBitmap(hCached);
    }

    if (!*phbmp)
    {
        *phbmp = CreateTransparentBitmap(96, 96);
    }

    return (*phbmp != nullptr) ? S_OK : E_FAIL;
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
    // AND it is a locked session scenario with valid SID.
    if (!isAuth || !isLocked || sid.empty())
    {
        *pcpgsr = CPGSR_NO_CREDENTIAL_FINISHED;
        return S_OK;
    }

    Logger::LogInfo(L"GetSerialization for locked session scenario - attempting to retrieve password from LSA secret store");

    std::wstring password;
    bool readSuccess = false;
    if (profileId.empty())
    {
        readSuccess = PeekSecretVault::ReadPasswordSecret(sid, password, &profileId);
    }
    else
    {
        readSuccess = PeekSecretVault::ReadPasswordSecret(sid, profileId, password);
    }

    if (readSuccess && !password.empty() && !profileId.empty())
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
                LogLatency(m_sessionId, "GetSerialization done");
                return S_OK;
            }
        }
        else
        {
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

    LogLatency(m_sessionId, "GetSerialization done", "status=no_credential");
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
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekCredential::GetUserSid: hr=0x%08X", hr);
        Logger::LogInfo(szBuf);
        Logger::LogDebug(L"PeekCredential::GetUserSid: SID='" + sid + L"'");
        return hr;
    }

    Logger::LogInfo("PeekCredential::GetUserSid: SID is empty -> S_FALSE");
    return S_FALSE;
}

void PeekCredential::SetStatusText(const std::wstring& text)
{
    EnterCriticalSection(&m_cs);
    // Skip SetFieldString call when text is unchanged and already visible
    if (m_statusTextVisible && m_statusText == text)
    {
        LeaveCriticalSection(&m_cs);
        return;
    }

    m_statusText = text;
    bool wasVisible = m_statusTextVisible;
    m_statusTextVisible = true;
    ICredentialProviderCredentialEvents* pcpce = m_pcpce;
    if (pcpce) pcpce->AddRef();
    LeaveCriticalSection(&m_cs);

    Logger::LogInfo(L"SetStatusText: " + text);

    if (pcpce)
    {
        if (!wasVisible)
        {
            pcpce->SetFieldState(this, PFI_STATUS_TEXT, CPFS_DISPLAY_IN_SELECTED_TILE);
        }
        HRESULT hr = pcpce->SetFieldString(this, PFI_STATUS_TEXT, text.c_str());

        static std::atomic<int> s_textUpdateCount{ 0 };
        int count = ++s_textUpdateCount;
        if (count <= 20)
        {
            wchar_t szLog[256] = {};
            swprintf_s(szLog, ARRAYSIZE(szLog),
                L"SetFieldString update #%d: text='%s', hr=0x%08X",
                count, text.c_str(), hr);
            Logger::LogInfo(szLog);
        }

        pcpce->Release();
    }
}

void PeekCredential::ShowStatusText(const std::wstring& text)
{
    SetStatusText(text);
}

void PeekCredential::HideStatusText()
{
    EnterCriticalSection(&m_cs);
    m_statusText = PeekStatusStrings::kIdle;
    bool wasVisible = m_statusTextVisible;
    m_statusTextVisible = false;
    ICredentialProviderCredentialEvents* pcpce = m_pcpce;
    if (pcpce) pcpce->AddRef();
    LeaveCriticalSection(&m_cs);

    Logger::LogInfo("HideStatusText: status text hidden");

    if (pcpce && wasVisible)
    {
        pcpce->SetFieldState(this, PFI_STATUS_TEXT, CPFS_HIDDEN);
        pcpce->SetFieldString(this, PFI_STATUS_TEXT, L"");
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
    case PeekIPCState::ENGINE_READY:
    case PeekIPCState::CAMERA_STARTING:
    case PeekIPCState::SEARCHING:
        SetStatusText(PeekStatusStrings::kSearching);
        break;

    case PeekIPCState::FACE_FOUND:
    case PeekIPCState::VERIFYING:
    case PeekIPCState::LIVENESS_CHECK:
        SetStatusText(PeekStatusStrings::kRecognizing);
        break;

    case PeekIPCState::AUTHENTICATED:
        if (result.isAuthorizedToUnlock)
        {
            EnterCriticalSection(&m_cs);
            m_isAuthenticated = true;
            m_consecutiveFailedAttempts = 0;
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

            std::wstring greeting;
            if (!enrolledName.empty() &&
                enrolledName != L"Peek Facial Recognition" &&
                enrolledName != rawUser)
            {
                greeting = L"Hello, " + enrolledName + L"!";
            }
            else
            {
                greeting = PeekStatusStrings::kWelcomeBack;
            }

            if (kShowSuccessGreeting)
            {
                SetStatusText(greeting);
            }

            EnterCriticalSection(&m_cs);
            bool isAuthConfirmed = m_isAuthenticated;
            LeaveCriticalSection(&m_cs);

            if (!result.isAuthorizedToUnlock || !isAuthConfirmed)
            {
                Logger::LogError("Security invariant violation: AUTHENTICATED reached without confirmed unlock authorization; aborting");
                return;
            }

            Sleep(750);

            LogLatency(m_sessionId, "AUTHENTICATED");
            Logger::LogInfo("AUTHENTICATED received - signaling CredentialsChanged");
            NotifyCredentialsReady();
        }
        break;

    case PeekIPCState::AUTH_FAILED:
        {
            EnterCriticalSection(&m_cs);
            m_consecutiveFailedAttempts++;
            int attempts = m_consecutiveFailedAttempts;
            LeaveCriticalSection(&m_cs);

            wchar_t szLog[256] = {};
            swprintf_s(szLog, ARRAYSIZE(szLog),
                L"Authentication failure (AUTH_FAILED): attempt %d of %d (detail: %s)",
                attempts, kMaxRetryCycles, result.detail.c_str());
            Logger::LogInfo(szLog);

            if (attempts >= kMaxRetryCycles)
            {
                Logger::LogInfo(L"Retry limit reached. Stopping session and displaying PIN/password prompt.");
                m_pipeClient.Cancel();
                SetStatusText(PeekStatusStrings::kFallback);
            }
            else
            {
                SetStatusText(PeekStatusStrings::kFailure);
                QueueRetry();
            }
        }
        break;

    case PeekIPCState::TIMEOUT:
        {
            EnterCriticalSection(&m_cs);
            m_consecutiveFailedAttempts++;
            int attempts = m_consecutiveFailedAttempts;
            LeaveCriticalSection(&m_cs);

            wchar_t szLog[256] = {};
            swprintf_s(szLog, ARRAYSIZE(szLog),
                L"Authentication timeout (TIMEOUT): attempt %d of %d (detail: %s)",
                attempts, kMaxRetryCycles, result.detail.c_str());
            Logger::LogInfo(szLog);

            m_pipeClient.Cancel();
            SetStatusText(PeekStatusStrings::kFallback);
        }
        break;

    case PeekIPCState::DISCONNECTED:
    case PeekIPCState::ERROR_STATE:
    default:
        if (result.reasonCode == L"VERSION_MISMATCH")
        {
            Logger::LogError(L"Engine version mismatch encountered: " + result.detail + L". Displaying restart notification and failing open.");
            m_pipeClient.Cancel();
            SetStatusText(result.detail.empty() ? L"Peek was just updated, restart your PC" : result.detail.c_str());
            break;
        }
        Logger::LogError(L"Engine offline or pipe disconnected: detail='" + result.detail + L"', reason='" + result.reasonCode + L"'");
        SetStatusText(PeekStatusStrings::kFallback);
        break;
    }
}

void PeekCredential::QueueRetry()
{
    EnterCriticalSection(&m_cs);
    bool canRetry = m_isSelected && !m_isAuthenticated && (m_consecutiveFailedAttempts < kMaxRetryCycles);
    LeaveCriticalSection(&m_cs);
    if (!canRetry) return;

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
    if (WaitForSingleObject(m_hRetryCancelEvent, 1200) != WAIT_TIMEOUT)
    {
        return; // cancelled or deselected
    }

    EnterCriticalSection(&m_cs);
    bool stillActive = m_isSelected && !m_isAuthenticated && (m_consecutiveFailedAttempts < kMaxRetryCycles);
    LeaveCriticalSection(&m_cs);
    if (!stillActive) return;

    SetStatusText(PeekStatusStrings::kRetry);

    if (WaitForSingleObject(m_hRetryCancelEvent, 600) != WAIT_TIMEOUT)
    {
        return; // cancelled or deselected
    }

    EnterCriticalSection(&m_cs);
    stillActive = m_isSelected && !m_isAuthenticated && (m_consecutiveFailedAttempts < kMaxRetryCycles);
    LeaveCriticalSection(&m_cs);
    if (!stillActive) return;

    SetStatusText(PeekStatusStrings::kSearching);

    m_sessionId = PeekPipeClient::GenerateChallengeNonce();
    LogLatency(m_sessionId, "SetSelected", "retry=true");
    m_pipeClient.StartAuthAsync([this](const PeekAuthResult& res) {
        this->OnEngineStateUpdate(res);
    }, m_sid, 12.0f, m_sessionId);
}
