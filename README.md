# Peek — Face-Unlock for Windows

> **"Look, and you're in."**

**Peek** is a biometric face-recognition unlock system built specifically for Windows. Inspired by the macOS project [Glance](https://github.com/jonnyoo/glance), Peek adapts the core concepts—multi-pose guided enrollment, deep ArcFace embeddings, independent liveness gating, and state-driven UX—to Windows' native security boundaries, Windows Media Foundation, and the Windows Credential Provider architecture.

---

## Non-Negotiable Security Constraints

Peek is engineered with strict security boundaries:

1. **Face match alone can never unlock**: Biometric similarity and liveness detection are independent gates. Both must pass before authentication can proceed.
2. **No raw frames persist**: Camera frames are processed strictly in-memory and discarded immediately after 512-dimensional embedding extraction.
3. **Fail open, never fail closed**: If the camera is unavailable or an internal error occurs, Peek gracefully yields to standard Windows authentication (Password / PIN / Windows Hello). A failure will never lock a user out of their machine.
4. **Encrypted at rest**: All biometric templates and profile metadata are encrypted using Windows DPAPI (`CryptProtectData`). No plaintext embeddings touch disk.
5. **No secrets or bypasses**: Shipped code contains no hardcoded bypasses, auto-success flags, or plaintext credentials.
6. **No OS tampering**: Peek operates cleanly alongside existing credential providers as an additional credential tile without modifying or spoofing standard OS authentication packages.

---

## Architectural Pipeline

```text
Live Camera Frame (Media Foundation)
       │
       ▼
Face Detection (SCRFD-500M ONNX)
       │
       ▼
5-Point Facial Landmarks (Eyes, Nose, Mouth Corners)
       │
       ├───► Head Pose Estimation (Yaw, Pitch, Roll)
       │
       ▼
Face Alignment (Umeyama Similarity Transform to 112×112)
       │
       ▼
ArcFace Embedding (MobileFaceNet ONNX → 512-D L2-Normalized Vector)
       │
       ▼
Cosine Similarity Matching (against DPAPI-enrolled templates)
       │
       ▼
Liveness & Anti-Spoof Gate (Mandatory)
       │
       ▼
Windows Credential Provider (Named Pipe IPC)
       │
       ▼
Windows Authentication / Unlock
```

---

## Repository Structure

```text
Peek/
├── apps/
│   ├── PeekEnrollment/                  # Phase 2 standalone 9-direction guided enrollment app
│   │   └── main.py
│   └── PeekPrototype/                   # Phase 1 interactive desktop recognition prototype
│       └── main.py
├── engine/                              # Core Peek Face Engine
│   ├── camera/                          # Media Foundation camera capture
│   ├── detection/                       # SCRFD face & landmark detector
│   ├── alignment/                       # 5-point Umeyama similarity transform
│   ├── embedding/                       # ArcFace 512-D embedding extractor
│   ├── recognition/                     # Cosine similarity matcher
│   ├── pose/                            # Landmark-based head pose estimation (yaw, pitch, roll)
│   ├── quality/                         # Blur, illumination, sizing, and single-face gating
│   ├── enrollment/                      # 9-direction state machine & candidate selection
│   ├── pipeline/                        # End-to-end pipeline orchestrator
│   └── models/                          # Automated ONNX model fetcher & storage
├── storage/                             # DPAPI-encrypted profile store
├── tests/                               # Automated unit and pipeline tests
│   ├── test_engine_pipeline.py          # Phase 1 pipeline tests
│   └── test_enrollment_pipeline.py      # Phase 2 pose, quality, & enrollment tests
├── scripts/                             # Verification & validation scripts
│   ├── verify_phase1.py                 # Phase 1 offline verification script
│   └── verify_phase2.py                 # Phase 2 offline verification script
├── requirements.txt                     # Python dependencies
└── Glance_Windows_Implementation_Plan_README.md  # Reference architecture specification
```

---

## Getting Started

### 1. Prerequisites
- **OS**: Windows 10 or Windows 11 (64-bit)
- **Runtime**: Python 3.10+ (tested on Python 3.14)
- **Hardware**: Standard RGB webcam

### 2. Installation
Clone or navigate to the repository directory and install dependencies:
```powershell
pip install -r requirements.txt
```

### 3. Guided Multi-Angle Face Enrollment
Run the standalone guided enrollment application:
```powershell
python apps/PeekEnrollment/main.py
```
- **Guided 9-Direction Setup**: Center, Left, Right, Up, Down, and the 4 Diagonals.
- **Moving Target Dial**: A circular guide with a moving target dot prompts your gaze direction.
- **Measured Head Pose**: Pose direction is computed directly from 5 facial landmarks.
- **Quality Gating**: Automatically rejects blurry, poorly lit, or multiple-face frames.
- **Rapid Capture**: Locks onto and captures each pose in $\approx 70\text{ ms}$, completing all 9 directions in under **10 seconds**.
- **DPAPI Encryption**: Final multi-angle profile is encrypted via Windows DPAPI into `%LOCALAPPDATA%\Peek\Profiles`.

### 4. Real-Time Recognition & Verification
Run the interactive face recognition desktop prototype:
```powershell
python apps/PeekPrototype/main.py
```
- **Live detection**: Renders stylized face corner brackets and 5 keypoints in real time.
- **Multi-Angle Matching**: Smoothly recognizes enrolled users across varying head angles.
- **Visual Feedback**: HUD displays `✓ MATCH (XX%)` in vibrant green or `✗ NO MATCH` in red.
- **Controls**:
  - `[E]` : Enroll single face directly.
  - `[C]` : Clear stored profile.
  - `[Q]` or `[ESC]` : Exit cleanly.

---

## Testing & Verification

### Automated Unit Tests
Run the complete unit test suite covering detector, aligner, embedder, DPAPI storage, pose classification, and quality checks:
```powershell
python -m unittest discover tests -v
```

### Phase 1 Verification Script
Runs end-to-end simulated inference, self-match score ($1.0000$), and impostor rejection:
```powershell
python scripts/verify_phase1.py
```

### Phase 2 Verification Script
Runs an automated offline 9-direction enrollment simulation, candidate accumulation, and DPAPI profile persistence:
```powershell
python scripts/verify_phase2.py
```

---

## Phased Development Roadmap

| Phase | Description | Status |
|---|---|---|
| **Phase 1: Face Engine Prototype** | Camera → SCRFD → 5 Landmarks → Umeyama → ArcFace → Similarity | **Completed** |
| **Phase 2: Peek-Style Enrollment** | 9-direction guided enrollment, head-pose estimation, multi-frame quality gating | **Completed** |
| **Phase 3: Recognition Hardening** | Dominant-face tracking, temporal verification window, threshold calibration | Next |
| **Phase 4: Liveness** | Rolling window, blink & micro-movement signals, presentation-attack defense | Planned |
| **Phase 5: Windows Credential Provider** | Native C++/Win32/COM credential provider tile integration | Planned |
| **Phase 6: Engine ↔ CP IPC** | Named Pipe (`\\.\pipe\PeekEngine`) secure request/response protocol | Planned |
| **Phase 7: Lock-Screen UX** | Visual state flow (Searching → Found → Verifying → Liveness → Unlock) | Planned |
| **Phase 8: Performance & Security** | Latency, CPU/RAM benchmarks, attack resistance, and sleep/wake recovery | Planned |
