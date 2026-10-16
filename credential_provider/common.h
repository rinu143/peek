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
