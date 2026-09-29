// Peek Face Engine - Native C++ Named Pipe Client
// Communicates with \\\\.\\pipe\\PeekEngine over the Phase 6 IPC protocol.
#pragma once

#include "common.h"
#include <functional>
#include <atomic>
#include <string>

// Reusable IPC state enumeration matching engine/ipc/protocol.py
enum class PeekIPCState
{
    DISCONNECTED = 0,
    CONNECTING,
    ENGINE_READY,
    CAMERA_STARTING,
    SEARCHING,
    FACE_FOUND,
    VERIFYING,
    LIVENESS_CHECK,
    AUTHENTICATED,
    AUTH_FAILED,
    TIMEOUT,
    ERROR_STATE
};

struct PeekAuthResult
{
    PeekIPCState state;
    bool isAuthorizedToUnlock;
    std::wstring displayName;
    std::wstring profileId;
    std::wstring detail;
    std::wstring reasonCode;
    std::wstring promptText;

    PeekAuthResult()
        : state(PeekIPCState::DISCONNECTED)
        , isAuthorizedToUnlock(false)
    {}
};

// Callback signature for async state updates from the engine
typedef std::function<void(const PeekAuthResult&)> PeekStateCallback;

class PeekPipeClient
{
public:
    PeekPipeClient(const std::wstring& pipeName = PEEK_PIPE_NAME);
    ~PeekPipeClient();

    // Starts background authentication thread
    bool StartAuthAsync(PeekStateCallback callback, float timeoutSeconds = 12.0f);

    // Signals cancellation and cleanly disconnects from pipe
    void Cancel();

    // Check if background worker is currently active
    bool IsRunning() const;

private:
    static DWORD WINAPI WorkerThreadProc(LPVOID lpParam);
    void RunWorker();

    bool ConnectToPipe(DWORD timeoutMs = 2000);
    bool SendMessageString(const std::string& jsonLine);
    bool ReadNextMessage(std::string& outLine);
    void ParseAndDispatchMessage(const std::string& line, PeekAuthResult& result);

    std::wstring m_pipeName;
    HANDLE m_hPipe;
    HANDLE m_hThread;
    HANDLE m_hCancelEvent;
    std::atomic<bool> m_isRunning;
    float m_timeoutSeconds;
    PeekStateCallback m_callback;
    std::string m_readBuffer;
};
