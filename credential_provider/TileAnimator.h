// Peek Face Engine - Phase 7 Tile Animator Header
// Native, state-driven 32-bit ARGB tile animation for Windows Credential Provider
#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <gdiplus.h>
#include <atomic>
#include <functional>
#include <vector>
#include <map>

// UI Presentation States matching the reference state machine
enum class PeekUiState
{
    IDLE = 0,
    SEARCHING,
    FACE_FOUND,
    VERIFYING,
    LIVENESS,
    SUCCESS,
    FAILURE,
    RETRY
};

class TileAnimator
{
public:
    // Callback invoked when a frame updates, receiving the new 32bpp HBITMAP.
    // TileAnimator is structurally incapable of calling authentication or logon methods.
    using FrameChangedCallback = std::function<void(HBITMAP hbmp)>;

    TileAnimator();
    ~TileAnimator();

    // Initializes GDI+ and pre-renders embedded PNG frames to 32bpp DIB sections.
    // Returns true on success; on failure, fails soft without throwing.
    bool Initialize(HINSTANCE hInstance, FrameChangedCallback callback);

    // Shuts down animation thread and cleans up GDI+ resources.
    void Shutdown();

    // Sets the presentation state. Must only be driven by real pipe state updates.
    // State transitions cancel any in-flight frame timer instantly.
    void SetState(PeekUiState newState);

    // Returns the current presentation state.
    PeekUiState GetState() const;

    // Returns the current frame HBITMAP (32bpp DIB section preserving alpha), or nullptr if none.
    HBITMAP GetCurrentFrame() const;

    // Returns true if frames are loaded and ready.
    bool HasFrames() const { return m_hasFrames; }

private:
    // Background animation worker loop for multi-frame state cycling (~200ms per frame)
    static DWORD WINAPI AnimationThreadProc(LPVOID lpParam);
    void AnimationLoop();

    // Loads a PNG resource into a 32bpp ARGB DIB section preserving transparency
    HBITMAP LoadPngResourceToDib(HINSTANCE hInstance, int resourceId);

    // Frees all cached DIB section bitmaps
    void CleanupBitmaps();

    HINSTANCE m_hInstance;
    FrameChangedCallback m_callback;
    ULONG_PTR m_gdiplusToken;
    bool m_gdiplusInitialized;
    bool m_hasFrames;

    mutable CRITICAL_SECTION m_cs;
    PeekUiState m_currentState;
    size_t m_currentFrameIndex;

    // Map each state to its list of pre-rendered 32bpp DIB section frames
    std::map<PeekUiState, std::vector<HBITMAP>> m_stateFrames;

    // Threading & timer sync
    HANDLE m_hThread;
    HANDLE m_hWakeEvent;
    HANDLE m_hStopEvent;
    std::atomic<bool> m_isRunning;
};
