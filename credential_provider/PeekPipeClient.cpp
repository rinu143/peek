// Peek Face Engine - Native C++ Named Pipe Client Implementation
#include "PeekPipeClient.h"
#include "JsonParser.h"
#include <sstream>
#include <vector>
#include <sddl.h>
#include <wincrypt.h>

static std::wstring GetConfiguredPipeName(const std::wstring& defaultName = PEEK_PIPE_NAME)
{
#ifdef PEEK_TEST_BUILD
    wchar_t szEnv[256] = {};
    DWORD dwLen = GetEnvironmentVariableW(L"PEEK_PIPE_NAME_OVERRIDE", szEnv, ARRAYSIZE(szEnv));
    if (dwLen > 0 && dwLen < ARRAYSIZE(szEnv))
    {
        return szEnv;
    }
#endif
    return defaultName;
}

PeekPipeClient::PeekPipeClient(const std::wstring& pipeName)
    : m_pipeName(GetConfiguredPipeName(pipeName))
    , m_expectedSid(L"")
    , m_expectedNonce("")
    , m_isServerVerified(false)
    , m_hPipe(INVALID_HANDLE_VALUE)
    , m_hThread(nullptr)
    , m_hCancelEvent(nullptr)
    , m_isRunning(false)
    , m_timeoutSeconds(12.0f)
{
    m_hCancelEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
}

PeekPipeClient::~PeekPipeClient()
{
    Cancel();
    if (m_hCancelEvent)
    {
        CloseHandle(m_hCancelEvent);
        m_hCancelEvent = nullptr;
    }
}

void PeekPipeClient::SetExpectedSid(const std::wstring& expectedSid)
{
    m_expectedSid = expectedSid;
}

bool PeekPipeClient::IsRunning() const
{
    return m_isRunning.load();
}

bool PeekPipeClient::IsPipeReachable(const std::wstring& pipeName)
{
    std::wstring effectivePipe = GetConfiguredPipeName(pipeName);
    if (WaitNamedPipeW(effectivePipe.c_str(), 0))
    {
        return true;
    }
    DWORD err = GetLastError();
    if (err == ERROR_SEM_TIMEOUT || err == ERROR_PIPE_BUSY || err == ERROR_ACCESS_DENIED)
    {
        return true;
    }
    return false;
}

std::string PeekPipeClient::GenerateChallengeNonce()
{
    BYTE bytes[16] = {};
    HCRYPTPROV hProv = 0;
    if (CryptAcquireContextW(&hProv, nullptr, nullptr, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT | CRYPT_SILENT))
    {
        CryptGenRandom(hProv, sizeof(bytes), bytes);
        CryptReleaseContext(hProv, 0);
    }
    else
    {
        LARGE_INTEGER qpc;
        QueryPerformanceCounter(&qpc);
        DWORD tick = GetTickCount();
        memcpy(bytes, &qpc.QuadPart, 8);
        memcpy(bytes + 8, &tick, 4);
    }

    char hex[33] = {};
    for (int i = 0; i < 16; i++)
    {
        sprintf_s(hex + (i * 2), 3, "%02x", bytes[i]);
    }
    return std::string(hex);
}

bool PeekPipeClient::VerifyServerProcess(HANDLE hPipe, const std::wstring& expectedSid)
{
    if (hPipe == INVALID_HANDLE_VALUE)
    {
        Logger::LogError("VerifyServerProcess: Invalid pipe handle");
        return false;
    }

    ULONG serverPid = 0;
    if (!GetNamedPipeServerProcessId(hPipe, &serverPid) || serverPid == 0)
    {
        Logger::LogError("VerifyServerProcess: GetNamedPipeServerProcessId failed");
        return false;
    }

    HANDLE hProc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, serverPid);
    if (!hProc)
    {
        Logger::LogError(("VerifyServerProcess: OpenProcess failed for server PID " + std::to_string(serverPid)).c_str());
        return false;
    }

    // 1. Verify image path is genuine %ProgramFiles%\Peek\PeekEngine.exe
    wchar_t szImagePath[MAX_PATH * 2] = {};
    DWORD dwSize = ARRAYSIZE(szImagePath);
    if (!QueryFullProcessImageNameW(hProc, 0, szImagePath, &dwSize))
    {
        CloseHandle(hProc);
        Logger::LogError("VerifyServerProcess: QueryFullProcessImageNameW failed");
        return false;
    }

    wchar_t szExpectedImage[MAX_PATH * 2] = {};
    ExpandEnvironmentStringsW(L"%ProgramFiles%\\Peek\\PeekEngine.exe", szExpectedImage, ARRAYSIZE(szExpectedImage));

    // Normalize both paths using GetLongPathNameW to avoid short-path / 8.3 mismatch (e.g. PROGRA~1)
    wchar_t szActualLong[MAX_PATH * 2] = {};
    if (GetLongPathNameW(szImagePath, szActualLong, ARRAYSIZE(szActualLong)) == 0)
    {
        wcscpy_s(szActualLong, szImagePath);
    }

    wchar_t szExpectedLong[MAX_PATH * 2] = {};
    if (GetLongPathNameW(szExpectedImage, szExpectedLong, ARRAYSIZE(szExpectedLong)) == 0)
    {
        wcscpy_s(szExpectedLong, szExpectedImage);
    }

    bool bImageMatch = (_wcsicmp(szActualLong, szExpectedLong) == 0);

#ifdef PEEK_TEST_BUILD
    wchar_t szOverride[MAX_PATH * 2] = {};
    DWORD dwLen = GetEnvironmentVariableW(L"PEEK_TEST_EXPECTED_IMAGE", szOverride, ARRAYSIZE(szOverride));
    if (dwLen > 0 && dwLen < ARRAYSIZE(szOverride))
    {
        if (_wcsicmp(szOverride, L"*") == 0)
        {
            bImageMatch = true;
        }
        else
        {
            bImageMatch = (_wcsicmp(szActualLong, szOverride) == 0);
        }
    }
#endif

    if (!bImageMatch)
    {
        Logger::LogError(L"VerifyServerProcess: Server image mismatch. Actual='" + std::wstring(szImagePath) + L"', Expected='" + szExpectedImage + L"'");
        CloseHandle(hProc);
        return false;
    }

    // 2. Verify token user SID matches expected SID or genuine SYSTEM (S-1-5-18)
    HANDLE hToken = nullptr;
    if (!OpenProcessToken(hProc, TOKEN_QUERY, &hToken))
    {
        CloseHandle(hProc);
        Logger::LogError("VerifyServerProcess: OpenProcessToken failed");
        return false;
    }

    DWORD dwTokenLen = 0;
    GetTokenInformation(hToken, TokenUser, nullptr, 0, &dwTokenLen);
    std::vector<BYTE> tokenBuf(dwTokenLen);
    if (!GetTokenInformation(hToken, TokenUser, tokenBuf.data(), dwTokenLen, &dwTokenLen))
    {
        CloseHandle(hToken);
        CloseHandle(hProc);
        Logger::LogError("VerifyServerProcess: GetTokenInformation failed");
        return false;
    }

    TOKEN_USER* pTokenUser = reinterpret_cast<TOKEN_USER*>(tokenBuf.data());
    LPWSTR pszSid = nullptr;
    if (!ConvertSidToStringSidW(pTokenUser->User.Sid, &pszSid))
    {
        CloseHandle(hToken);
        CloseHandle(hProc);
        Logger::LogError("VerifyServerProcess: ConvertSidToStringSidW failed");
        return false;
    }

    std::wstring serverSid = pszSid;
    LocalFree(pszSid);
    CloseHandle(hToken);
    CloseHandle(hProc);

    bool bSidMatch = false;
    if (!expectedSid.empty())
    {
        if (_wcsicmp(serverSid.c_str(), expectedSid.c_str()) == 0 ||
            _wcsicmp(serverSid.c_str(), L"S-1-5-18") == 0)
        {
            bSidMatch = true;
        }
    }
    else
    {
        // Cold boot logon without specific user SID: must be SYSTEM
        bSidMatch = (_wcsicmp(serverSid.c_str(), L"S-1-5-18") == 0);
    }

#ifdef PEEK_TEST_BUILD
    wchar_t szSidOverride[256] = {};
    DWORD dwSidLen = GetEnvironmentVariableW(L"PEEK_TEST_ALLOW_CURRENT_USER_SID", szSidOverride, ARRAYSIZE(szSidOverride));
    if (dwSidLen > 0 && _wcsicmp(szSidOverride, L"1") == 0)
    {
        bSidMatch = true;
    }
#endif

    if (!bSidMatch)
    {
        Logger::LogError(L"VerifyServerProcess: Token user SID mismatch. Server SID='" + serverSid + L"', Expected SID='" + expectedSid + L"'");
        return false;
    }

    Logger::LogInfo(L"VerifyServerProcess: Server verified successfully (PID=" + std::to_wstring(serverPid) + L", SID=" + serverSid + L")");
    return true;
}

bool PeekPipeClient::StartAuthAsync(PeekStateCallback callback, float timeoutSeconds)
{
    return StartAuthAsync(callback, m_expectedSid, timeoutSeconds);
}

bool PeekPipeClient::StartAuthAsync(PeekStateCallback callback, const std::wstring& expectedSid, float timeoutSeconds)
{
    Logger::LogInfo("PeekPipeClient::StartAuthAsync called");

    if (m_isRunning.load())
    {
        Logger::LogError("StartAuthAsync called while already running");
        return false;
    }

    m_callback = callback;
    m_expectedSid = expectedSid;
    m_timeoutSeconds = timeoutSeconds;
    m_isServerVerified = false;
    ResetEvent(m_hCancelEvent);
    m_isRunning = true;

    Logger::LogInfo("Creating worker thread for authentication");

    m_hThread = CreateThread(
        nullptr,
        0,
        WorkerThreadProc,
        this,
        0,
        nullptr
    );

    if (!m_hThread)
    {
        Logger::LogError("Failed to create worker thread for authentication");
        m_isRunning = false;
        return false;
    }

    Logger::LogInfo("Authentication thread created successfully");
    return true;
}

void PeekPipeClient::Cancel()
{
    if (m_hCancelEvent)
    {
        SetEvent(m_hCancelEvent);
    }

    if (m_hPipe != INVALID_HANDLE_VALUE)
    {
        // Attempt to send CANCEL_AUTH before teardown
        std::string cancelMsg = "{\"protocol_version\":\"2.0\",\"type\":\"CANCEL_AUTH\",\"reason\":\"USER_CANCELLED\"}\n";
        DWORD written = 0;
        WriteFile(m_hPipe, cancelMsg.c_str(), (DWORD)cancelMsg.size(), &written, nullptr);

        CloseHandle(m_hPipe);
        m_hPipe = INVALID_HANDLE_VALUE;
    }

    if (m_hThread)
    {
        WaitForSingleObject(m_hThread, 1500);
        CloseHandle(m_hThread);
        m_hThread = nullptr;
    }

    m_isRunning = false;
}

DWORD WINAPI PeekPipeClient::WorkerThreadProc(LPVOID lpParam)
{
    PeekPipeClient* pThis = static_cast<PeekPipeClient*>(lpParam);
    if (pThis)
    {
        pThis->RunWorker();
    }
    return 0;
}

bool PeekPipeClient::ConnectToPipe(DWORD timeoutMs)
{
    DWORD startTime = GetTickCount();

    while (GetTickCount() - startTime < timeoutMs)
    {
        if (WaitForSingleObject(m_hCancelEvent, 0) == WAIT_OBJECT_0)
        {
            return false;
        }

        m_hPipe = CreateFileW(
            m_pipeName.c_str(),
            GENERIC_READ | GENERIC_WRITE,
            0,
            nullptr,
            OPEN_EXISTING,
            SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
            nullptr
        );

        if (m_hPipe != INVALID_HANDLE_VALUE)
        {
            // CRITICAL SECURITY REQUIREMENT:
            // Verify server process image path and token SID
            if (!VerifyServerProcess(m_hPipe, m_expectedSid))
            {
                Logger::LogError("ConnectToPipe: Server process verification failed; closing connection");
                CloseHandle(m_hPipe);
                m_hPipe = INVALID_HANDLE_VALUE;
                m_isServerVerified = false;
                return false;
            }

            m_isServerVerified = true;
            return true;
        }

        DWORD err = GetLastError();
        if (err == ERROR_PIPE_BUSY)
        {
            WaitNamedPipeW(m_pipeName.c_str(), 500);
        }
        else
        {
            Logger::LogError(("ConnectToPipe: CreateFile failed with error " + std::to_string(err)).c_str());
            return false;
        }
    }

    return false;
}

bool PeekPipeClient::SendMessageString(const std::string& jsonLine)
{
    if (m_hPipe == INVALID_HANDLE_VALUE) return false;
    DWORD bytesWritten = 0;
    BOOL bSuccess = WriteFile(
        m_hPipe,
        jsonLine.c_str(),
        (DWORD)jsonLine.size(),
        &bytesWritten,
        nullptr
    );
    return (bSuccess && bytesWritten == jsonLine.size());
}

bool PeekPipeClient::ReadNextMessage(std::string& outLine)
{
    while (true)
    {
        if (WaitForSingleObject(m_hCancelEvent, 0) == WAIT_OBJECT_0)
        {
            return false;
        }

        size_t newlinePos = m_readBuffer.find('\n');
        if (newlinePos != std::string::npos)
        {
            outLine = m_readBuffer.substr(0, newlinePos);
            m_readBuffer.erase(0, newlinePos + 1);
            return true;
        }

        DWORD bytesAvail = 0;
        if (!PeekNamedPipe(m_hPipe, nullptr, 0, nullptr, &bytesAvail, nullptr))
        {
            return false;
        }

        if (bytesAvail > 0)
        {
            std::vector<char> buffer(bytesAvail);
            DWORD bytesRead = 0;
            if (ReadFile(m_hPipe, buffer.data(), bytesAvail, &bytesRead, nullptr) && bytesRead > 0)
            {
                m_readBuffer.append(buffer.data(), bytesRead);
                continue;
            }
            else
            {
                return false;
            }
        }

        Sleep(20);
    }
}

void PeekPipeClient::ParseAndDispatchMessage(const std::string& line, PeekAuthResult& result)
{
    PeekJson::PeekProtocolMessage msg;
    if (!PeekJson::ProtocolSchemaValidator::ValidateAndParse(line, msg))
    {
        Logger::LogError(L"ParseAndDispatchMessage: Protocol schema validation failed: " + msg.parseError);
        result.state = PeekIPCState::ERROR_STATE;
        result.reasonCode = L"PROTOCOL_VIOLATION";
        result.detail = L"Security violation or invalid message schema";
        if (m_callback) m_callback(result);
        return;
    }

    result.detail = msg.detail;
    result.displayName = msg.displayName;
    result.reasonCode = msg.reasonCode;
    result.promptText = msg.promptText;
    result.isAuthorizedToUnlock = msg.isAuthorizedToUnlock;

    if (msg.type == L"ENGINE_READY")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received ENGINE_READY");
        result.state = PeekIPCState::ENGINE_READY;
    }
    else if (msg.type == L"CAMERA_STARTING")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received CAMERA_STARTING");
        result.state = PeekIPCState::CAMERA_STARTING;
    }
    else if (msg.type == L"SEARCHING")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received SEARCHING");
        result.state = PeekIPCState::SEARCHING;
    }
    else if (msg.type == L"FACE_FOUND")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received FACE_FOUND");
        result.state = PeekIPCState::FACE_FOUND;
    }
    else if (msg.type == L"VERIFYING")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received VERIFYING");
        result.state = PeekIPCState::VERIFYING;
    }
    else if (msg.type == L"LIVENESS_CHECK")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received LIVENESS_CHECK");
        result.state = PeekIPCState::LIVENESS_CHECK;
    }
    else if (msg.type == L"AUTHENTICATED")
    {
        // CRITICAL SECURITY REQUIREMENT:
        // Never act on AUTHENTICATED from an unverified server
        if (!m_isServerVerified)
        {
            Logger::LogError("ParseAndDispatchMessage: Refusing AUTHENTICATED from unverified server");
            result.state = PeekIPCState::ERROR_STATE;
            result.reasonCode = L"UNVERIFIED_SERVER";
            result.isAuthorizedToUnlock = false;
            if (m_callback) m_callback(result);
            return;
        }

        // CRITICAL SECURITY REQUIREMENT:
        // Reject missing or mismatched challenge nonce
        std::wstring expectedNonceWide = PeekJson::Utf8ToWide(m_expectedNonce);
        if (msg.nonce.empty() || _wcsicmp(msg.nonce.c_str(), expectedNonceWide.c_str()) != 0)
        {
            Logger::LogError(L"ParseAndDispatchMessage: Challenge nonce mismatch! Received='" + msg.nonce + L"', Expected='" + expectedNonceWide + L"'");
            result.state = PeekIPCState::ERROR_STATE;
            result.reasonCode = L"NONCE_MISMATCH";
            result.isAuthorizedToUnlock = false;
            if (m_callback) m_callback(result);
            return;
        }

        Logger::LogInfo(L"ParseAndDispatchMessage: Received verified AUTHENTICATED with valid challenge nonce");
        result.state = PeekIPCState::AUTHENTICATED;
    }
    else if (msg.type == L"AUTH_FAILED")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received AUTH_FAILED");
        result.state = PeekIPCState::AUTH_FAILED;
    }
    else if (msg.type == L"TIMEOUT")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received TIMEOUT");
        result.state = PeekIPCState::TIMEOUT;
    }
    else if (msg.type == L"ERROR")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received ERROR");
        result.state = PeekIPCState::ERROR_STATE;
    }
    else if (msg.type == L"PONG")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received PONG");
        return;
    }
    else
    {
        Logger::LogError(L"ParseAndDispatchMessage: Unknown message type " + msg.type);
        result.state = PeekIPCState::ERROR_STATE;
        result.reasonCode = L"INVALID_MESSAGE";
    }

    if (m_callback)
    {
        m_callback(result);
    }
}

void PeekPipeClient::RunWorker()
{
    Logger::LogInfo("PeekPipeClient::RunWorker started");

    PeekAuthResult result;

    // 1. Initial State: Connecting
    result.state = PeekIPCState::CONNECTING;
    result.detail = L"Connecting to Peek Face Engine...";
    if (m_callback) m_callback(result);

    // 2. Connect to Named Pipe & Verify Server
    if (!ConnectToPipe(2500))
    {
        // Fail-open: Engine service is offline or unverified
        result.state = PeekIPCState::DISCONNECTED;
        result.detail = L"Peek engine offline. Sign in with password or PIN.";
        result.reasonCode = L"SERVICE_UNAVAILABLE";
        if (m_callback) m_callback(result);
        m_isRunning = false;
        Logger::LogError("Failed to connect or verify Peek engine pipe");
        return;
    }

    Logger::LogInfo("Successfully connected to and verified Peek engine pipe");

    // 3. Generate random 128-bit challenge nonce and send START_AUTH
    m_expectedNonce = GenerateChallengeNonce();

    std::ostringstream ss;
    ss << "{\"protocol_version\":\"2.0\",\"type\":\"START_AUTH\",\"nonce\":\""
       << m_expectedNonce << "\",\"timeout_seconds\":"
       << m_timeoutSeconds << "}\n";
    if (!SendMessageString(ss.str()))
    {
        result.state = PeekIPCState::ERROR_STATE;
        result.detail = L"Failed to send authentication request.";
        if (m_callback) m_callback(result);
        m_isRunning = false;
        Logger::LogError("Failed to send START_AUTH message");
        return;
    }

    Logger::LogInfo("START_AUTH message sent successfully with challenge nonce");

    // 4. Stream responses until completion or cancel
    std::string line;
    while (m_isRunning.load() && WaitForSingleObject(m_hCancelEvent, 0) != WAIT_OBJECT_0)
    {
        if (!ReadNextMessage(line))
        {
            Logger::LogInfo("Failed to read next message from pipe");
            break;
        }

        ParseAndDispatchMessage(line, result);

        // Terminal state reached
        if (result.state == PeekIPCState::AUTHENTICATED ||
            result.state == PeekIPCState::AUTH_FAILED ||
            result.state == PeekIPCState::TIMEOUT ||
            result.state == PeekIPCState::ERROR_STATE)
        {
            Logger::LogInfo("Terminal authentication state reached, exiting loop");
            break;
        }
    }

    if (m_hPipe != INVALID_HANDLE_VALUE)
    {
        CloseHandle(m_hPipe);
        m_hPipe = INVALID_HANDLE_VALUE;
    }

    m_isRunning = false;
    Logger::LogInfo("PeekPipeClient::RunWorker completed");
}
