// Peek Face Engine - Credential Implementation
// Implements ICredentialProviderCredential and ICredentialProviderCredential2
#pragma once

#include "common.h"
#include "PeekPipeClient.h"
#include "TileAnimator.h"

// Forward declaration of provider class
class PeekProvider;

class PeekCredential : public ICredentialProviderCredential2
{
public:
    PeekCredential();
    virtual ~PeekCredential();

    HRESULT Initialize(
        CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
        const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR* rgcpfd,
        DWORD dwFieldCount,
        PCWSTR pszUsername = nullptr,
        PCWSTR pszDomain = nullptr,
        PCWSTR pszSid = nullptr
    );

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
    void UpdateStatusText(const std::wstring& newText);
    void NotifyCredentialsReady();
    void QueueRetry();
    void RunRetrySequence();

    long m_cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_cpus;
    ICredentialProviderCredentialEvents* m_pcpce;
    PeekProvider* m_pProvider;
    std::wstring m_username;
    std::wstring m_domain;
    std::wstring m_sid;
    std::wstring m_displayName;
    std::wstring m_profileId;
    std::wstring m_statusText;
    bool m_isAuthenticated;
    bool m_isSelected;
    PeekPipeClient m_pipeClient;
    TileAnimator m_animator;
    HANDLE m_hRetryCancelEvent;
    CRITICAL_SECTION m_cs;
};
