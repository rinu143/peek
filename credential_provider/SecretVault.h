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
    constexpr DWORD CURRENT_PAYLOAD_VERSION = 1;

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

    // Builds the binary payload:
    // [DWORD version (=1)]
    // [DWORD profileIdBytes] + [UTF-16LE profile_id]
    // [DWORD passwordBytes] + [UTF-16LE password]
    bool BuildSecretPayload(
        const std::wstring& profileId,
        const std::wstring& password,
        std::vector<BYTE>& outPayload
    );

    // Parses and validates binary payload:
    // Rejects invalid version, truncated length, size > 4KB, or profile_id mismatch.
    bool ParseSecretPayload(
        const BYTE* pbData,
        DWORD cbData,
        const std::wstring& expectedProfileId,
        std::wstring& outPassword
    );

    // Formats the LSA secret key name: L$PeekCred_<user SID string>
    std::wstring GetSecretKeyName(const std::wstring& userSid);

    // Reads and unwraps the password secret for the given user SID and profile ID.
    // Password buffer is populated only if SID matches, payload is valid, and profile_id binding matches.
    // All temporary buffers are zeroed before return.
    bool ReadPasswordSecret(
        const std::wstring& userSid,
        const std::wstring& expectedProfileId,
        std::wstring& outPassword,
        ILsaSecretStore* pStore = nullptr
    );

    // Deletes the LSA secret for the given user SID. Idempotent.
    bool DeletePasswordSecret(
        const std::wstring& userSid,
        ILsaSecretStore* pStore = nullptr
    );

    // Stores an LSA secret under L$PeekCred_<userSid>.
    bool StorePasswordSecret(
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
