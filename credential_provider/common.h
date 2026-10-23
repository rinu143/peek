// Peek Face Engine - Common Definitions & Macros
// Phase 5 Native Credential Provider
#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>
#include <credentialprovider.h>
#include <ntsecapi.h>
#include <shlwapi.h>
#include <shlobj.h>
#include <shlguid.h>
#include <strsafe.h>
#include <string>
#include <vector>
#include <memory>
#include <wtsapi32.h>
#include <sddl.h>

#include "guid.h"

#include <objbase.h>

// Helper to convert GUID/IID to string via StringFromIID
inline std::wstring GuidToString(REFGUID guid)
{
    LPOLESTR psz = nullptr;
    HRESULT hr = StringFromIID(guid, &psz);
    std::wstring result = (SUCCEEDED(hr) && psz) ? psz : L"(null)";
    if (psz) CoTaskMemFree(psz);
    return result;
}

namespace PeekLoggerInternal
{
    inline CRITICAL_SECTION* GetLock()
    {
        static CRITICAL_SECTION s_cs;
        static INIT_ONCE s_initOnce = INIT_ONCE_STATIC_INIT;
        InitOnceExecuteOnce(&s_initOnce, [](PINIT_ONCE, PVOID, PVOID*) -> BOOL {
            InitializeCriticalSection(&s_cs);
            return TRUE;
        }, NULL, NULL);
        return &s_cs;
    }

    inline std::wstring GetLogFilePath()
    {
        wchar_t szPath[MAX_PATH] = {};
        if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_COMMON_APPDATA, NULL, 0, szPath)) && szPath[0] != L'\0')
        {
            return std::wstring(szPath) + L"\\Peek\\logs\\credential_provider.log";
        }
        DWORD dwLen = GetEnvironmentVariableW(L"ProgramData", szPath, ARRAYSIZE(szPath));
        if (dwLen > 0 && dwLen < ARRAYSIZE(szPath))
        {
            return std::wstring(szPath) + L"\\Peek\\logs\\credential_provider.log";
        }
        return L"C:\\ProgramData\\Peek\\logs\\credential_provider.log";
    }

    inline void WriteToFileInner(const wchar_t* pwszLevel, const wchar_t* pwszMsg)
    {
        try
        {
            CRITICAL_SECTION* pCS = GetLock();
            EnterCriticalSection(pCS);
            struct AutoLeave {
                CRITICAL_SECTION* cs;
                ~AutoLeave() { LeaveCriticalSection(cs); }
            } guard{ pCS };

            std::wstring logFilePath = GetLogFilePath();

            // Cap file size: rotate to .old at ~1 MB (1024 * 1024)
            WIN32_FILE_ATTRIBUTE_DATA fad = {};
            if (GetFileAttributesExW(logFilePath.c_str(), GetFileExInfoStandard, &fad))
            {
                if (fad.nFileSizeHigh > 0 || fad.nFileSizeLow >= (1024 * 1024))
                {
                    std::wstring oldPath = logFilePath + L".old";
                    DeleteFileW(oldPath.c_str());
                    MoveFileW(logFilePath.c_str(), oldPath.c_str());
                }
            }

            // Open or create log file
            HANDLE hFile = CreateFileW(
                logFilePath.c_str(),
                FILE_APPEND_DATA,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                NULL,
                OPEN_ALWAYS,
                FILE_ATTRIBUTE_NORMAL,
                NULL
            );

            if (hFile == INVALID_HANDLE_VALUE)
            {
                // If path missing, create directory and retry once
                if (GetLastError() == ERROR_PATH_NOT_FOUND)
                {
                    size_t lastSlash = logFilePath.find_last_of(L"\\/");
                    if (lastSlash != std::wstring::npos)
                    {
                        std::wstring dir = logFilePath.substr(0, lastSlash);
                        SHCreateDirectoryExW(NULL, dir.c_str(), NULL);
                    }

                    hFile = CreateFileW(
                        logFilePath.c_str(),
                        FILE_APPEND_DATA,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        NULL,
                        OPEN_ALWAYS,
                        FILE_ATTRIBUTE_NORMAL,
                        NULL
                    );
                }
            }

            if (hFile != INVALID_HANDLE_VALUE)
            {
                // Timestamp with ms: YYYY-MM-DD HH:MM:SS.mmm
                SYSTEMTIME st;
                GetLocalTime(&st);
                wchar_t szTime[32] = {};
                swprintf_s(szTime, ARRAYSIZE(szTime), L"%04u-%02u-%02u %02u:%02u:%02u.%03u",
                    st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);

                // Process info
                DWORD pid = GetCurrentProcessId();
                DWORD tid = GetCurrentThreadId();

                // Process image name
                wchar_t szExePath[MAX_PATH] = {};
                const wchar_t* pszExeName = L"unknown";
                if (GetModuleFileNameW(NULL, szExePath, ARRAYSIZE(szExePath)))
                {
                    wchar_t* pSlash = wcsrchr(szExePath, L'\\');
                    if (pSlash) pszExeName = pSlash + 1;
                    else
                    {
                        pSlash = wcsrchr(szExePath, L'/');
                        if (pSlash) pszExeName = pSlash + 1;
                        else pszExeName = szExePath;
                    }
                }

                // Format log line:
                // [YYYY-MM-DD HH:MM:SS.mmm] [PID:1234 LogonUI.exe] [TID:5678] [INFO] message\r\n
                std::wstring line;
                line.reserve(512 + (pwszMsg ? wcslen(pwszMsg) : 0));
                wchar_t szHeader[256] = {};
                swprintf_s(szHeader, ARRAYSIZE(szHeader), L"[%s] [PID:%lu %s] [TID:%lu] [%s] ",
                    szTime, pid, pszExeName, tid, pwszLevel ? pwszLevel : L"INFO");
                line += szHeader;
                if (pwszMsg) line += pwszMsg;
                line += L"\r\n";

                // Convert to UTF-8
                int cbUtf8 = WideCharToMultiByte(CP_UTF8, 0, line.c_str(), static_cast<int>(line.size()), NULL, 0, NULL, NULL);
                if (cbUtf8 > 0)
                {
                    std::string utf8Str(cbUtf8, '\0');
                    WideCharToMultiByte(CP_UTF8, 0, line.c_str(), static_cast<int>(line.size()), &utf8Str[0], cbUtf8, NULL, NULL);
                    DWORD bytesWritten = 0;
                    WriteFile(hFile, utf8Str.data(), static_cast<DWORD>(utf8Str.size()), &bytesWritten, NULL);
                }

                CloseHandle(hFile);
            }
        }
        catch (...)
        {
            // Best-effort: swallow any C++ exceptions
        }
    }

    inline void SafeWriteToFile(const wchar_t* pwszLevel, const wchar_t* pwszMsg)
    {
        __try
        {
            WriteToFileInner(pwszLevel, pwszMsg);
        }
        __except (EXCEPTION_EXECUTE_HANDLER)
        {
            // Best-effort: swallow any SEH exceptions, never crash LogonUI
        }
    }
}

// Lightweight debug & persistent file logger for Credential Provider
class Logger
{
public:
    static void LogInfo(const char* pszMsg)
    {
        if (pszMsg)
        {
            OutputDebugStringA("[PeekCP][INFO] ");
            OutputDebugStringA(pszMsg);
            OutputDebugStringA("\n");

            int cchW = MultiByteToWideChar(CP_ACP, 0, pszMsg, -1, NULL, 0);
            if (cchW > 0)
            {
                std::vector<wchar_t> wbuf(cchW);
                MultiByteToWideChar(CP_ACP, 0, pszMsg, -1, wbuf.data(), cchW);
                PeekLoggerInternal::SafeWriteToFile(L"INFO", wbuf.data());
            }
        }
    }
    static void LogInfo(const wchar_t* pwszMsg)
    {
        if (pwszMsg)
        {
            OutputDebugStringW(L"[PeekCP][INFO] ");
            OutputDebugStringW(pwszMsg);
            OutputDebugStringW(L"\n");
            PeekLoggerInternal::SafeWriteToFile(L"INFO", pwszMsg);
        }
    }
    static void LogInfo(const std::wstring& msg)
    {
        LogInfo(msg.c_str());
    }
    static void LogError(const char* pszMsg)
    {
        if (pszMsg)
        {
            OutputDebugStringA("[PeekCP][ERROR] ");
            OutputDebugStringA(pszMsg);
            OutputDebugStringA("\n");

            int cchW = MultiByteToWideChar(CP_ACP, 0, pszMsg, -1, NULL, 0);
            if (cchW > 0)
            {
                std::vector<wchar_t> wbuf(cchW);
                MultiByteToWideChar(CP_ACP, 0, pszMsg, -1, wbuf.data(), cchW);
                PeekLoggerInternal::SafeWriteToFile(L"ERROR", wbuf.data());
            }
        }
    }
    static void LogError(const wchar_t* pwszMsg)
    {
        if (pwszMsg)
        {
            OutputDebugStringW(L"[PeekCP][ERROR] ");
            OutputDebugStringW(pwszMsg);
            OutputDebugStringW(L"\n");
            PeekLoggerInternal::SafeWriteToFile(L"ERROR", pwszMsg);
        }
    }
    static void LogError(const std::wstring& msg)
    {
        LogError(msg.c_str());
    }
};

namespace PeekDiagnostics
{
    // RAII guard ensuring privilege adjustment is restored on every exit path
    class PrivilegeAdjustmentGuard
    {
    public:
        PrivilegeAdjustmentGuard(HANDLE hToken, const TOKEN_PRIVILEGES& prevPriv, bool bNeedsRevert)
            : m_hToken(hToken), m_prevPriv(prevPriv), m_bNeedsRevert(bNeedsRevert)
        {
        }

        ~PrivilegeAdjustmentGuard()
        {
            Revert();
        }

        void Revert()
        {
            if (m_bNeedsRevert && m_hToken)
            {
                SetLastError(ERROR_SUCCESS);
                BOOL bOk = AdjustTokenPrivileges(m_hToken, FALSE, const_cast<TOKEN_PRIVILEGES*>(&m_prevPriv), 0, NULL, NULL);
                DWORD dwErr = GetLastError();
                wchar_t szBuf[128] = {};
                swprintf_s(szBuf, ARRAYSIZE(szBuf),
                    L"[PeekDiag] PrivilegeAdjustmentGuard: Reverted SeTcbPrivilege to previous state (ok=%d, err=%u)",
                    bOk, dwErr);
                Logger::LogInfo(szBuf);
                m_bNeedsRevert = false;
            }
        }

        PrivilegeAdjustmentGuard(const PrivilegeAdjustmentGuard&) = delete;
        PrivilegeAdjustmentGuard& operator=(const PrivilegeAdjustmentGuard&) = delete;

    private:
        HANDLE m_hToken;
        TOKEN_PRIVILEGES m_prevPriv;
        bool m_bNeedsRevert;
    };

    inline const wchar_t* GetIntegrityLevelName(DWORD dwIntegrityLevel)
    {
        if (dwIntegrityLevel >= SECURITY_MANDATORY_PROTECTED_PROCESS_RID) return L"PROTECTED_PROCESS (0x5000+)";
        if (dwIntegrityLevel >= SECURITY_MANDATORY_SYSTEM_RID) return L"SYSTEM (0x4000)";
        if (dwIntegrityLevel >= SECURITY_MANDATORY_HIGH_RID) return L"HIGH (0x3000)";
        if (dwIntegrityLevel >= SECURITY_MANDATORY_MEDIUM_RID) return L"MEDIUM (0x2000)";
        if (dwIntegrityLevel >= SECURITY_MANDATORY_LOW_RID) return L"LOW (0x1000)";
        if (dwIntegrityLevel >= SECURITY_MANDATORY_UNTRUSTED_RID) return L"UNTRUSTED (0x0000)";
        return L"UNKNOWN";
    }

    inline void RunProcessTokenDiagnostics(PCWSTR pszCallerContext, DWORD dwSessionId = 0xFFFFFFFF)
    {
        try
        {
            wchar_t szHeader[256] = {};
            swprintf_s(szHeader, ARRAYSIZE(szHeader),
                L"=== [PeekDiag] Process Token Diagnostics START: Caller='%s', SessionHint=%u ===",
                pszCallerContext ? pszCallerContext : L"Unknown", dwSessionId);
            Logger::LogInfo(szHeader);

            HANDLE hToken = NULL;
            // Open process token for query and adjust privileges
            if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES, &hToken))
            {
                DWORD dwErr = GetLastError();
                if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &hToken))
                {
                    wchar_t szErr[128] = {};
                    swprintf_s(szErr, ARRAYSIZE(szErr),
                        L"[PeekDiag] OpenProcessToken failed completely, error=%u", GetLastError());
                    Logger::LogError(szErr);
                    return;
                }
                wchar_t szWarn[128] = {};
                swprintf_s(szWarn, ARRAYSIZE(szWarn),
                    L"[PeekDiag] OpenProcessToken succeeded with TOKEN_QUERY only (ADJUST_PRIVILEGES error=%u)", dwErr);
                Logger::LogInfo(szWarn);
            }

            // 1. Process Token User (SID / Account name)
            DWORD dwLen = 0;
            GetTokenInformation(hToken, TokenUser, NULL, 0, &dwLen);
            if (GetLastError() == ERROR_INSUFFICIENT_BUFFER && dwLen > 0)
            {
                std::vector<BYTE> buf(dwLen);
                if (GetTokenInformation(hToken, TokenUser, buf.data(), dwLen, &dwLen))
                {
                    TOKEN_USER* pTokenUser = reinterpret_cast<TOKEN_USER*>(buf.data());
                    LPWSTR pszSid = nullptr;
                    std::wstring strSid = L"(unknown)";
                    if (ConvertSidToStringSidW(pTokenUser->User.Sid, &pszSid) && pszSid)
                    {
                        strSid = pszSid;
                        LocalFree(pszSid);
                    }

                    wchar_t szName[256] = {};
                    wchar_t szDomain[256] = {};
                    DWORD cchName = ARRAYSIZE(szName);
                    DWORD cchDomain = ARRAYSIZE(szDomain);
                    SID_NAME_USE snu;
                    std::wstring strAccount = L"(unknown)";
                    if (LookupAccountSidW(NULL, pTokenUser->User.Sid, szName, &cchName, szDomain, &cchDomain, &snu))
                    {
                        if (szDomain[0] != L'\0')
                        {
                            strAccount = szDomain;
                            strAccount += L"\\";
                        }
                        else
                        {
                            strAccount.clear();
                        }
                        strAccount += szName;
                    }

                    wchar_t szUserMsg[512] = {};
                    swprintf_s(szUserMsg, ARRAYSIZE(szUserMsg),
                        L"[PeekDiag] Process Token User: SID='%s', Account='%s'",
                        strSid.c_str(), strAccount.c_str());
                    Logger::LogInfo(szUserMsg);
                }
            }

            // 2. Process Integrity Level
            dwLen = 0;
            GetTokenInformation(hToken, TokenIntegrityLevel, NULL, 0, &dwLen);
            if (GetLastError() == ERROR_INSUFFICIENT_BUFFER && dwLen > 0)
            {
                std::vector<BYTE> buf(dwLen);
                if (GetTokenInformation(hToken, TokenIntegrityLevel, buf.data(), dwLen, &dwLen))
                {
                    TOKEN_MANDATORY_LABEL* pLabel = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buf.data());
                    if (pLabel->Label.Sid)
                    {
                        UCHAR* pSubAuthCount = GetSidSubAuthorityCount(pLabel->Label.Sid);
                        if (pSubAuthCount && *pSubAuthCount > 0)
                        {
                            DWORD dwIntegrityLevel = *GetSidSubAuthority(pLabel->Label.Sid, (DWORD)(*pSubAuthCount - 1));
                            wchar_t szIlMsg[256] = {};
                            swprintf_s(szIlMsg, ARRAYSIZE(szIlMsg),
                                L"[PeekDiag] Process Token Integrity Level: 0x%04X (%s)",
                                dwIntegrityLevel, GetIntegrityLevelName(dwIntegrityLevel));
                            Logger::LogInfo(szIlMsg);
                        }
                    }
                }
            }

            // 3. Token Privileges list
            dwLen = 0;
            GetTokenInformation(hToken, TokenPrivileges, NULL, 0, &dwLen);
            if (GetLastError() == ERROR_INSUFFICIENT_BUFFER && dwLen > 0)
            {
                std::vector<BYTE> buf(dwLen);
                if (GetTokenInformation(hToken, TokenPrivileges, buf.data(), dwLen, &dwLen))
                {
                    TOKEN_PRIVILEGES* pPrivs = reinterpret_cast<TOKEN_PRIVILEGES*>(buf.data());
                    wchar_t szCountMsg[128] = {};
                    swprintf_s(szCountMsg, ARRAYSIZE(szCountMsg),
                        L"[PeekDiag] Process Token Privileges count=%u:", pPrivs->PrivilegeCount);
                    Logger::LogInfo(szCountMsg);

                    for (DWORD i = 0; i < pPrivs->PrivilegeCount; i++)
                    {
                        wchar_t szPrivName[128] = {};
                        DWORD cchPrivName = ARRAYSIZE(szPrivName);
                        if (!LookupPrivilegeNameW(NULL, &pPrivs->Privileges[i].Luid, szPrivName, &cchPrivName))
                        {
                            swprintf_s(szPrivName, ARRAYSIZE(szPrivName), L"LUID(0x%08X:0x%08X)",
                                pPrivs->Privileges[i].Luid.HighPart, pPrivs->Privileges[i].Luid.LowPart);
                        }

                        bool bEnabled = (pPrivs->Privileges[i].Attributes & SE_PRIVILEGE_ENABLED) != 0;
                        bool bDefault = (pPrivs->Privileges[i].Attributes & SE_PRIVILEGE_ENABLED_BY_DEFAULT) != 0;

                        bool bIsTcb = (_wcsicmp(szPrivName, SE_TCB_NAME) == 0);
                        bool bIsImpersonate = (_wcsicmp(szPrivName, SE_IMPERSONATE_NAME) == 0);
                        bool bIsAssignPrimary = (_wcsicmp(szPrivName, SE_ASSIGNPRIMARYTOKEN_NAME) == 0);
                        bool bIsIncreaseQuota = (_wcsicmp(szPrivName, SE_INCREASE_QUOTA_NAME) == 0);

                        const wchar_t* pszHighlight = L"";
                        if (bIsTcb || bIsImpersonate || bIsAssignPrimary || bIsIncreaseQuota)
                        {
                            pszHighlight = L" *** HIGHLIGHTED ***";
                        }

                        wchar_t szLine[384] = {};
                        swprintf_s(szLine, ARRAYSIZE(szLine),
                            L"  [PeekDiag] [%02u] %-32s: Enabled=%d, Default=%d, Attr=0x%08X%s",
                            i, szPrivName, bEnabled ? 1 : 0, bDefault ? 1 : 0,
                            pPrivs->Privileges[i].Attributes, pszHighlight);
                        Logger::LogInfo(szLine);
                    }
                }
            }

            // 4. Try AdjustTokenPrivileges to enable SeTcbPrivilege & retry WTSQueryUserToken
            LUID luidTcb = {};
            if (LookupPrivilegeValueW(NULL, SE_TCB_NAME, &luidTcb))
            {
                TOKEN_PRIVILEGES tp = {};
                tp.PrivilegeCount = 1;
                tp.Privileges[0].Luid = luidTcb;
                tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;

                TOKEN_PRIVILEGES tpPrev = {};
                DWORD dwPrevLen = sizeof(tpPrev);
                SetLastError(ERROR_SUCCESS);
                BOOL bAdjustOk = AdjustTokenPrivileges(hToken, FALSE, &tp, sizeof(tp), &tpPrev, &dwPrevLen);
                DWORD dwAdjustErr = GetLastError();

                const wchar_t* pszErrNote = L"";
                if (dwAdjustErr == ERROR_NOT_ALL_ASSIGNED)
                {
                    pszErrNote = L" (1300 = ERROR_NOT_ALL_ASSIGNED: SeTcbPrivilege is not held/assigned in process token)";
                }
                else if (dwAdjustErr == ERROR_SUCCESS)
                {
                    pszErrNote = L" (0 = ERROR_SUCCESS: SeTcbPrivilege enabled)";
                }

                wchar_t szAdjustMsg[256] = {};
                swprintf_s(szAdjustMsg, ARRAYSIZE(szAdjustMsg),
                    L"[PeekDiag] AdjustTokenPrivileges(SeTcbPrivilege): bAdjustOk=%d, GetLastError=%u%s",
                    bAdjustOk ? 1 : 0, dwAdjustErr, pszErrNote);
                Logger::LogInfo(szAdjustMsg);

                bool bNeedsRevert = (bAdjustOk && dwAdjustErr == ERROR_SUCCESS && tpPrev.PrivilegeCount > 0);
                PrivilegeAdjustmentGuard guard(hToken, tpPrev, bNeedsRevert);

                // Determine target session to test WTSQueryUserToken
                DWORD targetSession = dwSessionId;
                if (targetSession == 0xFFFFFFFF)
                {
                    if (!ProcessIdToSessionId(GetCurrentProcessId(), &targetSession))
                    {
                        targetSession = WTSGetActiveConsoleSessionId();
                    }
                }

                if (targetSession != 0xFFFFFFFF)
                {
                    HANDLE hTestToken = NULL;
                    SetLastError(ERROR_SUCCESS);
                    BOOL bWtsOk = WTSQueryUserToken(targetSession, &hTestToken);
                    DWORD dwWtsErr = GetLastError();

                    wchar_t szWtsMsg[256] = {};
                    swprintf_s(szWtsMsg, ARRAYSIZE(szWtsMsg),
                        L"[PeekDiag] Diagnostic WTSQueryUserToken(session=%u) result=%d, GetLastError=%u",
                        targetSession, bWtsOk ? 1 : 0, dwWtsErr);
                    Logger::LogInfo(szWtsMsg);

                    if (bWtsOk && hTestToken)
                    {
                        CloseHandle(hTestToken);
                    }
                }
                else
                {
                    Logger::LogInfo(L"[PeekDiag] Diagnostic WTSQueryUserToken skipped: target session is 0xFFFFFFFF");
                }

                // Immediately restore the original privilege state
                guard.Revert();
            }
            else
            {
                DWORD dwErr = GetLastError();
                wchar_t szErr[128] = {};
                swprintf_s(szErr, ARRAYSIZE(szErr),
                    L"[PeekDiag] LookupPrivilegeValueW(SeTcbPrivilege) failed, error=%u", dwErr);
                Logger::LogError(szErr);
            }

            CloseHandle(hToken);

            Logger::LogInfo(L"=== [PeekDiag] Process Token Diagnostics END ===");
        }
        catch (...)
        {
            Logger::LogError(L"[PeekDiag] Exception caught in RunProcessTokenDiagnostics");
        }
    }
}

#define PEEK_PIPE_NAME L"\\\\.\\pipe\\PeekEngine"

// Tile UI Field Identifiers
enum PEEK_FIELD_ID
{
    PFI_LOGO = 0,             // CPFT_TILE_IMAGE: Peek Biometric Logo (guidFieldType = CPFG_CREDENTIAL_PROVIDER_LOGO)
    PFI_LABEL = 1,            // CPFT_SMALL_TEXT: Credential Provider label/title ("Peek - Face Unlock")
    PFI_DISPLAY_NAME = 2,     // CPFT_LARGE_TEXT: User display name / Peek greeting
    PFI_STATUS_TEXT = 3,      // CPFT_SMALL_TEXT: Dynamic IPC state updates
    PFI_COUNT = 4
};

// Global DLL Module Handle and Reference Counting
extern HINSTANCE g_hinst;
void DllAddRef();
void DllRelease();

// Helper to allocate CoTaskMem strings for COM callers
inline HRESULT CoTaskMemAllocString(PCWSTR pszSource, PWSTR* ppszDest)
{
    if (!ppszDest) return E_POINTER;
    *ppszDest = nullptr;

    if (!pszSource)
    {
        *ppszDest = nullptr;
        return S_OK;
    }

    size_t cch = 0;
    HRESULT hr = StringCchLengthW(pszSource, STRSAFE_MAX_CCH, &cch);
    if (FAILED(hr)) return hr;

    size_t cb = (cch + 1) * sizeof(WCHAR);
    *ppszDest = static_cast<PWSTR>(CoTaskMemAlloc(cb));
    if (!*ppszDest) return E_OUTOFMEMORY;

    return StringCchCopyW(*ppszDest, cch + 1, pszSource);
}
