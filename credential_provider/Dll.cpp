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

        if (pUnkOuter) return CLASS_E_NOAGGREGATION;

        PeekProvider* pProvider = new (std::nothrow) PeekProvider();
        if (!pProvider) return E_OUTOFMEMORY;

        HRESULT hr = pProvider->QueryInterface(riid, ppv);
        pProvider->Release();
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

    if (!IsEqualCLSID(rclsid, CLSID_PeekCredentialProvider))
    {
        return CLASS_E_CLASSNOTAVAILABLE;
    }

    PeekClassFactory* pFactory = new (std::nothrow) PeekClassFactory();
    if (!pFactory) return E_OUTOFMEMORY;

    HRESULT hr = pFactory->QueryInterface(riid, ppv);
    pFactory->Release();
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
        return HRESULT_FROM_WIN32(GetLastError());
    }

    // 1. Register COM CLSID: HKCR\CLSID\{GUID}
    std::wstring clsidKey = L"CLSID\\" PEEK_CP_GUID_STR;
    HRESULT hr = SetRegistryValue(HKEY_CLASSES_ROOT, clsidKey.c_str(), nullptr, PEEK_CP_FRIENDLY_NAME);
    if (FAILED(hr)) return hr;

    // InprocServer32
    std::wstring inprocKey = clsidKey + L"\\InprocServer32";
    hr = SetRegistryValue(HKEY_CLASSES_ROOT, inprocKey.c_str(), nullptr, szModulePath);
    if (FAILED(hr)) return hr;

    hr = SetRegistryValue(HKEY_CLASSES_ROOT, inprocKey.c_str(), L"ThreadingModel", L"Apartment");
    if (FAILED(hr)) return hr;

    // 2. Register Windows Credential Provider:
    // HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{GUID}
    std::wstring cpKey = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\" PEEK_CP_GUID_STR;
    hr = SetRegistryValue(HKEY_LOCAL_MACHINE, cpKey.c_str(), nullptr, PEEK_CP_NAME_STR);
    if (FAILED(hr)) return hr;

    return S_OK;
}

STDAPI DllUnregisterServer()
{
    // 1. Unregister Credential Provider
    std::wstring cpKey = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\" PEEK_CP_GUID_STR;
    RegDeleteTreeW(HKEY_LOCAL_MACHINE, cpKey.c_str());

    // 2. Unregister COM CLSID
    std::wstring clsidKey = L"CLSID\\" PEEK_CP_GUID_STR;
    RegDeleteTreeW(HKEY_CLASSES_ROOT, clsidKey.c_str());

    return S_OK;
}
