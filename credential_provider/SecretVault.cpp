// Peek Face Engine - Secret Vault Implementation
// Manages LSA private data secrets for credential provider

#include "SecretVault.h"
#include "common.h"
#include <windows.h>
#include <ntsecapi.h>
#include <vector>
#include <string>
#include <memory>
#include <map>

#pragma comment(lib, "Advapi32.lib")

#ifndef STATUS_SUCCESS
#define STATUS_SUCCESS ((NTSTATUS)0x00000000L)
#endif
#ifndef STATUS_OBJECT_NAME_NOT_FOUND
#define STATUS_OBJECT_NAME_NOT_FOUND ((NTSTATUS)0xC0000034L)
#endif

namespace PeekSecretVault
{
    static std::shared_ptr<ILsaSecretStore> s_activeStore = nullptr;
    static PasswordValidatorFn s_activeValidator = nullptr;

    static void InitLsaString(PLSA_UNICODE_STRING pLsaStr, PCWSTR pszStr)
    {
        if (pszStr)
        {
            size_t cch = wcslen(pszStr);
            pLsaStr->Buffer = const_cast<PWSTR>(pszStr);
            pLsaStr->Length = static_cast<USHORT>(cch * sizeof(WCHAR));
            pLsaStr->MaximumLength = static_cast<USHORT>((cch + 1) * sizeof(WCHAR));
        }
        else
        {
            pLsaStr->Buffer = nullptr;
            pLsaStr->Length = 0;
            pLsaStr->MaximumLength = 0;
        }
    }

    NTSTATUS RealLsaSecretStore::RetrieveSecret(const std::wstring& secretName, std::vector<BYTE>& outData)
    {
        outData.clear();
        LSA_OBJECT_ATTRIBUTES objAttr = {};
        LSA_HANDLE hPolicy = NULL;

        NTSTATUS status = LsaOpenPolicy(NULL, &objAttr, POLICY_GET_PRIVATE_INFORMATION, &hPolicy);
        if (status != STATUS_SUCCESS)
        {
            return status;
        }

        LSA_UNICODE_STRING lusKey = {};
        InitLsaString(&lusKey, secretName.c_str());

        PLSA_UNICODE_STRING pPrivateData = NULL;
        status = LsaRetrievePrivateData(hPolicy, &lusKey, &pPrivateData);
        if (status == STATUS_SUCCESS && pPrivateData != NULL)
        {
            if (pPrivateData->Buffer != NULL && pPrivateData->Length > 0)
            {
                outData.resize(pPrivateData->Length);
                memcpy(outData.data(), pPrivateData->Buffer, pPrivateData->Length);
                SecureZeroMemory(pPrivateData->Buffer, pPrivateData->Length);
            }
            LsaFreeMemory(pPrivateData);
        }

        LsaClose(hPolicy);
        return status;
    }

    NTSTATUS RealLsaSecretStore::StoreSecret(const std::wstring& secretName, const BYTE* pbData, DWORD cbData)
    {
        LSA_OBJECT_ATTRIBUTES objAttr = {};
        LSA_HANDLE hPolicy = NULL;

        NTSTATUS status = LsaOpenPolicy(NULL, &objAttr, POLICY_CREATE_SECRET, &hPolicy);
        if (status != STATUS_SUCCESS)
        {
            return status;
        }

        LSA_UNICODE_STRING lusKey = {};
        InitLsaString(&lusKey, secretName.c_str());

        LSA_UNICODE_STRING lusData = {};
        lusData.Buffer = const_cast<PWSTR>(reinterpret_cast<PCWSTR>(pbData));
        lusData.Length = static_cast<USHORT>(cbData);
        lusData.MaximumLength = static_cast<USHORT>(cbData);

        status = LsaStorePrivateData(hPolicy, &lusKey, &lusData);
        LsaClose(hPolicy);
        return status;
    }

    NTSTATUS RealLsaSecretStore::DeleteSecret(const std::wstring& secretName)
    {
        LSA_OBJECT_ATTRIBUTES objAttr = {};
        LSA_HANDLE hPolicy = NULL;

        NTSTATUS status = LsaOpenPolicy(NULL, &objAttr, POLICY_CREATE_SECRET, &hPolicy);
        if (status != STATUS_SUCCESS)
        {
            return status;
        }

        LSA_UNICODE_STRING lusKey = {};
        InitLsaString(&lusKey, secretName.c_str());

        // Storing NULL deletes the secret
        status = LsaStorePrivateData(hPolicy, &lusKey, NULL);
        LsaClose(hPolicy);
        return status;
    }

    NTSTATUS InMemoryLsaSecretStore::RetrieveSecret(const std::wstring& secretName, std::vector<BYTE>& outData)
    {
        outData.clear();
        auto it = m_secrets.find(secretName);
        if (it == m_secrets.end())
        {
            return STATUS_OBJECT_NAME_NOT_FOUND;
        }
        outData = it->second;
        return STATUS_SUCCESS;
    }

    NTSTATUS InMemoryLsaSecretStore::StoreSecret(const std::wstring& secretName, const BYTE* pbData, DWORD cbData)
    {
        if (pbData && cbData > 0)
        {
            m_secrets[secretName] = std::vector<BYTE>(pbData, pbData + cbData);
        }
        else
        {
            m_secrets.erase(secretName);
        }
        return STATUS_SUCCESS;
    }

    NTSTATUS InMemoryLsaSecretStore::DeleteSecret(const std::wstring& secretName)
    {
        m_secrets.erase(secretName);
        return STATUS_SUCCESS;
    }

    void InMemoryLsaSecretStore::Clear()
    {
        for (auto& pair : m_secrets)
        {
            if (!pair.second.empty())
            {
                SecureZeroMemory(pair.second.data(), pair.second.size());
            }
        }
        m_secrets.clear();
    }

    bool InMemoryLsaSecretStore::HasSecret(const std::wstring& secretName) const
    {
        return m_secrets.find(secretName) != m_secrets.end();
    }

    void SetLsaSecretStore(std::shared_ptr<ILsaSecretStore> store)
    {
        s_activeStore = store;
    }

    std::shared_ptr<ILsaSecretStore> GetLsaSecretStore()
    {
        if (!s_activeStore)
        {
            s_activeStore = std::make_shared<RealLsaSecretStore>();
        }
        return s_activeStore;
    }

    void SetPasswordValidator(PasswordValidatorFn fn)
    {
        s_activeValidator = fn;
    }

    PasswordValidatorFn GetPasswordValidator()
    {
        if (!s_activeValidator)
        {
            s_activeValidator = ValidatePassword;
        }
        return s_activeValidator;
    }

    bool BuildSecretPayload(
        const std::wstring& profileId,
        const std::wstring& password,
        std::vector<BYTE>& outPayload
    )
    {
        outPayload.clear();
        if (profileId.empty() || password.empty())
        {
            return false;
        }

        DWORD version = CURRENT_PAYLOAD_VERSION; // 1
        DWORD profileIdBytes = static_cast<DWORD>(profileId.size() * sizeof(wchar_t));
        DWORD passwordBytes = static_cast<DWORD>(password.size() * sizeof(wchar_t));

        DWORD totalSize = sizeof(DWORD) * 3 + profileIdBytes + passwordBytes;
        if (totalSize > LSA_SECRET_MAX_SIZE)
        {
            return false;
        }

        outPayload.resize(totalSize);
        BYTE* ptr = outPayload.data();

        memcpy(ptr, &version, sizeof(DWORD));
        ptr += sizeof(DWORD);

        memcpy(ptr, &profileIdBytes, sizeof(DWORD));
        ptr += sizeof(DWORD);

        memcpy(ptr, profileId.data(), profileIdBytes);
        ptr += profileIdBytes;

        memcpy(ptr, &passwordBytes, sizeof(DWORD));
        ptr += sizeof(DWORD);

        memcpy(ptr, password.data(), passwordBytes);

        return true;
    }

    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        std::wstring& outProfileId,
        std::wstring& outPassword
    )
    {
        outProfileId.clear();
        outPassword.clear();
        if (!pbData || cbData < sizeof(DWORD) * 3 || cbData > LSA_SECRET_MAX_SIZE)
        {
            return false;
        }

        const BYTE* ptr = pbData;
        DWORD version = 0;
        memcpy(&version, ptr, sizeof(DWORD));
        ptr += sizeof(DWORD);

        if (version != CURRENT_PAYLOAD_VERSION)
        {
            return false;
        }

        DWORD profileIdBytes = 0;
        memcpy(&profileIdBytes, ptr, sizeof(DWORD));
        ptr += sizeof(DWORD);

        if (profileIdBytes == 0 || (profileIdBytes % sizeof(wchar_t)) != 0)
        {
            return false;
        }

        DWORD remainingAfterProfLen = cbData - (sizeof(DWORD) * 2);
        if (profileIdBytes > remainingAfterProfLen - sizeof(DWORD))
        {
            return false;
        }

        outProfileId.assign(reinterpret_cast<const wchar_t*>(ptr), profileIdBytes / sizeof(wchar_t));
        ptr += profileIdBytes;

        DWORD passwordBytes = 0;
        memcpy(&passwordBytes, ptr, sizeof(DWORD));
        ptr += sizeof(DWORD);

        if (passwordBytes == 0 || (passwordBytes % sizeof(wchar_t)) != 0)
        {
            outProfileId.clear();
            return false;
        }

        DWORD expectedRemaining = cbData - (sizeof(DWORD) * 3 + profileIdBytes);
        if (passwordBytes != expectedRemaining)
        {
            outProfileId.clear();
            return false;
        }

        outPassword.assign(reinterpret_cast<const wchar_t*>(ptr), passwordBytes / sizeof(wchar_t));
        return true;
    }

    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        const std::wstring& expectedProfileId,
        std::wstring& outPassword
    )
    {
        std::wstring storedProfileId;
        if (!ParseSecretPayload(pbData, cbData, storedProfileId, outPassword))
        {
            return false;
        }

        if (storedProfileId != expectedProfileId)
        {
            if (!outPassword.empty())
            {
                SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                outPassword.clear();
            }
            return false;
        }

        return true;
    }

    std::wstring GetSecretKeyName(const std::wstring& userSid)
    {
        return L"L$PeekCred_" + userSid;
    }

    bool GetStoredProfileId(
        const std::wstring& userSid,
        std::wstring& outProfileId,
        ILsaSecretStore* pStore
    )
    {
        outProfileId.clear();
        if (userSid.empty()) return false;

        std::shared_ptr<ILsaSecretStore> spStore;
        if (!pStore)
        {
            spStore = GetLsaSecretStore();
            pStore = spStore.get();
        }
        if (!pStore) return false;

        std::wstring keyName = GetSecretKeyName(userSid);
        std::vector<BYTE> secretData;
        NTSTATUS status = pStore->RetrieveSecret(keyName, secretData);
        if (status != STATUS_SUCCESS || secretData.empty())
        {
            return false;
        }

        std::wstring dummyPassword;
        bool ok = ParseSecretPayload(secretData.data(), static_cast<DWORD>(secretData.size()), outProfileId, dummyPassword);
        if (!dummyPassword.empty())
        {
            SecureZeroMemory(&dummyPassword[0], dummyPassword.size() * sizeof(wchar_t));
        }
        if (!secretData.empty())
        {
            SecureZeroMemory(secretData.data(), secretData.size());
        }
        return ok && !outProfileId.empty();
    }

    bool ReadPasswordSecret(
        const std::wstring& userSid,
        std::wstring& outPassword,
        std::wstring* pOutProfileId,
        ILsaSecretStore* pStore
    )
    {
        outPassword.clear();
        if (pOutProfileId) pOutProfileId->clear();
        if (userSid.empty()) return false;

        std::shared_ptr<ILsaSecretStore> spStore;
        if (!pStore)
        {
            spStore = GetLsaSecretStore();
            pStore = spStore.get();
        }
        if (!pStore) return false;

        std::wstring keyName = GetSecretKeyName(userSid);
        std::vector<BYTE> secretData;
        NTSTATUS status = pStore->RetrieveSecret(keyName, secretData);
        if (status != STATUS_SUCCESS || secretData.empty())
        {
            return false;
        }

        std::wstring storedProfileId;
        bool ok = ParseSecretPayload(secretData.data(), static_cast<DWORD>(secretData.size()), storedProfileId, outPassword);
        if (!secretData.empty())
        {
            SecureZeroMemory(secretData.data(), secretData.size());
        }
        if (ok)
        {
            if (pOutProfileId) *pOutProfileId = storedProfileId;
            return true;
        }
        return false;
    }

    bool ReadPasswordSecret(
        const std::wstring& userSid,
        const std::wstring& expectedProfileId,
        std::wstring& outPassword,
        ILsaSecretStore* pStore
    )
    {
        std::wstring storedProfileId;
        if (!ReadPasswordSecret(userSid, outPassword, &storedProfileId, pStore))
        {
            return false;
        }

        if (storedProfileId != expectedProfileId)
        {
            if (!outPassword.empty())
            {
                SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                outPassword.clear();
            }
            return false;
        }

        return true;
    }

    bool DeletePasswordSecret(
        const std::wstring& userSid,
        ILsaSecretStore* pStore
    )
    {
        if (userSid.empty())
        {
            return false;
        }

        std::shared_ptr<ILsaSecretStore> spStore;
        if (!pStore)
        {
            spStore = GetLsaSecretStore();
            pStore = spStore.get();
        }
        if (!pStore)
        {
            return false;
        }

        std::wstring keyName = GetSecretKeyName(userSid);
        NTSTATUS status = pStore->DeleteSecret(keyName);
        return (status == STATUS_SUCCESS || status == STATUS_OBJECT_NAME_NOT_FOUND);
    }

    bool StorePasswordSecret(
        const std::wstring& userSid,
        const std::wstring& profileId,
        const std::wstring& password,
        ILsaSecretStore* pStore
    )
    {
        if (userSid.empty() || profileId.empty() || password.empty())
        {
            return false;
        }

        std::shared_ptr<ILsaSecretStore> spStore;
        if (!pStore)
        {
            spStore = GetLsaSecretStore();
            pStore = spStore.get();
        }
        if (!pStore)
        {
            return false;
        }

        std::vector<BYTE> payload;
        if (!BuildSecretPayload(profileId, password, payload))
        {
            return false;
        }

        std::wstring keyName = GetSecretKeyName(userSid);
        NTSTATUS status = pStore->StoreSecret(keyName, payload.data(), static_cast<DWORD>(payload.size()));
        SecureZeroMemory(payload.data(), payload.size());
        return (status == STATUS_SUCCESS);
    }

    bool ValidatePassword(
        PCWSTR pszUsername,
        PCWSTR pszDomain,
        PCWSTR pszPassword,
        DWORD* pdwErrorCode
    )
    {
        if (pdwErrorCode) *pdwErrorCode = ERROR_SUCCESS;
        if (!pszUsername || !pszPassword)
        {
            if (pdwErrorCode) *pdwErrorCode = ERROR_INVALID_PARAMETER;
            return false;
        }

        HANDLE hToken = NULL;
        BOOL bResult = LogonUserW(
            pszUsername,
            pszDomain,
            pszPassword,
            LOGON32_LOGON_INTERACTIVE,
            LOGON32_PROVIDER_DEFAULT,
            &hToken
        );

        if (bResult && hToken)
        {
            CloseHandle(hToken);
            return true;
        }

        DWORD dwErr = GetLastError();
        if (pdwErrorCode) *pdwErrorCode = dwErr;
        return false;
    }
}
