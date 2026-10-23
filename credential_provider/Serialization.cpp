// Peek Face Engine - Credential Serialization Implementation
#include "Serialization.h"
#include <ntsecapi.h>
#include <wincred.h>

#pragma comment(lib, "Secur32.lib")
#pragma comment(lib, "Credui.lib")

// Define missing constants for authentication package names
#ifndef NEGOSSP_NAME_A
#define NEGOSSP_NAME_A   "Negotiate"
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
        packageName.Buffer = const_cast<PCHAR>(NEGOSSP_NAME_A);
        packageName.Length = static_cast<USHORT>(strlen(NEGOSSP_NAME_A));
        packageName.MaximumLength = packageName.Length + 1;

        ULONG packageId = 0;
        status = LsaLookupAuthenticationPackage(hLsa, &packageName, &packageId);
        LsaDeregisterLogonProcess(hLsa);

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

        if (!pszUsername || pszUsername[0] == L'\0') return E_INVALIDARG;

        std::wstring qualifiedUser;
        if (pszDomain && pszDomain[0] != L'\0' && wcscmp(pszDomain, L".") != 0)
        {
            // If domain is specified, check if username already contains a backslash
            const wchar_t* pSlash = wcschr(pszUsername, L'\\');
            if (!pSlash)
            {
                qualifiedUser = pszDomain;
                qualifiedUser += L"\\";
                qualifiedUser += pszUsername;
            }
            else
            {
                qualifiedUser = pszUsername;
            }
        }
        else
        {
            qualifiedUser = pszUsername;
        }

        PCWSTR password = pszPassword ? pszPassword : L"";

        DWORD cbPacked = 0;
        CredPackAuthenticationBufferW(
            0,
            const_cast<PWSTR>(qualifiedUser.c_str()),
            const_cast<PWSTR>(password),
            NULL,
            &cbPacked
        );

        if (cbPacked == 0)
        {
            DWORD dwErr = GetLastError();
            return HRESULT_FROM_WIN32(dwErr);
        }

        BYTE* pBuffer = static_cast<BYTE*>(CoTaskMemAlloc(cbPacked));
        if (!pBuffer) return E_OUTOFMEMORY;
        ZeroMemory(pBuffer, cbPacked);

        if (!CredPackAuthenticationBufferW(
            0,
            const_cast<PWSTR>(qualifiedUser.c_str()),
            const_cast<PWSTR>(password),
            pBuffer,
            &cbPacked))
        {
            DWORD dwErr = GetLastError();
            SecureZeroMemory(pBuffer, cbPacked);
            CoTaskMemFree(pBuffer);
            return HRESULT_FROM_WIN32(dwErr);
        }

        ULONG authPackage = 0;
        HRESULT hr = GetNegotiateAuthPackage(&authPackage);
        if (FAILED(hr))
        {
            SecureZeroMemory(pBuffer, cbPacked);
            CoTaskMemFree(pBuffer);
            return hr;
        }

        pcpcs->ulAuthenticationPackage = authPackage;
        pcpcs->cbSerialization = cbPacked;
        pcpcs->rgbSerialization = pBuffer;
        pcpcs->clsidCredentialProvider = CLSID_PeekCredentialProvider;

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
