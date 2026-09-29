// Test file for SecretVault functionality
#include "SecretVault.h"
#include <iostream>
#include <windows.h>

int main()
{
    std::wcout << L"Testing SecretVault functionality...\n";
    
    // Test 1: Check if the functions are available
    std::wcout << L"Testing ReadWrappedSecret function...\n";
    BYTE buffer[1024];
    DWORD cbActual = 0;
    
    // Try to read a non-existent secret (should fail gracefully)
    // An invalid session must fail cleanly; this exercises the safe failure path
    // when WTSQueryUserToken/impersonation is unavailable.
    bool result = PeekSecretVault::ReadWrappedSecret(L"nonexistent_user", 0xFFFFFFFF, buffer, sizeof(buffer), &cbActual);
    std::wcout << L"ReadWrappedSecret for nonexistent user: " << (result ? L"SUCCESS" : L"FAILED") << L"\n";
    
    // Test 2: Check ValidateWindowsPassword function
    std::wcout << L"Testing ValidateWindowsPassword function...\n";
    bool validateResult = PeekSecretVault::ValidateWindowsPassword(L"nonexistent_user", L"", L"test_password");
    std::wcout << L"ValidateWindowsPassword for nonexistent user: " << (validateResult ? L"SUCCESS" : L"FAILED") << L"\n";
    
    std::wcout << L"Test completed.\n";
    return 0;
}
