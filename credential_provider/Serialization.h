// Peek Face Engine - Credential Serialization
// Handles packaging credentials for Windows LogonUI / LSA (Local Security Authority)
#pragma once

#include "common.h"

/**
 * ARCHITECTURAL DECISION & SECURITY ANALYSIS:
 * -------------------------------------------
 * Approach: Password-Equivalent Packed Credential (KERB_INTERACTIVE_LOGON)
 *
 * Evaluation:
 * 1. Approach A (Chosen for Phase 5):
 *    - Uses standard Windows KERB_INTERACTIVE_LOGON (or MSV1_0_INTERACTIVE_LOGON)
 *      submitted to the Negotiate / Kerberos LSA package.
 *    - Standard Microsoft contract for third-party biometric, smartcard, and
 *      facial recognition providers (e.g. Windows Hello companion providers).
 *    - Rationale: Fully supported on all Windows 10/11 versions without requiring
 *      custom kernel-mode drivers or an in-process LSA security package (which
 *      runs inside lsass.exe and risks OS-wide crash / BSOD).
 *
 * 2. Approach B (Custom LSA Authentication Package - Future Phase 7 Evaluation):
 *    - Involves registering a custom native package in HKLM\SYSTEM\CurrentControlSet\Control\Lsa.
 *    - Trade-offs: Requires WHQL/Authenticode kernel-level code signing for LSA Protection
 *      (RunAsPPL). A crash in custom LSA code terminates lsass.exe and forces immediate
 *      system reboot.
 *
 * 3. Fail-Open Invariant:
 *    - If biometric authentication completes but credentials cannot be serialized
 *      (e.g. first logon or expired cached token), the provider returns
 *      CPGSR_NO_CREDENTIAL_FINISHED, which seamlessly falls back to standard
 *      Windows password/PIN logon without locking the user out.
 */

namespace PeekSerialization
{
    // Resolves the Negotiate/Kerberos LSA Authentication Package ID
    HRESULT GetNegotiateAuthPackage(ULONG* pPackageId);

    // Packages credentials into a KERB_INTERACTIVE_LOGON structure allocated via CoTaskMemAlloc
    HRESULT PackageKerbLogon(
        PCWSTR pszDomain,
        PCWSTR pszUsername,
        PCWSTR pszPassword,
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs
    );

    // Clears and zeroes credential serialization buffers securely
    void WipeSerialization(CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs);
}
