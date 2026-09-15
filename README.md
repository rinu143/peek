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
Face Tracking & Dominant Target Selection (IoU, Centroids, Hysteresis)
       │
       ├───► Passive Multi-Cue Liveness Gate (3D Parallax, Screen Moiré, Eye Dynamics)
       │
       ▼
Face Alignment (Umeyama Similarity Transform to 112×112)
       │
       ▼
ArcFace Embedding (MobileFaceNet ONNX → 512-D L2-Normalized Vector)
       │
       ▼
Cosine Similarity Matching (Top-3 Mean + Pose Affinity Bonus)
       │
       ▼
Temporal Verification Window (M-of-N Rolling Sliding Window & EMA Smoothing)
       │
       ▼
Dual-Gate Authorization Decision (Biometric Match AND Independent Liveness Gate)
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
│   └── PeekPrototype/                   # Phase 1, 3, & 4 interactive desktop recognition prototype
│       └── main.py
├── engine/                              # Core Peek Face Engine
│   ├── camera/                          # Media Foundation camera capture
│   ├── detection/                       # SCRFD face & landmark detector
│   ├── alignment/                       # 5-point Umeyama similarity transform
│   ├── embedding/                       # ArcFace 512-D embedding extractor
│   ├── recognition/                     # Multi-template matcher & threshold calibrator
│   │   ├── matcher.py
│   │   └── calibrator.py
│   ├── tracking/                        # Dominant face tracker with IoU, centroids, & hysteresis
│   │   └── face_tracker.py
│   ├── temporal/                        # M-of-N rolling sliding window & EMA score smoothing
│   │   └── temporal_verifier.py
│   ├── liveness/                        # Phase 4 passive RGB multi-cue anti-spoofing engine
│   │   ├── motion_detector.py           # 3D landmark parallax & foreshortening ratio analysis
│   │   ├── bezel_detector.py            # Macro device bezel & rectangular screen chassis detection
│   │   ├── texture_checker.py           # Screen moiré FFT & specular reflection analysis
│   │   ├── eye_dynamics.py              # Eye openness, gradient contrast, & blink detection
│   │   ├── challenge.py                 # Optional active challenge-response mode (head turns, blinks)
│   │   └── liveness_detector.py         # Veto-based score fusion & spoof state machine
│   ├── pose/                            # Landmark-based head pose estimation (yaw, pitch, roll)
│   ├── quality/                         # Blur, illumination, sizing, and single-face gating
│   ├── enrollment/                      # 9-direction state machine & candidate selection
│   ├── pipeline/                        # End-to-end pipeline orchestrator with dual-gate authorization
│   │   └── face_engine.py
│   └── models/                          # Automated ONNX model fetcher & storage
├── storage/                             # DPAPI-encrypted profile store
├── tests/                               # Automated unit and pipeline tests
│   ├── test_engine_pipeline.py          # Phase 1 pipeline tests
│   ├── test_enrollment_pipeline.py      # Phase 2 pose, quality, & enrollment tests
│   ├── test_recognition_hardening.py    # Phase 3 tracking, temporal verification, & calibration tests
│   ├── test_liveness.py                 # Phase 4 anti-spoofing & dual-gate authorization tests
│   ├── test_bezel_detector.py           # Phase 4 hardening: smartphone bezel detection tests
│   ├── test_motion_parallax.py          # Phase 4 hardening: 3D parallax vs 2D hand tremor tests
│   ├── test_challenge_response.py       # Phase 4 hardening: active challenge-response tests
│   └── test_screen_replay_regression.py # Phase 4 hardening: synthetic screen replay regression tests
├── scripts/                             # Verification & validation scripts
│   ├── verify_phase1.py                 # Phase 1 offline verification script
│   ├── verify_phase2.py                 # Phase 2 offline verification script
│   ├── verify_phase3.py                 # Phase 3 offline verification script
│   └── verify_phase4.py                 # Phase 4 offline verification script
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

### 4. Real-Time Recognition & Liveness Verification
Run the interactive face recognition desktop prototype:
```powershell
python apps/PeekPrototype/main.py
```
- **Dual-Gate Authorization**: Implements **Non-Negotiable Security Constraint #1**: face match alone can never unlock Windows. An unlock is authorized *only* when both the biometric temporal match gate and the independent passive liveness gate pass.
- **Hardened Multi-Signal Anti-Spoofing Engine**:
  - *Veto-Based Score Fusion*: Any single hard presentation attack indicator immediately caps the overall frame score to $\le 0.12$, preventing dilution across other scoring channels.
  - *Macro Device Bezel & Screen Chassis Detection*: Scans the bounding context around the face using Hough transforms, edge contrast step analysis, and right-angle rectilinear pair checks to detect smartphones, tablets, or laptop screen edges (`BEZEL_DETECTED`).
  - *3D Perspective Parallax & Landmark Foreshortening*: Analyzes inter-feature ratios ($\rho_1 = \text{nose-to-eyes}/\text{inter-eye}$, $\rho_2 = \text{nose-to-mouth}/\text{mouth-width}$) across time to differentiate living 3D face rotation from flat 2D hand tremor (`LACKS_PARALLAX`, `RIGID_PLANAR_MOTION`, `STATIC_PHOTO`).
  - *Texture & Screen Moiré Frequency Spectra*: Computes FFT high-frequency band power and spatial variance to catch LCD/OLED display grids (`SCREEN_MOIRE`) and glass glare (`SPECULAR_GLARE`).
  - *Eye Dynamics & Blink Tracking*: Evaluates pupil patch vertical contrast gradients and eyelid blink events.
  - *Optional Active Challenge-Response*: Issues unpredictable, randomized prompts (`TURN_LEFT`, `TURN_RIGHT`, `TILT_UP`, `BLINK`) with strict time budgets for high-assurance scenarios.
- **Persistent Track IDs & Dominant Face Stickiness**: Assigns stable track IDs and enforces hysteresis (+25% area margin, 4+ frames) so bystanders cannot hijack authentication.
- **Multi-Template Matching**: Compares live embeddings against all enrolled pose angles using cosine similarity and pose-affinity weighting.
- **Rolling Temporal Verification Window**: Requires an $M$-of-$N$ sliding window (5 of 7 consecutive frames) with EMA score smoothing ($\alpha = 0.40$).
- **Live Visual Feedback**:
  - Displays dual telemetry meters in the HUD: Temporal match progress (`Temporal 5/5`) and Liveness score (`Live: 85%`).
  - Real-time spoof detection alerts (`⚠ SPOOF DETECTED (BEZEL_DETECTED)` in crimson).
  - Glowing emerald celebration border and `✓ UNLOCKED — LOOK, AND YOU'RE IN.` banner upon dual-gate authorization.
- **Controls**:
  - `[E]` : Enroll single face directly.
  - `[C]` : Clear stored profile.
  - `[Q]` or `[ESC]` : Exit cleanly.

---

## Testing & Verification

### Automated Unit Tests
Run the complete unit test suite covering detector, aligner, embedder, DPAPI storage, pose classification, quality checks, face tracking, temporal verification, threshold calibration, motion parallax, texture moiré, eye dynamics, and dual-gate security:
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

### Phase 3 Verification Script
Runs an end-to-end simulation of multi-frame tracking, dominant target stickiness against bystanders, $M$-of-$N$ temporal confirmation, track-loss reset, and FAR/FRR threshold calibration:
```powershell
python scripts/verify_phase3.py
```

### Phase 4 Verification Script
Runs an automated end-to-end simulation of genuine live stream acceptance, static photo attack blocking, waved 2D photo attack blocking, screen replay moiré blocking, and strict dual-gate authorization enforcement:
```powershell
python scripts/verify_phase4.py
```

---

## Phased Development Roadmap

| Phase | Description | Status |
|---|---|---|
| **Phase 1: Face Engine Prototype** | Camera → SCRFD → 5 Landmarks → Umeyama → ArcFace → Similarity | **Completed** |
| **Phase 2: Peek-Style Enrollment** | 9-direction guided enrollment, head-pose estimation, multi-frame quality gating | **Completed** |
| **Phase 3: Recognition Hardening** | Dominant-face tracking, temporal verification window, threshold calibration | **Completed** |
| **Phase 4: Liveness & Hardening** | Veto-based fusion, phone bezel detector, 3D parallax, challenge-response | **Completed** |
| **Phase 5: Windows Credential Provider** | Native C++/Win32/COM credential provider tile integration | Next |
| **Phase 6: Engine ↔ CP IPC** | Named Pipe (`\\.\pipe\PeekEngine`) secure request/response protocol | Planned |
| **Phase 7: Lock-Screen UX** | Visual state flow (Searching → Found → Verifying → Liveness → Unlock) | Planned |
| **Phase 8: Performance & Security** | Latency, CPU/RAM benchmarks, attack resistance, and sleep/wake recovery | Planned |

---

## Security Boundaries, Threat Model & Sensor Disclosures

### 1. RGB Monocular Sensor Reality
Peek is designed to run on standard, commodity 2D RGB webcams without requiring proprietary hardware or dedicated depth sensors. 

> [!IMPORTANT]
> **No Claim of Parity with Dedicated Hardware Windows Hello**:
> Peek does **not** claim biometric equivalence or parity with hardware-based Windows Hello. Official Windows Hello Face requires specialized hardware featuring structured-light or time-of-flight (ToF) depth sensors and dedicated Near-Infrared (NIR) illumination with an IR sensor. Standard monocular RGB webcams lack true physical depth capture at the sensor level.

### 2. Mitigated Presentation Attack Classes (Phase 4 Hardened)
Peek defends against common presentation attacks using algorithmic multi-cue verification and veto-based fusion:

| Attack Vector | Vulnerability Mechanism | Peek Defense & Veto Rule |
|---|---|---|
| **Static Photo / Paper Printout** | Zero movement across time | `STATIC_PHOTO`: Landmark centroid jitter $<0.25\text{ px}$ and pixel difference $<1.0$ triggers immediate veto. |
| **Waved 2D Photo / Tablet** | Rigid planar translation | `RIGID_PLANAR_MOTION`: Uniform 2D displacement without differential depth movement triggers veto. |
| **Smartphone Screen Replay (Handheld)** | Hand tremor mimicking micro-movement | `LACKS_PARALLAX`: Tracks relative landmark foreshortening ($\rho_1, \rho_2$). Invariant 2D spacing ($\sigma < 0.0022$) triggers veto. |
| **Visible Smartphone / Tablet Bezel** | Phone chassis held in front of webcam | `BEZEL_DETECTED`: Rectilinear edge pairs, high step-gradient contrast, and aspect-ratio checks ($1.3 - 2.4$) veto frame score to $\le 0.12$. |
| **Screen Moiré / Pixel Grids** | Periodic subpixel sampling artifacts | `SCREEN_MOIRE`: 2D FFT spectral analysis in high-frequency bands detects screen display artifacts. |
| **Pre-Recorded Video Replay Loop** | Looped video of genuine user blinking | **Active Challenge-Response Mode**: Issues randomized, non-repeating physiological prompts (`TURN_LEFT`, `TURN_RIGHT`, `TILT_UP`, `BLINK`) requiring real-time compliance within a $3.5\text{s}$ budget. |

### 3. Residual Risks & Inherent Limitations
Users and administrators should understand the residual risks inherent to any pure RGB monocular computer vision pipeline:
- **Bezel-Less Screens Outside Camera FOV**: If an attacker presents a large 4K/8K monitor positioned so its physical bezels are completely outside the camera's field of view, bezel detection cannot trigger.
- **Occluded Bezels**: If an attacker conceals the device borders using hands, paper, or custom cutouts, bezel detection confidence decreases.
- **Sophisticated 3D Silicone Masks**: High-quality 3D physical masks molded to the user's facial contours possess true 3D depth geometry and natural foreshortening ratios. (Defending against curved silicone masks in RGB requires the active challenge-response mode with randomized prompts).
- **Adverse Lighting**: Extreme low-light conditions may degrade edge detection gradients and frequency spectra, prompting the quality checker to reject frames and fall back to password/PIN.

