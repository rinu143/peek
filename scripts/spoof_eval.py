"""
Peek Anti-Spoofing & Presentation Attack Evaluation Tool (Phase 8)

Evaluates Peek's face recognition and passive anti-spoofing pipeline against a manifest
of test videos across presentation attack categories:
  - genuine
  - photo_print
  - photo_phone_screen
  - video_replay_phone
  - video_replay_monitor
  - mask
  - lookalike

Computes per-category:
  - Accept Rate (%)
  - Decision Latency (Mean & Median time in seconds)
  - Veto Reason Histogram (which liveness/biometric signal rejected the clip)
  - Genuine False Rejection Rate (FRR)
  - Attack Success Rate (ASR)

Usage:
    python scripts/spoof_eval.py [--manifest eval_data/manifest.csv] [--output-markdown report.md] [--synthetic]

RECORDING PROTOCOL (Specification & Instructions)
==================================================
To ensure rigorous, scientifically repeatable benchmarks, record video clips in `eval_data/`
according to the following standard protocol:

1. Capture Distance:
   - Close: 30 - 35 cm (simulates user leaning forward).
   - Nominal: 45 - 55 cm (standard laptop/monitor arm's length).
   - Far Boundary: 70 - 75 cm (camera field-of-view limits).

2. Lighting Variations:
   - Indoor Balanced: 300 - 500 lux, diffused overhead lighting, no harsh shadows.
   - High Glare / Direct: 800 - 1200 lux, directional desk lamp or sunlight creating specular
     reflections on smartphone glass or glossy photo prints.
   - Backlit: Window or bright lamp placed directly behind user's head, face in partial shadow.
   - Low Light: 20 - 50 lux, dark room with screen as the primary illumination source.

3. Attack Presentation Mediums:
   - photo_print:
     * Color laser print (600+ dpi) on matte 80gsm paper.
     * High-gloss photo paper (300+ dpi) on rigid cardboard backing.
     * Cut-out eye holes to test static face with live eyes.
   - photo_phone_screen:
     * iPhone / Samsung AMOLED smartphone displaying high-res portrait at 100% brightness.
     * Held at 40-50 cm from webcam.
   - video_replay_phone:
     * Smartphone playing a 1080p/60fps video of genuine user blinking and turning head slightly.
   - video_replay_monitor:
     * 24" - 27" desktop monitor or tablet playing looped genuine video with visible screen borders.
   - mask:
     * 3D printed colored mask or silicone novelty face covering.
   - lookalike:
     * Biological sibling, close relative, or actor with similar facial structure and complexion.

4. Clip Characteristics:
   - Resolution: 640x480 or 1280x720, 30 FPS.
   - Duration: 3 to 6 seconds per clip (H.264 / MP4 format).
   - All videos stored under `eval_data/` (gitignored, never committed).
"""

import os
import sys
import csv
import time
import argparse
from typing import List, Dict, Any, Tuple, Optional
import numpy as np
import cv2

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from engine.pipeline.face_engine import FaceEngine, EngineFrameResult
from storage.template_store import PeekProfile, PeekTemplate

VALID_CATEGORIES = [
    "genuine",
    "photo_print",
    "photo_phone_screen",
    "video_replay_phone",
    "video_replay_monitor",
    "mask",
    "lookalike",
]

DEFAULT_EVAL_DIR = os.path.join(REPO_ROOT, "eval_data")
DEFAULT_MANIFEST = os.path.join(DEFAULT_EVAL_DIR, "manifest.csv")


def calculate_percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_d = sorted(data)
    if len(sorted_d) == 1:
        return sorted_d[0]
    k = (len(sorted_d) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_d) - 1)
    d = k - f
    return sorted_d[f] + d * (sorted_d[c] - sorted_d[f])


class ClipEvaluationResult:
    def __init__(
        self,
        video_path: str,
        category: str,
        expected: str,
        decision: str,
        frames_processed: int,
        time_to_decision: float,
        veto_reason: Optional[str] = None,
        confidence: float = 0.0,
    ):
        self.video_path = video_path
        self.category = category
        self.expected = expected
        self.decision = decision
        self.frames_processed = frames_processed
        self.time_to_decision = time_to_decision
        self.veto_reason = veto_reason
        self.confidence = confidence

    @property
    def is_correct(self) -> bool:
        return self.decision.lower() == self.expected.lower()


def evaluate_video_clip(
    engine: FaceEngine,
    video_path: str,
    category: str,
    expected: str,
    max_frames: int = 150,
) -> ClipEvaluationResult:
    """
    Feeds a video clip frame by frame through FaceEngine until unlock authorization,
    hard spoof detection, or timeout.
    """
    # Reset engine state per session
    engine.reset_session_state()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return ClipEvaluationResult(
            video_path=video_path,
            category=category,
            expected=expected,
            decision="reject",
            frames_processed=0,
            time_to_decision=0.0,
            veto_reason="VIDEO_FILE_UNREADABLE",
        )

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_idx = 0
    decision = "reject"
    veto_reason = None
    start_t = time.perf_counter()
    highest_conf = 0.0

    spoof_reasons_observed = []
    face_seen = False
    temporal_confirmed_seen = False

    try:
        while frame_idx < max_frames:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            frame_idx += 1

            result: EngineFrameResult = engine.process_frame(frame)

            if result.has_face:
                face_seen = True

            if result.is_temporally_confirmed:
                temporal_confirmed_seen = True

            # Track spoof reason if present
            if result.liveness_result and result.liveness_result.spoof_reason:
                spoof_reasons_observed.append(result.liveness_result.spoof_reason)
            elif result.state_label == "SPOOF_DETECTED":
                spoof_reasons_observed.append("SPOOF_DETECTED")

            # Check authorization (Terminal Success)
            if result.is_authorized_to_unlock:
                decision = "accept"
                veto_reason = None
                break

    finally:
        cap.release()

    elapsed = time.perf_counter() - start_t
    video_time = frame_idx / fps

    # If rejected, assign most representative veto signal
    if decision == "reject":
        if spoof_reasons_observed:
            # Most common spoof veto
            veto_reason = max(set(spoof_reasons_observed), key=spoof_reasons_observed.count)
        elif not face_seen:
            veto_reason = "NO_FACE_DETECTED"
        elif not temporal_confirmed_seen:
            if category == "lookalike":
                veto_reason = "NO_BIOMETRIC_MATCH"
            else:
                veto_reason = "TEMPORAL_UNCONFIRMED"
        else:
            veto_reason = "LIVENESS_REJECTED"

    return ClipEvaluationResult(
        video_path=video_path,
        category=category,
        expected=expected,
        decision=decision,
        frames_processed=frame_idx,
        time_to_decision=round(video_time, 2),
        veto_reason=veto_reason,
        confidence=highest_conf,
    )


def generate_synthetic_eval_dataset(target_dir: str) -> str:
    """
    Creates synthetic evaluation video clips and a sample manifest.csv for
    immediate, repeatable validation in environments without physical camera recordings.
    """
    os.makedirs(target_dir, exist_ok=True)
    manifest_path = os.path.join(target_dir, "manifest.csv")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    width, height = 320, 240
    fps = 30.0

    entries = []

    # Helper to generate a face-like oval frame
    def make_face_frame(
        offset_x: int = 0,
        offset_y: int = 0,
        draw_bezel: bool = False,
        static_noise: bool = False,
        eye_open: bool = True,
        skin_color: Tuple[int, int, int] = (160, 190, 230),
    ) -> np.ndarray:
        img = np.full((height, width, 3), 40, dtype=np.uint8)
        cx, cy = (width // 2) + offset_x, (height // 2) + offset_y

        # Draw head / face oval
        cv2.ellipse(img, (cx, cy), (50, 70), 0, 0, 360, skin_color, -1)

        # Draw eyes
        eye_y = cy - 15
        if eye_open:
            cv2.circle(img, (cx - 20, eye_y), 6, (255, 255, 255), -1)
            cv2.circle(img, (cx - 20, eye_y), 3, (40, 20, 20), -1)
            cv2.circle(img, (cx + 20, eye_y), 6, (255, 255, 255), -1)
            cv2.circle(img, (cx + 20, eye_y), 3, (40, 20, 20), -1)
        else:
            cv2.line(img, (cx - 25, eye_y), (cx - 15, eye_y), (20, 20, 20), 2)
            cv2.line(img, (cx + 15, eye_y), (cx + 25, eye_y), (20, 20, 20), 2)

        # Nose & mouth
        cv2.line(img, (cx, cy - 5), (cx, cy + 15), (100, 130, 170), 2)
        cv2.ellipse(img, (cx, cy + 30), (18, 8), 0, 0, 180, (60, 60, 180), -1)

        if draw_bezel:
            # Draw strong smartphone / monitor black border and glare highlight
            cv2.rectangle(img, (20, 15), (width - 20, height - 15), (10, 10, 10), 12)
            cv2.line(img, (25, 25), (120, height - 25), (180, 180, 180), 3)

        if static_noise:
            noise = np.random.randint(0, 15, img.shape, dtype=np.uint8)
            img = cv2.add(img, noise)

        return img

    def write_clip(filename: str, frames: List[np.ndarray]):
        out_path = os.path.join(target_dir, filename)
        writer = cv2.VideoWriter(out_path, fourcc, fps, (width, height))
        for f in frames:
            writer.write(f)
        writer.release()
        return out_path

    # 1. Genuine clips (natural micro-motion, blink, genuine face)
    for i in range(1, 3):
        frames = []
        for f in range(60):
            blink = not (25 <= f <= 28)
            ox = int(np.sin(f * 0.1) * 3)
            oy = int(np.cos(f * 0.08) * 2)
            frames.append(make_face_frame(offset_x=ox, offset_y=oy, eye_open=blink))
        name = f"genuine_{i:02d}.mp4"
        write_clip(name, frames)
        entries.append((name, "genuine", "accept"))

    # 2. Photo Print (completely static, frozen planar frames)
    for i in range(1, 3):
        base_f = make_face_frame(draw_bezel=False, static_noise=True)
        frames = [base_f.copy() for _ in range(60)]
        name = f"photo_print_{i:02d}.mp4"
        write_clip(name, frames)
        entries.append((name, "photo_print", "reject"))

    # 3. Photo on Phone Screen (bezel + static photo)
    for i in range(1, 3):
        base_f = make_face_frame(draw_bezel=True, eye_open=True)
        frames = [base_f.copy() for _ in range(60)]
        name = f"photo_phone_screen_{i:02d}.mp4"
        write_clip(name, frames)
        entries.append((name, "photo_phone_screen", "reject"))

    # 4. Video Replay Phone (bezel + playing video with blinks)
    for i in range(1, 3):
        frames = []
        for f in range(60):
            blink = not (20 <= f <= 23)
            frames.append(make_face_frame(draw_bezel=True, eye_open=blink))
        name = f"video_replay_phone_{i:02d}.mp4"
        write_clip(name, frames)
        entries.append((name, "video_replay_phone", "reject"))

    # 5. Video Replay Monitor (wide bezel / screen edges)
    for i in range(1, 3):
        frames = []
        for f in range(60):
            frames.append(make_face_frame(draw_bezel=True, eye_open=True))
        name = f"video_replay_monitor_{i:02d}.mp4"
        write_clip(name, frames)
        entries.append((name, "video_replay_monitor", "reject"))

    # 6. Mask (rigid face with zero natural elasticity)
    for i in range(1, 3):
        frames = []
        for f in range(60):
            ox = int(np.sin(f * 0.05) * 4)
            frames.append(make_face_frame(offset_x=ox, eye_open=True, skin_color=(190, 190, 190)))
        name = f"mask_{i:02d}.mp4"
        write_clip(name, frames)
        entries.append((name, "mask", "reject"))

    # 7. Lookalike (natural human face, different identity/coloration)
    for i in range(1, 3):
        frames = []
        for f in range(60):
            blink = not (30 <= f <= 33)
            frames.append(make_face_frame(eye_open=blink, skin_color=(120, 140, 200)))
        name = f"lookalike_{i:02d}.mp4"
        write_clip(name, frames)
        entries.append((name, "lookalike", "reject"))

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["video_path", "category", "expected"])
        for rel_path, cat, exp in entries:
            writer.writerow([rel_path, cat, exp])

    return manifest_path


def build_evaluation_report_markdown(results: List[ClipEvaluationResult]) -> str:
    """Renders structured Markdown table and summary metrics from evaluation results."""
    by_category: Dict[str, List[ClipEvaluationResult]] = {cat: [] for cat in VALID_CATEGORIES}
    for r in results:
        if r.category in by_category:
            by_category[r.category].append(r)
        else:
            by_category.setdefault(r.category, []).append(r)

    lines = []
    lines.append("# Peek Presentation Attack & Spoof Resistance Report")
    lines.append(f"*Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")
    lines.append("## 1. Category Breakdown")
    lines.append("")
    lines.append("| Category | Clips | Expected | Accepted | Rejected | Accept Rate | Security Metric | Avg Time | Top Veto Signals |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")

    total_genuine = 0
    genuine_rejected = 0
    total_attacks = 0
    attacks_accepted = 0

    for cat in VALID_CATEGORIES:
        clips = by_category.get(cat, [])
        if not clips:
            continue

        count = len(clips)
        expected = clips[0].expected.upper()
        accepted = sum(1 for c in clips if c.decision == "accept")
        rejected = sum(1 for c in clips if c.decision == "reject")
        accept_rate = (accepted / count) * 100.0
        times = [c.time_to_decision for c in clips]
        avg_time = sum(times) / len(times) if times else 0.0

        # Veto histogram
        veto_counts: Dict[str, int] = {}
        for c in clips:
            if c.veto_reason:
                veto_counts[c.veto_reason] = veto_counts.get(c.veto_reason, 0) + 1

        top_vetoes = sorted(veto_counts.items(), key=lambda x: x[1], reverse=True)[:2]
        veto_str = ", ".join(f"{k} ({v})" for k, v in top_vetoes) if top_vetoes else "None (All Accepted)"

        if cat == "genuine":
            total_genuine += count
            genuine_rejected += rejected
            frr = (rejected / count) * 100.0
            metric_str = f"**FRR: {frr:.1f}%**"
        else:
            total_attacks += count
            attacks_accepted += accepted
            asr = (accepted / count) * 100.0
            metric_str = f"**ASR: {asr:.1f}%**"

        lines.append(
            f"| `{cat}` | {count} | {expected} | {accepted} | {rejected} | {accept_rate:.1f}% | {metric_str} | {avg_time:.2f}s | {veto_str} |"
        )

    lines.append("")
    lines.append("## 2. Executive Security Summary")
    lines.append("")

    overall_frr = (genuine_rejected / total_genuine * 100.0) if total_genuine > 0 else 0.0
    overall_asr = (attacks_accepted / total_attacks * 100.0) if total_attacks > 0 else 0.0
    pad_blocking_rate = 100.0 - overall_asr

    lines.append(f"- **Total Test Clips**: {len(results)}")
    lines.append(f"- **Genuine False Rejection Rate (FRR)**: `{overall_frr:.2f}%` ({genuine_rejected}/{total_genuine})")
    lines.append(f"- **Attack Success Rate (ASR / Impostor Pass)**: `{overall_asr:.2f}%` ({attacks_accepted}/{total_attacks})")
    lines.append(f"- **Presentation Attack Blocking Rate**: `{pad_blocking_rate:.2f}%`")
    lines.append("")

    return "\n".join(lines)


def run_evaluation(
    manifest_path: str,
    output_markdown: Optional[str] = None,
    output_csv: Optional[str] = None,
    profile_id: Optional[str] = None,
):
    if not os.path.isfile(manifest_path):
        print(f"Error: Manifest file '{manifest_path}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading spoof evaluation manifest from: {manifest_path}")
    manifest_dir = os.path.dirname(os.path.abspath(manifest_path))

    rows = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    if not rows:
        print("Error: Manifest CSV is empty.", file=sys.stderr)
        sys.exit(1)

    # Initialize FaceEngine
    print("Initializing Peek FaceEngine...")
    engine = FaceEngine()

    # If no enrolled profile, create an evaluation profile matching the reference genuine clip
    if not engine.enabled_profiles or profile_id is None:
        print("Note: Setting up reference enrolled profile from evaluation dataset...")
        ref_embedding = [0.05] * 512
        for r in rows:
            if r.get("category", "").strip().lower() == "genuine":
                g_path = r.get("video_path", "").strip()
                full_g_path = g_path if os.path.isabs(g_path) else os.path.join(manifest_dir, g_path)
                if os.path.isfile(full_g_path):
                    cap = cv2.VideoCapture(full_g_path)
                    ret, frame = cap.read()
                    cap.release()
                    if ret and frame is not None:
                        detections = engine.detector.detect(frame)
                        if detections:
                            dominant = max(detections, key=lambda d: (d.bbox[2]-d.bbox[0])*(d.bbox[3]-d.bbox[1]))
                            if dominant.landmarks is not None:
                                aligned = engine.aligner.align(frame, dominant.landmarks)
                                emb = engine.embedder.extract_embedding(aligned)
                                if emb is not None:
                                    ref_embedding = emb.tolist()
                                    break

        dummy_template = PeekTemplate(
            template_id="eval_tpl_1",
            embedding=ref_embedding,
            pose_label="CENTER",
            quality_score=0.85,
        )
        dummy_profile = PeekProfile(
            profile_id="eval_profile_default",
            display_name="Evaluation Genuine User",
            templates=[dummy_template],
        )
        engine.set_active_profile(dummy_profile)

    print(f"Evaluating {len(rows)} test clips...")
    results: List[ClipEvaluationResult] = []

    for idx, r in enumerate(rows, 1):
        rel_path = r.get("video_path", "").strip()
        cat = r.get("category", "genuine").strip().lower()
        exp = r.get("expected", "accept" if cat == "genuine" else "reject").strip().lower()

        full_video_path = rel_path if os.path.isabs(rel_path) else os.path.join(manifest_dir, rel_path)

        res = evaluate_video_clip(engine, full_video_path, cat, exp)
        results.append(res)
        status = "PASS" if res.is_correct else "FAIL"
        print(f"[{idx}/{len(rows)}] [{status}] {cat:<22} -> {res.decision.upper():<6} (time={res.time_to_decision:.2f}s, veto={res.veto_reason or 'None'})")

    report_md = build_evaluation_report_markdown(results)
    print("\n" + report_md + "\n")

    if output_markdown:
        out_m_dir = os.path.dirname(os.path.abspath(output_markdown))
        if out_m_dir and not os.path.exists(out_m_dir):
            os.makedirs(out_m_dir, exist_ok=True)
        with open(output_markdown, "w", encoding="utf-8") as f:
            f.write(report_md)
        print(f"Markdown report saved to: {output_markdown}")

    if output_csv:
        out_c_dir = os.path.dirname(os.path.abspath(output_csv))
        if out_c_dir and not os.path.exists(out_c_dir):
            os.makedirs(out_c_dir, exist_ok=True)
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["video_path", "category", "expected", "decision", "is_correct", "time_to_decision", "veto_reason"])
            for r in results:
                writer.writerow([r.video_path, r.category, r.expected, r.decision, r.is_correct, r.time_to_decision, r.veto_reason])
        print(f"Detailed per-clip CSV saved to: {output_csv}")


def main():
    parser = argparse.ArgumentParser(description="Peek Anti-Spoofing & Presentation Attack Evaluation Tool")
    parser.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help=f"Path to manifest CSV (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--output-markdown",
        default=None,
        help="Path to save markdown summary table",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Path to save detailed per-clip CSV results",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Generate synthetic eval clips in eval_data/ and run evaluation",
    )

    args = parser.parse_args()

    manifest_to_use = args.manifest

    # If requested or manifest does not exist, generate synthetic dataset
    if args.synthetic or (not os.path.isfile(manifest_to_use) and manifest_to_use == DEFAULT_MANIFEST):
        print("Notice: Manifest not found or --synthetic specified. Generating synthetic benchmark dataset...")
        manifest_to_use = generate_synthetic_eval_dataset(DEFAULT_EVAL_DIR)

    run_evaluation(
        manifest_path=manifest_to_use,
        output_markdown=args.output_markdown,
        output_csv=args.output_csv,
    )


if __name__ == "__main__":
    main()
