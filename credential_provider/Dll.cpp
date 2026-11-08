// Peek Face Engine - COM In-Process DLL Scaffolding & Registration
#include "common.h"
#include "PeekProvider.h"
#include "guid.h"
#include <unknwn.h>

HINSTANCE g_hinst = nullptr;
static long g_cRef = 0;

void DllAddRef()
{
    InterlockedIncrement(&g_cRef);
}

void DllRelease()
{
    InterlockedDecrement(&g_cRef);
}

// --- COM Class Factory ---

class PeekClassFactory : public IClassFactory
{
public:
    PeekClassFactory() : m_cRef(1) { DllAddRef(); }
    virtual ~PeekClassFactory() { DllRelease(); }

    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv)
    {
        if (!ppv) return E_POINTER;
        *ppv = nullptr;

        if (riid == IID_IUnknown || riid == IID_IClassFactory)
        {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() { return InterlockedIncrement(&m_cRef); }
    IFACEMETHODIMP_(ULONG) Release()
    {
        LONG cRef = InterlockedDecrement(&m_cRef);
        if (cRef == 0) delete this;
        return cRef;
    }

    IFACEMETHODIMP CreateInstance(IUnknown* pUnkOuter, REFIID riid, void** ppv)
    {
        if (!ppv) return E_POINTER;
        *ppv = nullptr;

        std::wstring iidStr = GuidToString(riid);

        if (pUnkOuter)
        {
            Logger::LogError(L"PeekClassFactory::CreateInstance: aggregation not supported -> CLASS_E_NOAGGREGATION");
            return CLASS_E_NOAGGREGATION;
        }

        PeekProvider* pProvider = new (std::nothrow) PeekProvider();
        if (!pProvider)
        {
            Logger::LogError(L"PeekClassFactory::CreateInstance: failed to allocate PeekProvider -> E_OUTOFMEMORY");
            return E_OUTOFMEMORY;
        }

        HRESULT hr = pProvider->QueryInterface(riid, ppv);
        pProvider->Release();

        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf), L"PeekClassFactory::CreateInstance: IID=%s -> hr=0x%08X", iidStr.c_str(), hr);
        Logger::LogInfo(szBuf);
        return hr;
    }

    IFACEMETHODIMP LockServer(BOOL fLock)
    {
        if (fLock) DllAddRef();
        else DllRelease();
        return S_OK;
    }

private:
    long m_cRef;
};

// --- DLL Exports ---

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID /*lpvReserved*/)
{
    if (fdwReason == DLL_PROCESS_ATTACH)
    {
        g_hinst = hinstDLL;
        DisableThreadLibraryCalls(hinstDLL);
    }
    return TRUE;
}

STDAPI DllCanUnloadNow()
{
    return (g_cRef == 0) ? S_OK : S_FALSE;
}

STDAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, void** ppv)
{
    if (!ppv) return E_POINTER;
    *ppv = nullptr;

    Logger::LogInfo("PeekCredentialProvider: module loaded (DllGetClassObject)");

    std::wstring clsidStr = GuidToString(rclsid);
    std::wstring iidStr = GuidToString(riid);

    if (!IsEqualCLSID(rclsid, CLSID_PeekCredentialProvider))
    {
        wchar_t szBuf[256] = {};
        swprintf_s(szBuf, ARRAYSIZE(szBuf),
            L"DllGetClassObject: CLSID mismatch (CLSID=%s, IID=%s) -> CLASS_E_CLASSNOTAVAILABLE",
            clsidStr.c_str(), iidStr.c_str());
        Logger::LogInfo(szBuf);
        return CLASS_E_CLASSNOTAVAILABLE;
    }

    wchar_t szBuf[256] = {};
    swprintf_s(szBuf, ARRAYSIZE(szBuf),
        L"DllGetClassObject: CLSID matched (CLSID=%s, IID=%s)",
        clsidStr.c_str(), iidStr.c_str());
    Logger::LogInfo(szBuf);

    PeekClassFactory* pFactory = new (std::nothrow) PeekClassFactory();
    if (!pFactory)
    {
        Logger::LogError(L"DllGetClassObject: out of memory allocating PeekClassFactory -> E_OUTOFMEMORY");
        return E_OUTOFMEMORY;
    }

    HRESULT hr = pFactory->QueryInterface(riid, ppv);
    pFactory->Release();

    swprintf_s(szBuf, ARRAYSIZE(szBuf), L"DllGetClassObject: hr=0x%08X", hr);
    Logger::LogInfo(szBuf);
    return hr;
}

static HRESULT SetRegistryValue(HKEY hRoot, PCWSTR pszSubKey, PCWSTR pszValueName, PCWSTR pszData)
{
    HKEY hKey = nullptr;
    LSTATUS status = RegCreateKeyExW(
        hRoot, pszSubKey, 0, nullptr,
        REG_OPTION_NON_VOLATILE, KEY_SET_VALUE, nullptr,
        &hKey, nullptr
    );
    if (status != ERROR_SUCCESS) return HRESULT_FROM_WIN32(status);

    size_t cbData = (wcslen(pszData) + 1) * sizeof(WCHAR);
    status = RegSetValueExW(
        hKey, pszValueName, 0, REG_SZ,
        reinterpret_cast<const BYTE*>(pszData),
        static_cast<DWORD>(cbData)
    );

    RegCloseKey(hKey);
    return HRESULT_FROM_WIN32(status);
}

STDAPI DllRegisterServer()
{
    WCHAR szModulePath[MAX_PATH] = { 0 };
    if (!GetModuleFileNameW(g_hinst, szModulePath, ARRAYSIZE(szModulePath)))
    {
        HRESULT hr = HRESULT_FROM_WIN32(GetLastError());
        Logger::LogError("DllRegisterServer: GetModuleFileNameW failed");
        return hr;
    }

    std::wstring regLog = L"DllRegisterServer called for module: ";
    regLog += szModulePath;
    Logger::LogInfo(regLog);

    // 1. Register COM CLSID: HKCR\CLSID\{GUID}
    std::wstring clsidKey = L"CLSID\\" PEEK_CP_GUID_STR;
    HRESULT hr = SetRegistryValue(HKEY_CLASSES_ROOT, clsidKey.c_str(), nullptr, PEEK_CP_FRIENDLY_NAME);
    if (FAILED(hr))
    {
        Logger::LogError("DllRegisterServer: failed to register HKCR\\CLSID");
        return hr;
    }

    // InprocServer32
    std::wstring inprocKey = clsidKey + L"\\InprocServer32";
    hr = SetRegistryValue(HKEY_CLASSES_ROOT, inprocKey.c_str(), nullptr, szModulePath);
    if (FAILED(hr))
    {
        Logger::LogError("DllRegisterServer: failed to register InprocServer32 path");
        return hr;
    }

    hr = SetRegistryValue(HKEY_CLASSES_ROOT, inprocKey.c_str(), L"ThreadingModel", L"Apartment");
    if (FAILED(hr))
    {
        Logger::LogError("DllRegisterServer: failed to register ThreadingModel");
        return hr;
    }

    // 2. Register Windows Credential Provider:
    // HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{GUID}
    std::wstring cpKey = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\" PEEK_CP_GUID_STR;
    hr = SetRegistryValue(HKEY_LOCAL_MACHINE, cpKey.c_str(), nullptr, PEEK_CP_NAME_STR);
    if (FAILED(hr))
    {
        Logger::LogError("DllRegisterServer: failed to register Credential Provider in HKLM");
        return hr;
    }

    Logger::LogInfo("DllRegisterServer: successfully registered -> S_OK");
    return S_OK;
}

STDAPI DllUnregisterServer()
{
    Logger::LogInfo("DllUnregisterServer called");

    // 1. Unregister Credential Provider
    std::wstring cpKey = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\" PEEK_CP_GUID_STR;
    RegDeleteTreeW(HKEY_LOCAL_MACHINE, cpKey.c_str());

    // 2. Unregister COM CLSID
    std::wstring clsidKey = L"CLSID\\" PEEK_CP_GUID_STR;
    RegDeleteTreeW(HKEY_CLASSES_ROOT, clsidKey.c_str());

    Logger::LogInfo("DllUnregisterServer: successfully unregistered -> S_OK");
    return S_OK;
}
