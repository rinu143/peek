// Peek Face Engine - Credential Provider Main Class
// Implements ICredentialProvider COM Interface
#pragma once

#include "common.h"
#include "PeekCredential.h"

class PeekProvider : public ICredentialProvider
{
public:
    PeekProvider();
    virtual ~PeekProvider();

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv);
    IFACEMETHODIMP_(ULONG) AddRef();
    IFACEMETHODIMP_(ULONG) Release();

    // ICredentialProvider
    IFACEMETHODIMP SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, DWORD dwFlags);
    IFACEMETHODIMP SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs);
    IFACEMETHODIMP Advise(ICredentialProviderEvents* pcpe, UINT_PTR upAdviseContext);
    IFACEMETHODIMP UnAdvise();
    IFACEMETHODIMP GetFieldDescriptorCount(DWORD* pdwCount);
    IFACEMETHODIMP GetFieldDescriptorAt(DWORD dwIndex, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd);
    IFACEMETHODIMP GetCredentialCount(DWORD* pdwCount, DWORD* pdwDefault, BOOL* pbAutoLogonWithDefault);
    IFACEMETHODIMP GetCredentialAt(DWORD dwIndex, ICredentialProviderCredential** ppcpc);

    // Notify LogonUI that credentials have changed
    void NotifyCredentialsChanged();

private:
    void ReleaseCredential();

    long m_cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_cpus;
    DWORD m_dwFlags;
    ICredentialProviderEvents* m_pcpe;
    UINT_PTR m_upAdviseContext;
    PeekCredential* m_pCredential;
};
