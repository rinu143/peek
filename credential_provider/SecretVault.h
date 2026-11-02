// Peek Face Engine - Secret Vault Header
// Manages LSA private data secrets for credential provider

#pragma once

#include <windows.h>
#include <ntsecapi.h>
#include <string>
#include <vector>
#include <memory>
#include <map>

namespace PeekSecretVault
{
    constexpr DWORD LSA_SECRET_MAX_SIZE = 4096;
    constexpr DWORD CURRENT_PAYLOAD_VERSION = 2;
    constexpr DWORD LEGACY_PAYLOAD_VERSION = 1;
    constexpr DWORD MAX_AUTHORIZED_IDENTITIES = 5;

    // Interface for identity authorization store (HKLM\SOFTWARE\Peek\Identities\<user SID>)
    class IIdentityStore
    {
    public:
        virtual ~IIdentityStore() = default;
        // Returns true if the Identities key exists, false if it does not exist (legacy v1 install)
        virtual bool GetAuthorizedIdentities(const std::wstring& userSid, std::vector<std::wstring>& outProfileIds) = 0;
    };

    // Real Windows registry implementation
    class RealIdentityStore : public IIdentityStore
    {
    public:
        bool GetAuthorizedIdentities(const std::wstring& userSid, std::vector<std::wstring>& outProfileIds) override;
    };

    // In-memory fake implementation for testing
    class InMemoryIdentityStore : public IIdentityStore
    {
    public:
        bool GetAuthorizedIdentities(const std::wstring& userSid, std::vector<std::wstring>& outProfileIds) override;
        void SetAuthorizedIdentities(const std::wstring& userSid, const std::vector<std::wstring>& profileIds);
        void SetKeyExists(const std::wstring& userSid, bool exists);
        void RemoveIdentities(const std::wstring& userSid);
        void Clear();
    private:
        std::map<std::wstring, std::vector<std::wstring>> m_identities;
        std::map<std::wstring, bool> m_keyExists;
    };

    void SetIdentityStore(std::shared_ptr<IIdentityStore> store);
    std::shared_ptr<IIdentityStore> GetIdentityStore();

    // Interface for LSA secret store operations (allows in-memory mocking for unit tests)
    class ILsaSecretStore
    {
    public:
        virtual ~ILsaSecretStore() = default;
        virtual NTSTATUS RetrieveSecret(const std::wstring& secretName, std::vector<BYTE>& outData) = 0;
        virtual NTSTATUS StoreSecret(const std::wstring& secretName, const BYTE* pbData, DWORD cbData) = 0;
        virtual NTSTATUS DeleteSecret(const std::wstring& secretName) = 0;
    };

    // Real Windows LSA private-data implementation
    class RealLsaSecretStore : public ILsaSecretStore
    {
    public:
        NTSTATUS RetrieveSecret(const std::wstring& secretName, std::vector<BYTE>& outData) override;
        NTSTATUS StoreSecret(const std::wstring& secretName, const BYTE* pbData, DWORD cbData) override;
        NTSTATUS DeleteSecret(const std::wstring& secretName) override;
    };

    // In-memory fake implementation for testing
    class InMemoryLsaSecretStore : public ILsaSecretStore
    {
    public:
        NTSTATUS RetrieveSecret(const std::wstring& secretName, std::vector<BYTE>& outData) override;
        NTSTATUS StoreSecret(const std::wstring& secretName, const BYTE* pbData, DWORD cbData) override;
        NTSTATUS DeleteSecret(const std::wstring& secretName) override;
        void Clear();
        bool HasSecret(const std::wstring& secretName) const;
    private:
        std::map<std::wstring, std::vector<BYTE>> m_secrets;
    };

    // Set or get active store (defaults to RealLsaSecretStore if not set)
    void SetLsaSecretStore(std::shared_ptr<ILsaSecretStore> store);
    std::shared_ptr<ILsaSecretStore> GetLsaSecretStore();

    // Password validator function signature: returns true if valid, false otherwise.
    // If provided, pdwErrorCode receives the Win32 error code (e.g. ERROR_LOGON_FAILURE, ERROR_ACCOUNT_LOCKED_OUT).
    using PasswordValidatorFn = bool(*)(PCWSTR pszUsername, PCWSTR pszDomain, PCWSTR pszPassword, DWORD* pdwErrorCode);

    // Set or get active password validator (defaults to standard LogonUserW validator)
    void SetPasswordValidator(PasswordValidatorFn fn);
    PasswordValidatorFn GetPasswordValidator();

    // Builds the binary payload v2:
    // [DWORD version (=2)]
    // [DWORD passwordBytes] + [UTF-16LE password]
    bool BuildSecretPayload(
        const std::wstring& password,
        std::vector<BYTE>& outPayload
    );

    // Backward-compatible overload: builds v2 payload (ignores profileId for v2)
    bool BuildSecretPayload(
        const std::wstring& profileId,
        const std::wstring& password,
        std::vector<BYTE>& outPayload
    );

    // Builds binary payload v1 for backward-compatibility testing:
    // [DWORD version (=1)]
    // [DWORD profileIdBytes] + [UTF-16LE profile_id]
    // [DWORD passwordBytes] + [UTF-16LE password]
    bool BuildSecretPayloadV1(
        const std::wstring& profileId,
        const std::wstring& password,
        std::vector<BYTE>& outPayload
    );

    // Parses and validates binary payload, extracting version, profileId (if v1), and password:
    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        DWORD& outVersion,
        std::wstring& outProfileId,
        std::wstring& outPassword
    );

    // Parses binary payload (version 1 or 2):
    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        std::wstring& outProfileId,
        std::wstring& outPassword
    );

    // Parses and validates binary payload enforcing expected profileId:
    // For v1: requires equality with stored profile_id.
    // For v2: extracts password cleanly (identity authorization check is performed via IIdentityStore).
    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        const std::wstring& expectedProfileId,
        std::wstring& outPassword
    );

    // Retrieves stored bound profile ID directly from LSA payload for user SID (v1 only, or first authorized for v2).
    bool GetStoredProfileId(
        const std::wstring& userSid,
        std::wstring& outProfileId,
        ILsaSecretStore* pStore = nullptr
    );

    // Reads and unwraps the password secret directly from LSA without requiring pipe profile ID (v1 fallback).
    bool ReadPasswordSecret(
        const std::wstring& userSid,
        std::wstring& outPassword,
        std::wstring* pOutProfileId = nullptr,
        ILsaSecretStore* pStore = nullptr
    );

    // Formats the LSA secret key name: L$PeekCred_<user SID string>
    std::wstring GetSecretKeyName(const std::wstring& userSid);

    // Reads and unwraps the password secret for the given user SID and matched profile ID.
    // v2: requires matchedProfileId to be in the Authorized list for user SID (from registry).
    // v1: requires equality with the stored profile ID.
    // Otherwise fails open and logs the reason (no secrets, SIDs only at DEBUG).
    bool ReadPasswordSecret(
        const std::wstring& userSid,
        const std::wstring& matchedProfileId,
        std::wstring& outPassword,
        ILsaSecretStore* pStore = nullptr,
        IIdentityStore* pIdStore = nullptr
    );

    // Deletes the LSA secret for the given user SID. Idempotent.
    bool DeletePasswordSecret(
        const std::wstring& userSid,
        ILsaSecretStore* pStore = nullptr
    );

    // Stores an LSA secret under L$PeekCred_<userSid> (always writes v2).
    bool StorePasswordSecret(
        const std::wstring& userSid,
        const std::wstring& password,
        ILsaSecretStore* pStore = nullptr
    );

    // Overload for backward compatibility; always writes v2.
    bool StorePasswordSecret(
        const std::wstring& userSid,
        const std::wstring& profileId,
        const std::wstring& password,
        ILsaSecretStore* pStore = nullptr
    );

    // Explicitly stores a v1 secret for testing legacy fallback.
    bool StorePasswordSecretV1(
        const std::wstring& userSid,
        const std::wstring& profileId,
        const std::wstring& password,
        ILsaSecretStore* pStore = nullptr
    );

    // Default password validator using LogonUserW.
    bool ValidatePassword(
        PCWSTR pszUsername,
        PCWSTR pszDomain,
        PCWSTR pszPassword,
        DWORD* pdwErrorCode = nullptr
    );
}
