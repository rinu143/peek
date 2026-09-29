// Peek Face Engine - Native C++ Named Pipe Client Implementation
#include "PeekPipeClient.h"
#include <sstream>

// Helper to convert UTF-8 std::string to std::wstring
static std::wstring Utf8ToWide(const std::string& utf8Str)
{
    if (utf8Str.empty()) return L"";
    int cchNeeded = MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), (int)utf8Str.size(), nullptr, 0);
    if (cchNeeded <= 0) return L"";
    std::wstring wideStr(cchNeeded, 0);
    MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), (int)utf8Str.size(), &wideStr[0], cchNeeded);
    return wideStr;
}

// Lightweight JSON value extraction helpers for protocol parsing
static bool ExtractJsonString(const std::string& json, const std::string& key, std::wstring& outVal)
{
    std::string needle = "\"" + key + "\":";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return false;

    pos += needle.length();
    while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t')) pos++;

    if (pos >= json.size() || json[pos] != '\"') return false;
    pos++; // Skip opening quote

    std::string value;
    while (pos < json.size() && json[pos] != '\"')
    {
        if (json[pos] == '\\' && pos + 1 < json.size())
        {
            pos++;
            value += json[pos];
        }
        else
        {
            value += json[pos];
        }
        pos++;
    }

    outVal = Utf8ToWide(value);
    return true;
}

static bool ExtractJsonBool(const std::string& json, const std::string& key, bool& outVal)
{
    std::string needle = "\"" + key + "\":";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return false;

    pos += needle.length();
    while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t')) pos++;

    if (json.compare(pos, 4, "true") == 0)
    {
        outVal = true;
        return true;
    }
    if (json.compare(pos, 5, "false") == 0)
    {
        outVal = false;
        return true;
    }
    return false;
}

PeekPipeClient::PeekPipeClient(const std::wstring& pipeName)
    : m_pipeName(pipeName)
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

bool PeekPipeClient::IsRunning() const
{
    return m_isRunning.load();
}

bool PeekPipeClient::StartAuthAsync(PeekStateCallback callback, float timeoutSeconds)
{
    Logger::LogInfo("PeekPipeClient::StartAuthAsync called");
    
    if (m_isRunning.load())
    {
        Logger::LogError("StartAuthAsync called while already running");
        return false;
    }

    m_callback = callback;
    m_timeoutSeconds = timeoutSeconds;
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
        std::string cancelMsg = "{\"protocol_version\":\"1.0\",\"type\":\"CANCEL_AUTH\",\"reason\":\"USER_CANCELLED\"}\n";
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
            0,
            nullptr
        );

        if (m_hPipe != INVALID_HANDLE_VALUE)
        {
            return true;
        }

        DWORD err = GetLastError();
        if (err == ERROR_PIPE_BUSY)
        {
            WaitNamedPipeW(m_pipeName.c_str(), 500);
        }
        else
        {
            Sleep(80);
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

        // Check if full line is in buffer
        size_t newlinePos = m_readBuffer.find('\n');
        if (newlinePos != std::string::npos)
        {
            outLine = m_readBuffer.substr(0, newlinePos);
            m_readBuffer.erase(0, newlinePos + 1);
            return true;
        }

        // Peek available bytes
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
    std::wstring typeStr;
    if (!ExtractJsonString(line, "type", typeStr))
    {
        return;
    }

    ExtractJsonString(line, "detail", result.detail);
    ExtractJsonString(line, "display_name", result.displayName);
    ExtractJsonString(line, "profile_id", result.profileId);
    ExtractJsonString(line, "reason_code", result.reasonCode);
    ExtractJsonString(line, "prompt_text", result.promptText);
    ExtractJsonBool(line, "is_authorized_to_unlock", result.isAuthorizedToUnlock);

    if (typeStr == L"ENGINE_READY")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received ENGINE_READY message");
        result.state = PeekIPCState::ENGINE_READY;
    }
    else if (typeStr == L"CAMERA_STARTING")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received CAMERA_STARTING message");
        result.state = PeekIPCState::CAMERA_STARTING;
    }
    else if (typeStr == L"SEARCHING")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received SEARCHING message");
        result.state = PeekIPCState::SEARCHING;
    }
    else if (typeStr == L"FACE_FOUND")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received FACE_FOUND message");
        result.state = PeekIPCState::FACE_FOUND;
    }
    else if (typeStr == L"VERIFYING")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received VERIFYING message");
        result.state = PeekIPCState::VERIFYING;
    }
    else if (typeStr == L"LIVENESS_CHECK")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received LIVENESS_CHECK message");
        result.state = PeekIPCState::LIVENESS_CHECK;
    }
    else if (typeStr == L"AUTHENTICATED")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received AUTHENTICATED message");
        result.state = PeekIPCState::AUTHENTICATED;
    }
    else if (typeStr == L"AUTH_FAILED")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received AUTH_FAILED message");
        result.state = PeekIPCState::AUTH_FAILED;
    }
    else if (typeStr == L"TIMEOUT")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received TIMEOUT message");
        result.state = PeekIPCState::TIMEOUT;
    }
    else if (typeStr == L"ERROR")
    {
        Logger::LogInfo(L"ParseAndDispatchMessage: Received ERROR message");
        result.state = PeekIPCState::ERROR_STATE;
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

    // 2. Connect to Named Pipe
    if (!ConnectToPipe(2500))
    {
        // Fail-open: Engine service is offline or not installed
        result.state = PeekIPCState::DISCONNECTED;
        result.detail = L"Peek engine offline. Sign in with password or PIN.";
        result.reasonCode = L"SERVICE_UNAVAILABLE";
        if (m_callback) m_callback(result);
        m_isRunning = false;
        Logger::LogError("Failed to connect to Peek engine pipe");
        return;
    }

    Logger::LogInfo("Successfully connected to Peek engine pipe");

    // 3. Send START_AUTH
    std::ostringstream ss;
    ss << "{\"protocol_version\":\"1.0\",\"type\":\"START_AUTH\",\"timeout_seconds\":"
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

    Logger::LogInfo("START_AUTH message sent successfully");

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
