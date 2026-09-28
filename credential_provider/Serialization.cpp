// Peek Face Engine - Credential Serialization Implementation
#include "Serialization.h"
#include <ntsecapi.h>

#pragma comment(lib, "Secur32.lib")

// Define missing constants for authentication package names
#ifndef NEGOSSP_NAME_A
#define NEGOSSP_NAME_A   "Negotiate"
#endif

#ifndef MICROSOFT_KERBEROS_NAME_A
#define MICROSOFT_KERBEROS_NAME_A   "Kerberos"
#endif

namespace PeekSerialization
{
    HRESULT GetNegotiateAuthPackage(ULONG* pPackageId)
    {
        if (!pPackageId) return E_POINTER;
        *pPackageId = 0;

        HANDLE hLsa = nullptr;
        NTSTATUS status = LsaConnectUntrusted(&hLsa);
        if (status != 0) // STATUS_SUCCESS = 0
        {
            return HRESULT_FROM_NT(status);
        }

        LSA_STRING packageName;
        packageName.Buffer = const_cast<PCHAR>(MICROSOFT_KERBEROS_NAME_A);
        packageName.Length = static_cast<USHORT>(strlen(MICROSOFT_KERBEROS_NAME_A));
        packageName.MaximumLength = packageName.Length + 1;

        ULONG packageId = 0;
        status = LsaLookupAuthenticationPackage(hLsa, &packageName, &packageId);
        LsaDeregisterLogonProcess(hLsa);

        if (status != 0)
        {
            // Fall back to Negotiate package
            status = LsaConnectUntrusted(&hLsa);
            if (status == 0)
            {
                packageName.Buffer = const_cast<PCHAR>(NEGOSSP_NAME_A);
                packageName.Length = static_cast<USHORT>(strlen(NEGOSSP_NAME_A));
                packageName.MaximumLength = packageName.Length + 1;
                status = LsaLookupAuthenticationPackage(hLsa, &packageName, &packageId);
                LsaDeregisterLogonProcess(hLsa);
            }
        }

        if (status != 0)
        {
            return HRESULT_FROM_NT(status);
        }

        *pPackageId = packageId;
        return S_OK;
    }

    HRESULT PackageKerbLogon(
        PCWSTR pszDomain,
        PCWSTR pszUsername,
        PCWSTR pszPassword,
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs
    )
    {
        if (!pcpcs) return E_POINTER;
        ZeroMemory(pcpcs, sizeof(*pcpcs));

        if (!pszUsername) return E_INVALIDARG;

        PCWSTR domain = pszDomain ? pszDomain : L"";
        PCWSTR password = pszPassword ? pszPassword : L"";

        size_t cchDomain = wcslen(domain);
        size_t cchUser = wcslen(pszUsername);
        size_t cchPass = wcslen(password);

        size_t cbDomain = (cchDomain + 1) * sizeof(WCHAR);
        size_t cbUser = (cchUser + 1) * sizeof(WCHAR);
        size_t cbPass = (cchPass + 1) * sizeof(WCHAR);

        ULONG cbTotal = static_cast<ULONG>(sizeof(KERB_INTERACTIVE_LOGON) + cbDomain + cbUser + cbPass);

        BYTE* pBuffer = static_cast<BYTE*>(CoTaskMemAlloc(cbTotal));
        if (!pBuffer) return E_OUTOFMEMORY;
        ZeroMemory(pBuffer, cbTotal);

        PKERB_INTERACTIVE_LOGON pLogon = reinterpret_cast<PKERB_INTERACTIVE_LOGON>(pBuffer);
        pLogon->MessageType = KerbInteractiveLogon;

        // Current offset within buffer after header
        BYTE* pStringCursor = pBuffer + sizeof(KERB_INTERACTIVE_LOGON);

        // 1. Domain
        CopyMemory(pStringCursor, domain, cbDomain);
        pLogon->LogonDomainName.Length = static_cast<USHORT>(cchDomain * sizeof(WCHAR));
        pLogon->LogonDomainName.MaximumLength = static_cast<USHORT>(cbDomain);
        pLogon->LogonDomainName.Buffer = reinterpret_cast<PWSTR>(pStringCursor);
        pStringCursor += cbDomain;

        // 2. User
        CopyMemory(pStringCursor, pszUsername, cbUser);
        pLogon->UserName.Length = static_cast<USHORT>(cchUser * sizeof(WCHAR));
        pLogon->UserName.MaximumLength = static_cast<USHORT>(cbUser);
        pLogon->UserName.Buffer = reinterpret_cast<PWSTR>(pStringCursor);
        pStringCursor += cbUser;

        // 3. Password
        CopyMemory(pStringCursor, password, cbPass);
        pLogon->Password.Length = static_cast<USHORT>(cchPass * sizeof(WCHAR));
        pLogon->Password.MaximumLength = static_cast<USHORT>(cbPass);
        pLogon->Password.Buffer = reinterpret_cast<PWSTR>(pStringCursor);

        // Resolve package ID
        ULONG authPackage = 0;
        HRESULT hr = GetNegotiateAuthPackage(&authPackage);
        if (FAILED(hr))
        {
            SecureZeroMemory(pBuffer, cbTotal);
            CoTaskMemFree(pBuffer);
            return hr;
        }

        pcpcs->ulAuthenticationPackage = authPackage;
        pcpcs->cbSerialization = cbTotal;
        pcpcs->rgbSerialization = pBuffer;

        return S_OK;
    }

    void WipeSerialization(CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs)
    {
        if (pcpcs && pcpcs->rgbSerialization)
        {
            SecureZeroMemory(pcpcs->rgbSerialization, pcpcs->cbSerialization);
            CoTaskMemFree(pcpcs->rgbSerialization);
            pcpcs->rgbSerialization = nullptr;
            pcpcs->cbSerialization = 0;
        }
    }
}
