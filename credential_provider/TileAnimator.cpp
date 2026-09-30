// Peek Face Engine - Phase 7 Tile Animator Implementation
// Native, state-driven 32-bit ARGB tile animation for Windows Credential Provider

#include "TileAnimator.h"
#include "resource.h"
#include "common.h"

#pragma comment(lib, "gdiplus.lib")

TileAnimator::TileAnimator()
    : m_hInstance(nullptr)
    , m_callback(nullptr)
    , m_gdiplusToken(0)
    , m_gdiplusInitialized(false)
    , m_hasFrames(false)
    , m_currentState(PeekUiState::IDLE)
    , m_currentFrameIndex(0)
    , m_hThread(NULL)
    , m_hWakeEvent(NULL)
    , m_hStopEvent(NULL)
    , m_isRunning(false)
{
    InitializeCriticalSection(&m_cs);
}

TileAnimator::~TileAnimator()
{
    Shutdown();
    DeleteCriticalSection(&m_cs);
}

bool TileAnimator::Initialize(HINSTANCE hInstance, FrameChangedCallback callback)
{
    m_hInstance = hInstance;
    m_callback = callback;

    // Fail soft: start GDI+
    Gdiplus::GdiplusStartupInput gdiplusStartupInput;
    if (Gdiplus::GdiplusStartup(&m_gdiplusToken, &gdiplusStartupInput, NULL) != Gdiplus::Ok)
    {
        Logger::LogError("TileAnimator: Failed to initialize GDI+");
        return false;
    }
    m_gdiplusInitialized = true;

    // Helper lambda to load and append a frame
    auto loadFrame = [this, hInstance](PeekUiState state, int resId) {
        HBITMAP hbmp = LoadPngResourceToDib(hInstance, resId);
        if (hbmp)
        {
            m_stateFrames[state].push_back(hbmp);
            return true;
        }
        return false;
    };

    // 1. IDLE (1 frame)
    loadFrame(PeekUiState::IDLE, IDR_PNG_FRAME_IDLE);

    // 2. SEARCHING (3 pulse animation frames)
    loadFrame(PeekUiState::SEARCHING, IDR_PNG_FRAME_SEARCH_1);
    loadFrame(PeekUiState::SEARCHING, IDR_PNG_FRAME_SEARCH_2);
    loadFrame(PeekUiState::SEARCHING, IDR_PNG_FRAME_SEARCH_3);

    // 3. FACE FOUND (1 frame)
    loadFrame(PeekUiState::FACE_FOUND, IDR_PNG_FRAME_FACE_FOUND);

    // 4. VERIFYING (1 frame)
    loadFrame(PeekUiState::VERIFYING, IDR_PNG_FRAME_VERIFYING);

    // 5. LIVENESS (1 frame)
    loadFrame(PeekUiState::LIVENESS, IDR_PNG_FRAME_LIVENESS);

    // 6. SUCCESS (1 frame)
    loadFrame(PeekUiState::SUCCESS, IDR_PNG_FRAME_SUCCESS);

    // 7. FAILURE (1 frame)
    loadFrame(PeekUiState::FAILURE, IDR_PNG_FRAME_FAILURE);

    // 8. RETRY (1 frame)
    loadFrame(PeekUiState::RETRY, IDR_PNG_FRAME_RETRY);

    // Check if at least primary frames were loaded
    if (!m_stateFrames[PeekUiState::IDLE].empty() &&
        !m_stateFrames[PeekUiState::SEARCHING].empty())
    {
        m_hasFrames = true;
    }
    else
    {
        Logger::LogError("TileAnimator: Failed to load required PNG frames from resources");
        return false;
    }

    // Create synchronization events for the animation worker
    m_hWakeEvent = CreateEventW(NULL, FALSE, FALSE, NULL); // auto-reset
    m_hStopEvent = CreateEventW(NULL, TRUE, FALSE, NULL);  // manual-reset

    if (!m_hWakeEvent || !m_hStopEvent)
    {
        Logger::LogError("TileAnimator: Failed to create synchronization events");
        return false;
    }

    // Launch lightweight background animation thread for multi-frame state cycling
    m_isRunning = true;
    m_hThread = CreateThread(NULL, 0, AnimationThreadProc, this, 0, NULL);
    if (!m_hThread)
    {
        Logger::LogError("TileAnimator: Failed to create animation thread");
        m_isRunning = false;
        return false;
    }

    Logger::LogInfo("TileAnimator initialized successfully with 32bpp ARGB frames");
    return true;
}

void TileAnimator::Shutdown()
{
    m_isRunning = false;

    if (m_hStopEvent)
    {
        SetEvent(m_hStopEvent);
    }
    if (m_hWakeEvent)
    {
        SetEvent(m_hWakeEvent);
    }

    if (m_hThread)
    {
        WaitForSingleObject(m_hThread, 1000);
        CloseHandle(m_hThread);
        m_hThread = NULL;
    }

    if (m_hWakeEvent)
    {
        CloseHandle(m_hWakeEvent);
        m_hWakeEvent = NULL;
    }
    if (m_hStopEvent)
    {
        CloseHandle(m_hStopEvent);
        m_hStopEvent = NULL;
    }

    CleanupBitmaps();

    if (m_gdiplusInitialized)
    {
        Gdiplus::GdiplusShutdown(m_gdiplusToken);
        m_gdiplusInitialized = false;
    }
}

void TileAnimator::SetState(PeekUiState newState)
{
    HBITMAP hbmpNotify = nullptr;

    EnterCriticalSection(&m_cs);
    m_currentState = newState;
    m_currentFrameIndex = 0;

    auto it = m_stateFrames.find(newState);
    if (it != m_stateFrames.end() && !it->second.empty())
    {
        hbmpNotify = it->second[0];
    }
    LeaveCriticalSection(&m_cs);

    // Wake the worker thread immediately so any in-flight frame timer is cancelled
    if (m_hWakeEvent)
    {
        SetEvent(m_hWakeEvent);
    }

    // Deliver the new state's initial frame immediately
    if (m_callback && hbmpNotify)
    {
        m_callback(hbmpNotify);
    }
}

PeekUiState TileAnimator::GetState() const
{
    EnterCriticalSection(&m_cs);
    PeekUiState s = m_currentState;
    LeaveCriticalSection(&m_cs);
    return s;
}

HBITMAP TileAnimator::GetCurrentFrame() const
{
    EnterCriticalSection(&m_cs);
    HBITMAP hbmp = nullptr;
    auto it = m_stateFrames.find(m_currentState);
    if (it != m_stateFrames.end() && !it->second.empty())
    {
        size_t idx = m_currentFrameIndex % it->second.size();
        hbmp = it->second[idx];
    }
    LeaveCriticalSection(&m_cs);
    return hbmp;
}

DWORD WINAPI TileAnimator::AnimationThreadProc(LPVOID lpParam)
{
    TileAnimator* pThis = reinterpret_cast<TileAnimator*>(lpParam);
    if (pThis)
    {
        pThis->AnimationLoop();
    }
    return 0;
}

void TileAnimator::AnimationLoop()
{
    while (m_isRunning.load())
    {
        EnterCriticalSection(&m_cs);
        PeekUiState currentState = m_currentState;
        size_t frameCount = 0;
        auto it = m_stateFrames.find(currentState);
        if (it != m_stateFrames.end())
        {
            frameCount = it->second.size();
        }
        LeaveCriticalSection(&m_cs);

        // If the current state has only 1 frame (e.g. SUCCESS, FAILURE, RETRY, FACE_FOUND, IDLE),
        // there is no timer cycling needed. Wait indefinitely until SetState or Shutdown wakes us.
        if (frameCount <= 1)
        {
            HANDLE handles[2] = { m_hStopEvent, m_hWakeEvent };
            DWORD waitRes = WaitForMultipleObjects(2, handles, FALSE, INFINITE);
            if (waitRes == WAIT_OBJECT_0)
            {
                break; // Stop requested
            }
            // State changed or woken up; loop around to handle new state
            continue;
        }

        // For multi-frame states (SEARCHING: 3 pulse frames), wait ~200ms per frame
        HANDLE handles[2] = { m_hStopEvent, m_hWakeEvent };
        DWORD waitRes = WaitForMultipleObjects(2, handles, FALSE, 200);

        if (waitRes == WAIT_OBJECT_0)
        {
            break; // Stop requested
        }
        else if (waitRes == WAIT_OBJECT_0 + 1)
        {
            // State changed during wait! Immediately loop around to process new state.
            // In-flight animation frame is discarded; state transition ALWAYS wins.
            continue;
        }
        else if (waitRes == WAIT_TIMEOUT)
        {
            // Timer expired: cycle to next frame within the current multi-frame state
            HBITMAP hbmpNotify = nullptr;

            EnterCriticalSection(&m_cs);
            // Verify state has not changed
            if (m_currentState == currentState && !m_stateFrames[currentState].empty())
            {
                m_currentFrameIndex = (m_currentFrameIndex + 1) % m_stateFrames[currentState].size();
                hbmpNotify = m_stateFrames[currentState][m_currentFrameIndex];
            }
            LeaveCriticalSection(&m_cs);

            if (m_callback && hbmpNotify)
            {
                m_callback(hbmpNotify);
            }
        }
    }
}

HBITMAP TileAnimator::LoadPngResourceToDib(HINSTANCE hInstance, int resourceId)
{
    if (!hInstance) return nullptr;

    HRSRC hRes = FindResourceW(hInstance, MAKEINTRESOURCEW(resourceId), RT_RCDATA);
    if (!hRes) return nullptr;

    DWORD dwSize = SizeofResource(hInstance, hRes);
    if (dwSize == 0) return nullptr;

    HGLOBAL hResLoaded = LoadResource(hInstance, hRes);
    if (!hResLoaded) return nullptr;

    void* pResData = LockResource(hResLoaded);
    if (!pResData) return nullptr;

    HGLOBAL hMem = GlobalAlloc(GMEM_MOVEABLE, dwSize);
    if (!hMem) return nullptr;

    void* pMem = GlobalLock(hMem);
    if (!pMem)
    {
        GlobalFree(hMem);
        return nullptr;
    }
    memcpy(pMem, pResData, dwSize);
    GlobalUnlock(hMem);

    IStream* pStream = nullptr;
    HRESULT hr = CreateStreamOnHGlobal(hMem, TRUE, &pStream);
    if (FAILED(hr) || !pStream)
    {
        GlobalFree(hMem);
        return nullptr;
    }

    Gdiplus::Bitmap* pBmp = Gdiplus::Bitmap::FromStream(pStream);
    pStream->Release();

    if (!pBmp || pBmp->GetLastStatus() != Gdiplus::Ok)
    {
        delete pBmp;
        return nullptr;
    }

    UINT width = pBmp->GetWidth();
    UINT height = pBmp->GetHeight();
    if (width == 0 || height == 0)
    {
        delete pBmp;
        return nullptr;
    }

    // Construct 32bpp top-down DIB section for premultiplied ARGB
    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = width;
    bmi.bmiHeader.biHeight = -(LONG)height; // top-down
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    void* pBits = nullptr;
    HDC hdc = GetDC(NULL);
    HBITMAP hDib = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &pBits, NULL, 0);
    ReleaseDC(NULL, hdc);

    if (!hDib || !pBits)
    {
        delete pBmp;
        return nullptr;
    }

    // Convert and extract into 32bpp premultiplied ARGB directly into DIB bits
    Gdiplus::Rect rc(0, 0, width, height);
    Gdiplus::BitmapData bmpData = {};
    bmpData.Width = width;
    bmpData.Height = height;
    bmpData.Stride = width * 4;
    bmpData.PixelFormat = PixelFormat32bppPARGB;
    bmpData.Scan0 = pBits;

    Gdiplus::Status st = pBmp->LockBits(
        &rc,
        Gdiplus::ImageLockModeRead | Gdiplus::ImageLockModeUserInputBuf,
        PixelFormat32bppPARGB,
        &bmpData
    );

    if (st == Gdiplus::Ok)
    {
        pBmp->UnlockBits(&bmpData);
    }
    else
    {
        DeleteObject(hDib);
        hDib = nullptr;
    }

    delete pBmp;
    return hDib;
}

void TileAnimator::CleanupBitmaps()
{
    EnterCriticalSection(&m_cs);
    for (auto& pair : m_stateFrames)
    {
        for (HBITMAP hbmp : pair.second)
        {
            if (hbmp)
            {
                DeleteObject(hbmp);
            }
        }
        pair.second.clear();
    }
    m_stateFrames.clear();
    m_hasFrames = false;
    LeaveCriticalSection(&m_cs);
}
