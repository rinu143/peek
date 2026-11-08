# Presentation Attack Detection (Spoof) Evaluation Results

## Status: Not Yet Measured

This document records the empirical results of evaluating Peek's biometric and passive liveness pipeline using [`scripts/spoof_eval.py`](file:///E:/Downloads/Peek/scripts/spoof_eval.py) against physical presentation attack instruments (PAIs).

> [!NOTE]
> Physical test recordings across real capture conditions (low-light, direct glare, print attacks, screens, and masks) have **not yet been measured**.
> To populate this document with experimental evaluation results, collect attack and genuine video clips following the standardized recording protocol and execute `scripts/spoof_eval.py`.

---

## Evaluation Benchmark Target Template

When evaluation data is collected, running the command:
```powershell
python scripts/spoof_eval.py --manifest eval_data/manifest.csv --output-markdown docs/spoof_results.md
```
will populate the empirical metrics table below:

| Attack Category | Total Samples | Expected | Accepted | Rejected | Accept Rate (%) | Security Metric | Avg Decision Time | Primary Veto Reason(s) |
|---|---|---|---|---|---|---|---|---|
| `genuine` | 0 | accept | 0 | 0 | 0.0% | **FRR: N/A** | 0.00s | *Not yet measured* |
| `photo_print` | 0 | reject | 0 | 0 | 0.0% | **ASR: N/A** | 0.00s | *Not yet measured* |
| `photo_phone_screen` | 0 | reject | 0 | 0 | 0.0% | **ASR: N/A** | 0.00s | *Not yet measured* |
| `video_replay_phone` | 0 | reject | 0 | 0 | 0.0% | **ASR: N/A** | 0.00s | *Not yet measured* |
| `video_replay_monitor` | 0 | reject | 0 | 0 | 0.0% | **ASR: N/A** | 0.00s | *Not yet measured* |
| `mask` | 0 | reject | 0 | 0 | 0.0% | **ASR: N/A** | 0.00s | *Not yet measured* |
| `lookalike` | 0 | reject | 0 | 0 | 0.0% | **ASR: N/A** | 0.00s | *Not yet measured* |

---

## Recording Protocol & Methodology

Evaluation videos are collected according to the test protocol defined in [`scripts/spoof_eval.py`](file:///E:/Downloads/Peek/scripts/spoof_eval.py):
1. **Capture Distances**: Close (30–35 cm), Nominal (45–55 cm), and Far (70–75 cm).
2. **Lighting Variations**:
   - Indoor Balanced: 300–500 lux.
   - High Glare / Direct: 800–1200 lux with specular highlights on display glass or glossy photo prints.
   - Backlit: Strong background illumination behind head.
   - Low Light: 20–50 lux dark room.
3. **Attack Instruments**:
   - High-gloss and matte 600+ dpi photo prints.
   - OLED smartphone displays displaying high-resolution portraits at 100% brightness.
   - 1080p 60fps video replay with head tilt and eye blink cues.
   - 24"–27" desktop monitor video replays.
   - 3D masks and physical lookalikes.
4. **Data Isolation**: Video clips are stored under `eval_data/` (gitignored, never committed to version control).
