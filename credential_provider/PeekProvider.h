// Peek Face Engine - Credential Provider Main Class
// Implements ICredentialProvider and ICredentialProviderSetUserArray COM Interfaces
#pragma once

#include "common.h"
#include "PeekCredential.h"
#include <propkey.h>
#include <propsys.h>
#include <wtsapi32.h>

#ifndef PKEY_Identity_DomainName
DEFINE_PROPERTYKEY(PKEY_Identity_DomainName, 0xC4322503, 0x78CA, 0x49C6, 0x9A, 0xCC, 0xA6, 0x8E, 0x2A, 0xFD, 0x7B, 0x6B, 101);
#endif

class PeekProvider : public ICredentialProvider, public ICredentialProviderSetUserArray
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

    // ICredentialProviderSetUserArray
    IFACEMETHODIMP SetUserArray(ICredentialProviderUserArray* users);

    // Notify LogonUI that credentials have changed
    void NotifyCredentialsChanged();

#ifdef PEEK_TEST_BUILD
    static void SetTestConsoleUserSid(const std::wstring& sid) { s_testConsoleUserSid = sid; s_hasTestConsoleUserSid = true; }
    static void SetTestSessionId(DWORD sessionId) { s_testSessionId = sessionId; s_hasTestSessionId = true; }
    static void ClearTestOverrides() { s_testConsoleUserSid.clear(); s_hasTestConsoleUserSid = false; s_testSessionId = 0xFFFFFFFF; s_hasTestSessionId = false; }
#endif

private:
    void ReleaseCredential();
    void ReleaseUserArray();
    HRESULT ResolveLockedUser(DWORD* pdwUserIndex, bool* pbLockedSession);
    static bool GetConsoleUserSid(std::wstring& outSid);

    long m_cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_cpus;
    DWORD m_dwFlags;
    ICredentialProviderEvents* m_pcpe;
    UINT_PTR m_upAdviseContext;
    PeekCredential* m_pCredential;
    ICredentialProviderUserArray* m_pUserArray;

    std::wstring m_userSid;
    std::wstring m_userName;
    std::wstring m_userDomain;

#ifdef PEEK_TEST_BUILD
    static inline std::wstring s_testConsoleUserSid = L"";
    static inline bool s_hasTestConsoleUserSid = false;
    static inline DWORD s_testSessionId = 0xFFFFFFFF;
    static inline bool s_hasTestSessionId = false;
#endif
};
