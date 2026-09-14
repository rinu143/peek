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
│   └── PeekPrototype/                   # Phase 1 interactive desktop application
│       └── main.py
├── engine/                              # Core Peek Face Engine
│   ├── camera/                          # Media Foundation camera capture
│   ├── detection/                       # SCRFD face & landmark detector
│   ├── alignment/                       # 5-point Umeyama similarity transform
│   ├── embedding/                       # ArcFace 512-D embedding extractor
│   ├── recognition/                     # Cosine similarity matcher
│   ├── pipeline/                        # End-to-end pipeline orchestrator
│   └── models/                          # Automated ONNX model fetcher & storage
├── storage/                             # DPAPI-encrypted profile store
├── tests/                               # Automated unit and pipeline tests
├── scripts/                             # Verification & validation scripts
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

### 3. Launching the Face Recognition Prototype
Run the interactive desktop application:
```powershell
python apps/PeekPrototype/main.py
```
- **Live detection**: Renders face bounding brackets and 5 keypoints in real time.
- **Enrollment (`E`)**: Press `E` to capture your face and encrypt it into a DPAPI profile.
- **Verification**: HUD displays `✓ MATCH (XX%)` when verified, or `✗ NO MATCH` for unregistered faces.
- **Clear Profile (`C`)**: Clears the stored biometric profile.
- **Exit (`Q` / `ESC`)**: Releases the camera and closes cleanly.

---

## Testing & Verification

### Automated Unit Tests
Run the test suite verifying detector, aligner, embedder unit norm, DPAPI encryption at rest, and security constraint checks:
```powershell
python -m unittest discover tests -v
```

### Pipeline Verification Script
Run an end-to-end offline verification verifying model inference, self-match scores, impostor rejection, and DPAPI round-trip:
```powershell
python scripts/verify_phase1.py
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
