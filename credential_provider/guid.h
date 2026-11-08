// Peek Face Engine - Credential Provider GUID Definitions
// Phase 5 Native Credential Provider
#pragma once

#include <initguid.h>
#include <windows.h>

// {4F64A0FC-9F09-4C0B-A5E7-537C44919903}
DEFINE_GUID(CLSID_PeekCredentialProvider,
    0x4f64a0fc, 0x9f09, 0x4c0b, 0xa5, 0xe7, 0x53, 0x7c, 0x44, 0x91, 0x99, 0x03);

#define PEEK_CP_GUID_STR L"{4F64A0FC-9F09-4C0B-A5E7-537C44919903}"
#define PEEK_CP_NAME_STR L"PeekCredentialProvider"
#define PEEK_CP_FRIENDLY_NAME L"Peek Facial Biometrics"
