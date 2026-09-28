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
#include <strsafe.h>
#include <string>
#include <vector>
#include <memory>

#include "guid.h"

#define PEEK_PIPE_NAME L"\\\\.\\pipe\\PeekEngine"

// Tile UI Field Identifiers
enum PEEK_FIELD_ID
{
    PFI_LOGO = 0,             // CPFT_TILE_IMAGE: Peek Biometric Logo
    PFI_DISPLAY_NAME = 1,     // CPFT_LARGE_TEXT: User display name / Peek greeting
    PFI_STATUS_TEXT = 2,      // CPFT_SMALL_TEXT: Dynamic IPC state updates
    PFI_COUNT = 3
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
