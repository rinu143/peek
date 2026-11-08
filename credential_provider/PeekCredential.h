// Peek Face Engine - Credential Implementation
// Implements ICredentialProviderCredential and ICredentialProviderCredential2
#pragma once

#include "common.h"
#include "PeekPipeClient.h"
#include "TileAnimator.h"
#include <set>
#include <string>

// Forward declaration of provider class
class PeekProvider;

// Constant table for all plain text status messages (Requirement 1)
struct PeekStatusStrings
{
    static constexpr const wchar_t* kSearching    = L"Looking for face...";
    static constexpr const wchar_t* kFaceFound    = L"Found match";
    static constexpr const wchar_t* kVerifying    = L"Verifying...";
    static constexpr const wchar_t* kLiveness    = L"Verifying...";
    static constexpr const wchar_t* kRetry        = L"Retrying...";
    static constexpr const wchar_t* kFailure      = L"Face not recognized";
    static constexpr const wchar_t* kFallback     = L"Sign in with your PIN or password";
    static constexpr const wchar_t* kWelcomeBack  = L"Welcome back";
    static constexpr const wchar_t* kIdle         = L"";

    static const wchar_t* GetTextForState(PeekUiState state)
    {
        switch (state)
        {
        case PeekUiState::SEARCHING:  return kSearching;
        case PeekUiState::FACE_FOUND: return kFaceFound;
        case PeekUiState::VERIFYING:  return kVerifying;
        case PeekUiState::LIVENESS:   return kLiveness;
        case PeekUiState::RETRY:      return kRetry;
        case PeekUiState::FAILURE:    return kFailure;
        case PeekUiState::IDLE:
        default:                      return kIdle;
        }
    }
};

class PeekCredential : public ICredentialProviderCredential2
{
public:
    static constexpr bool kShowSuccessGreeting = true;
    static constexpr int kMaxRetryCycles = 3;

    PeekCredential();
    virtual ~PeekCredential();

    HRESULT Initialize(
        CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
        const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR* rgcpfd,
        DWORD dwFieldCount,
        PCWSTR pszUsername = nullptr,
        PCWSTR pszDomain = nullptr,
        PCWSTR pszSid = nullptr,
        bool bLockedSession = false
    );

    void SetUserContext(
        PCWSTR pszUsername,
        PCWSTR pszDomain,
        PCWSTR pszSid
    );

    void SetLockedSession(bool bLockedSession);
    bool IsLockedSession() const;

#ifdef PEEK_TEST_BUILD
    void TestSetAuthenticated(bool isAuth, const std::wstring& profileId)
    {
        EnterCriticalSection(&m_cs);
        m_isAuthenticated = isAuth;
        m_profileId = profileId;
        LeaveCriticalSection(&m_cs);
    }
    void TestOnEngineStateUpdate(const PeekAuthResult& result)
    {
        OnEngineStateUpdate(result);
    }
    int TestGetConsecutiveFailedAttempts() const
    {
        EnterCriticalSection(&const_cast<CRITICAL_SECTION&>(m_cs));
        int attempts = m_consecutiveFailedAttempts;
        LeaveCriticalSection(&const_cast<CRITICAL_SECTION&>(m_cs));
        return attempts;
    }
    TileAnimator& TestGetAnimator() { return m_animator; }
    void TestCancelPipe() { m_pipeClient.Cancel(); }
    void TestSetDisablePipe(bool disable) { m_testDisablePipe = disable; }
#endif

    // Provider linkage for logon notifications
    void SetProvider(PeekProvider* pProvider);
    bool IsAuthenticated() const;

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv);
    IFACEMETHODIMP_(ULONG) AddRef();
    IFACEMETHODIMP_(ULONG) Release();

    // ICredentialProviderCredential
    IFACEMETHODIMP Advise(ICredentialProviderCredentialEvents* pcpce);
    IFACEMETHODIMP UnAdvise();
    IFACEMETHODIMP SetSelected(BOOL* pbAutoLogon);
    IFACEMETHODIMP SetDeselected();
    IFACEMETHODIMP GetFieldState(DWORD dwFieldID, CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs, CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis);
    IFACEMETHODIMP GetStringValue(DWORD dwFieldID, PWSTR* ppsz);
    IFACEMETHODIMP GetBitmapValue(DWORD dwFieldID, HBITMAP* phbmp);
    IFACEMETHODIMP GetCheckboxValue(DWORD dwFieldID, BOOL* pbChecked, PWSTR* ppszLabel);
    IFACEMETHODIMP GetSubmitButtonValue(DWORD dwFieldID, DWORD* pdwAdjacentTo);
    IFACEMETHODIMP GetComboBoxValueCount(DWORD dwFieldID, DWORD* pcItems, DWORD* pdwSelectedItem);
    IFACEMETHODIMP GetComboBoxValueAt(DWORD dwFieldID, DWORD dwItem, PWSTR* ppszItem);
    IFACEMETHODIMP SetStringValue(DWORD dwFieldID, PCWSTR psz);
    IFACEMETHODIMP SetCheckboxValue(DWORD dwFieldID, BOOL bChecked);
    IFACEMETHODIMP SetComboBoxSelectedValue(DWORD dwFieldID, DWORD dwSelectedItem);
    IFACEMETHODIMP CommandLinkClicked(DWORD dwFieldID);
    IFACEMETHODIMP GetSerialization(
        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs,
        PWSTR* ppszOptionalStatusText,
        CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon
    );
    IFACEMETHODIMP ReportResult(
        NTSTATUS ntsStatus,
        NTSTATUS ntsSubstatus,
        PWSTR* ppszOptionalStatusText,
        CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon
    );

    // ICredentialProviderCredential2
    IFACEMETHODIMP GetUserSid(PWSTR* ppszSid);

private:
    void OnEngineStateUpdate(const PeekAuthResult& result);
    void SetStatusText(const std::wstring& text);
    void ShowStatusText(const std::wstring& text);
    void HideStatusText();
    void NotifyCredentialsReady();
    void QueueRetry();
    void RunRetrySequence();

    long m_cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_cpus;
    bool m_lockedSession;
    ICredentialProviderCredentialEvents* m_pcpce;
    PeekProvider* m_pProvider;
    std::wstring m_username;
    std::wstring m_domain;
    std::wstring m_sid;
    std::wstring m_displayName;
    std::wstring m_profileId;
    std::wstring m_statusText;
    bool m_statusTextVisible;
    int m_consecutiveFailedAttempts;
    bool m_isAuthenticated;
    bool m_isSelected;
    PeekPipeClient m_pipeClient;
    TileAnimator m_animator;
    HANDLE m_hRetryCancelEvent;
    CRITICAL_SECTION m_cs;
#ifdef PEEK_TEST_BUILD
    bool m_testDisablePipe = false;
#endif
};
