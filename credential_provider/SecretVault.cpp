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
    static std::shared_ptr<IIdentityStore> s_activeIdStore = nullptr;
    static PasswordValidatorFn s_activeValidator = nullptr;

    bool RealIdentityStore::GetAuthorizedIdentities(const std::wstring& userSid, std::vector<std::wstring>& outProfileIds)
    {
        outProfileIds.clear();
        if (userSid.empty()) return false;

        std::wstring keyPath = L"SOFTWARE\\Peek\\Identities\\" + userSid;
        HKEY hKey = nullptr;
        // Always use 64-bit registry view
        LSTATUS status = RegOpenKeyExW(HKEY_LOCAL_MACHINE, keyPath.c_str(), 0, KEY_READ | KEY_WOW64_64KEY, &hKey);
        if (status != ERROR_SUCCESS)
        {
            return false;
        }

        DWORD dwType = 0;
        DWORD cbData = 0;
        status = RegQueryValueExW(hKey, L"Authorized", nullptr, &dwType, nullptr, &cbData);
        if (status != ERROR_SUCCESS || cbData == 0)
        {
            RegCloseKey(hKey);
            return true; // Key exists, but value empty
        }

        std::vector<BYTE> buffer(cbData + sizeof(wchar_t) * 2, 0);
        status = RegQueryValueExW(hKey, L"Authorized", nullptr, &dwType, buffer.data(), &cbData);
        RegCloseKey(hKey);

        if (status != ERROR_SUCCESS)
        {
            return true;
        }

        if (dwType == REG_MULTI_SZ)
        {
            const wchar_t* pStr = reinterpret_cast<const wchar_t*>(buffer.data());
            const wchar_t* pEnd = reinterpret_cast<const wchar_t*>(buffer.data() + cbData);
            while (pStr < pEnd && *pStr)
            {
                std::wstring id(pStr);
                if (!id.empty() && outProfileIds.size() < MAX_AUTHORIZED_IDENTITIES)
                {
                    outProfileIds.push_back(id);
                }
                pStr += id.length() + 1;
            }
        }
        else if (dwType == REG_SZ)
        {
            const wchar_t* pStr = reinterpret_cast<const wchar_t*>(buffer.data());
            if (pStr && *pStr)
            {
                outProfileIds.push_back(std::wstring(pStr));
            }
        }

        return true;
    }

    bool InMemoryIdentityStore::GetAuthorizedIdentities(const std::wstring& userSid, std::vector<std::wstring>& outProfileIds)
    {
        outProfileIds.clear();
        auto itExist = m_keyExists.find(userSid);
        if (itExist != m_keyExists.end() && !itExist->second)
        {
            return false;
        }
        auto it = m_identities.find(userSid);
        if (it == m_identities.end())
        {
            if (itExist != m_keyExists.end() && itExist->second)
            {
                return true;
            }
            return false;
        }
        const auto& ids = it->second;
        for (size_t i = 0; i < ids.size() && i < MAX_AUTHORIZED_IDENTITIES; ++i)
        {
            outProfileIds.push_back(ids[i]);
        }
        return true;
    }

    void InMemoryIdentityStore::SetAuthorizedIdentities(const std::wstring& userSid, const std::vector<std::wstring>& profileIds)
    {
        m_identities[userSid] = profileIds;
        m_keyExists[userSid] = true;
    }

    void InMemoryIdentityStore::SetKeyExists(const std::wstring& userSid, bool exists)
    {
        m_keyExists[userSid] = exists;
        if (!exists)
        {
            m_identities.erase(userSid);
        }
    }

    void InMemoryIdentityStore::RemoveIdentities(const std::wstring& userSid)
    {
        m_identities.erase(userSid);
        m_keyExists.erase(userSid);
    }

    void InMemoryIdentityStore::Clear()
    {
        m_identities.clear();
        m_keyExists.clear();
    }

    void SetIdentityStore(std::shared_ptr<IIdentityStore> store)
    {
        s_activeIdStore = store;
    }

    std::shared_ptr<IIdentityStore> GetIdentityStore()
    {
        if (!s_activeIdStore)
        {
            s_activeIdStore = std::make_shared<RealIdentityStore>();
        }
        return s_activeIdStore;
    }

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
        const std::wstring& password,
        std::vector<BYTE>& outPayload
    )
    {
        outPayload.clear();
        if (password.empty())
        {
            return false;
        }

        DWORD version = CURRENT_PAYLOAD_VERSION; // 2
        DWORD passwordBytes = static_cast<DWORD>(password.size() * sizeof(wchar_t));

        DWORD totalSize = sizeof(DWORD) * 2 + passwordBytes;
        if (totalSize > LSA_SECRET_MAX_SIZE)
        {
            return false;
        }

        outPayload.resize(totalSize);
        BYTE* ptr = outPayload.data();

        memcpy(ptr, &version, sizeof(DWORD));
        ptr += sizeof(DWORD);

        memcpy(ptr, &passwordBytes, sizeof(DWORD));
        ptr += sizeof(DWORD);

        memcpy(ptr, password.data(), passwordBytes);

        return true;
    }

    bool BuildSecretPayload(
        const std::wstring& /*profileId*/,
        const std::wstring& password,
        std::vector<BYTE>& outPayload
    )
    {
        // Writers always write v2
        return BuildSecretPayload(password, outPayload);
    }

    bool BuildSecretPayloadV1(
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

        DWORD version = LEGACY_PAYLOAD_VERSION; // 1
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
        DWORD& outVersion,
        std::wstring& outProfileId,
        std::wstring& outPassword
    )
    {
        outVersion = 0;
        outProfileId.clear();
        outPassword.clear();

        if (!pbData || cbData < sizeof(DWORD) * 2 || cbData > LSA_SECRET_MAX_SIZE)
        {
            return false;
        }

        const BYTE* ptr = pbData;
        DWORD version = 0;
        memcpy(&version, ptr, sizeof(DWORD));
        ptr += sizeof(DWORD);

        if (version == LEGACY_PAYLOAD_VERSION) // 1
        {
            if (cbData < sizeof(DWORD) * 3)
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
            outVersion = 1;
            return true;
        }
        else if (version == CURRENT_PAYLOAD_VERSION) // 2
        {
            DWORD passwordBytes = 0;
            memcpy(&passwordBytes, ptr, sizeof(DWORD));
            ptr += sizeof(DWORD);

            if (passwordBytes == 0 || (passwordBytes % sizeof(wchar_t)) != 0)
            {
                return false;
            }

            DWORD expectedRemaining = cbData - (sizeof(DWORD) * 2);
            if (passwordBytes != expectedRemaining)
            {
                return false;
            }

            outPassword.assign(reinterpret_cast<const wchar_t*>(ptr), passwordBytes / sizeof(wchar_t));
            outVersion = 2;
            outProfileId.clear();
            return true;
        }

        return false;
    }

    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        std::wstring& outProfileId,
        std::wstring& outPassword
    )
    {
        DWORD version = 0;
        return ParseSecretPayload(pbData, cbData, version, outProfileId, outPassword);
    }

    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        const std::wstring& expectedProfileId,
        std::wstring& outPassword
    )
    {
        DWORD version = 0;
        std::wstring storedProfileId;
        if (!ParseSecretPayload(pbData, cbData, version, storedProfileId, outPassword))
        {
            return false;
        }

        if (version == LEGACY_PAYLOAD_VERSION)
        {
            if (storedProfileId != expectedProfileId)
            {
                if (!outPassword.empty())
                {
                    SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                    outPassword.clear();
                }
                return false;
            }
        }
        // For v2, authorization is verified in ReadPasswordSecret via IIdentityStore

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

        DWORD version = 0;
        std::wstring dummyPassword;
        bool ok = ParseSecretPayload(secretData.data(), static_cast<DWORD>(secretData.size()), version, outProfileId, dummyPassword);
        if (!dummyPassword.empty())
        {
            SecureZeroMemory(&dummyPassword[0], dummyPassword.size() * sizeof(wchar_t));
        }
        if (!secretData.empty())
        {
            SecureZeroMemory(secretData.data(), secretData.size());
        }

        if (ok && version == CURRENT_PAYLOAD_VERSION)
        {
            // For v2, profile ID is not stored in secret payload; retrieve first authorized identity
            auto idStore = GetIdentityStore();
            std::vector<std::wstring> ids;
            if (idStore && idStore->GetAuthorizedIdentities(userSid, ids) && !ids.empty())
            {
                outProfileId = ids[0];
            }
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

        DWORD version = 0;
        std::wstring storedProfileId;
        bool ok = ParseSecretPayload(secretData.data(), static_cast<DWORD>(secretData.size()), version, storedProfileId, outPassword);
        if (!secretData.empty())
        {
            SecureZeroMemory(secretData.data(), secretData.size());
        }
        if (ok)
        {
            if (version == LEGACY_PAYLOAD_VERSION)
            {
                if (pOutProfileId) *pOutProfileId = storedProfileId;
                return true;
            }
            else if (version == CURRENT_PAYLOAD_VERSION)
            {
                if (pOutProfileId)
                {
                    auto idStore = GetIdentityStore();
                    std::vector<std::wstring> ids;
                    if (idStore && idStore->GetAuthorizedIdentities(userSid, ids) && !ids.empty())
                    {
                        *pOutProfileId = ids[0];
                    }
                }
                return true;
            }
        }
        return false;
    }

    bool ReadPasswordSecret(
        const std::wstring& userSid,
        const std::wstring& matchedProfileId,
        std::wstring& outPassword,
        ILsaSecretStore* pStore,
        IIdentityStore* pIdStore
    )
    {
        outPassword.clear();
        if (userSid.empty())
        {
            Logger::LogInfo(L"ReadPasswordSecret failed: empty user SID");
            return false;
        }

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
            Logger::LogInfo(L"Authorization failed: No LSA secret found for user");
            Logger::LogDebug((L"No secret for SID: " + userSid).c_str());
            return false;
        }

        DWORD version = 0;
        std::wstring storedProfileId;
        bool ok = ParseSecretPayload(secretData.data(), static_cast<DWORD>(secretData.size()), version, storedProfileId, outPassword);
        if (!secretData.empty())
        {
            SecureZeroMemory(secretData.data(), secretData.size());
        }

        if (!ok)
        {
            Logger::LogInfo(L"Authorization failed: Corrupt or invalid LSA secret payload");
            Logger::LogDebug((L"Corrupt payload for SID: " + userSid).c_str());
            if (!outPassword.empty())
            {
                SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                outPassword.clear();
            }
            return false;
        }

        if (version == LEGACY_PAYLOAD_VERSION) // v1
        {
            // v1 requires equality with the stored id
            if (!matchedProfileId.empty() && storedProfileId != matchedProfileId)
            {
                Logger::LogInfo(L"Authorization failed: v1 profile binding mismatch");
                Logger::LogDebug((L"Mismatch for SID: " + userSid).c_str());
                if (!outPassword.empty())
                {
                    SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                    outPassword.clear();
                }
                return false;
            }
            return true;
        }
        else if (version == CURRENT_PAYLOAD_VERSION) // v2
        {
            // v2 requires matched profile_id to be in the Authorized list for the locked user's SID
            if (matchedProfileId.empty())
            {
                Logger::LogInfo(L"Authorization failed: Empty matched profile ID for v2 secret");
                Logger::LogDebug((L"Empty profile ID for SID: " + userSid).c_str());
                if (!outPassword.empty())
                {
                    SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                    outPassword.clear();
                }
                return false;
            }

            std::shared_ptr<IIdentityStore> spIdStore;
            if (!pIdStore)
            {
                spIdStore = GetIdentityStore();
                pIdStore = spIdStore.get();
            }
            if (!pIdStore)
            {
                if (!outPassword.empty())
                {
                    SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                    outPassword.clear();
                }
                return false;
            }

            std::vector<std::wstring> authorizedIds;
            bool keyExists = pIdStore->GetAuthorizedIdentities(userSid, authorizedIds);
            if (!keyExists)
            {
                Logger::LogInfo(L"Authorization failed: Identities key missing for user");
                Logger::LogDebug((L"Identities key missing for SID: " + userSid).c_str());
                if (!outPassword.empty())
                {
                    SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                    outPassword.clear();
                }
                return false;
            }

            bool isAuthorized = false;
            for (const auto& id : authorizedIds)
            {
                if (id == matchedProfileId)
                {
                    isAuthorized = true;
                    break;
                }
            }

            if (!isAuthorized)
            {
                Logger::LogInfo(L"Authorization failed: Profile not in Authorized identities list");
                Logger::LogDebug((L"Profile " + matchedProfileId + L" not in Authorized list for SID: " + userSid).c_str());
                if (!outPassword.empty())
                {
                    SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
                    outPassword.clear();
                }
                return false;
            }

            return true;
        }

        if (!outPassword.empty())
        {
            SecureZeroMemory(&outPassword[0], outPassword.size() * sizeof(wchar_t));
            outPassword.clear();
        }
        return false;
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
        const std::wstring& password,
        ILsaSecretStore* pStore
    )
    {
        if (userSid.empty() || password.empty())
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
        if (!BuildSecretPayload(password, payload))
        {
            return false;
        }

        std::wstring keyName = GetSecretKeyName(userSid);
        NTSTATUS status = pStore->StoreSecret(keyName, payload.data(), static_cast<DWORD>(payload.size()));
        SecureZeroMemory(payload.data(), payload.size());
        return (status == STATUS_SUCCESS);
    }

    bool StorePasswordSecret(
        const std::wstring& userSid,
        const std::wstring& /*profileId*/,
        const std::wstring& password,
        ILsaSecretStore* pStore
    )
    {
        // Writers always write v2
        return StorePasswordSecret(userSid, password, pStore);
    }

    bool StorePasswordSecretV1(
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
        if (!BuildSecretPayloadV1(profileId, password, payload))
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
