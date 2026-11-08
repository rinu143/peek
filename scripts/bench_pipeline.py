"""
Peek Face Pipeline Benchmark Harness
Replays a folder of frames or a video file through FaceEngine, reporting
latency per stage (detect, track, align, embed, match, temporal, liveness, total),
CPU utilization, and peak RSS memory, split by single-face vs multi-face frames.

Usage:
    python scripts/bench_pipeline.py [--input <path_to_video_or_folder>] [--max-frames 100] [--csv results.csv]
"""

import os
import sys
import time
import argparse
import logging
from typing import List, Dict, Any, Tuple, Optional
import numpy as np
import cv2

try:
    import psutil
except ImportError:
    psutil = None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.pipeline.face_engine import FaceEngine, EngineFrameResult
from storage.template_store import PeekProfile, PeekTemplate
from scripts.perf_sample import NativeProcessMonitor

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s]: %(message)s")
logger = logging.getLogger("Peek.BenchPipeline")


def generate_synthetic_benchmark_clip(num_single: int = 40, num_multi: int = 40) -> List[np.ndarray]:
    """
    Generates synthetic realistic test frames:
    - num_single frames with 1 centered face
    - num_multi frames with 2 distinct faces (to benchmark multi-face processing)
    """
    frames = []
    w, h = 640, 480

    # Draw dummy realistic synthetic skin/face contours
    for i in range(num_single):
        frame = np.full((h, w, 3), (180, 160, 140), dtype=np.uint8)
        # Face 1 (center)
        center_x = 320 + int(10 * np.sin(i * 0.2))
        center_y = 240 + int(5 * np.cos(i * 0.2))
        cv2.ellipse(frame, (center_x, center_y), (70, 95), 0, 0, 360, (140, 160, 210), -1)
        # Eyes
        cv2.circle(frame, (center_x - 25, center_y - 20), 8, (60, 50, 40), -1)
        cv2.circle(frame, (center_x + 25, center_y - 20), 8, (60, 50, 40), -1)
        # Mouth
        cv2.ellipse(frame, (center_x, center_y + 35), (20, 8), 0, 0, 180, (90, 80, 160), -1)
        frames.append(frame)

    for i in range(num_multi):
        frame = np.full((h, w, 3), (180, 160, 140), dtype=np.uint8)
        # Face 1 (left)
        x1, y1 = 200, 240
        cv2.ellipse(frame, (x1, y1), (60, 80), 0, 0, 360, (140, 160, 210), -1)
        cv2.circle(frame, (x1 - 20, y1 - 15), 6, (60, 50, 40), -1)
        cv2.circle(frame, (x1 + 20, y1 - 15), 6, (60, 50, 40), -1)
        # Face 2 (right / competing bystander)
        x2, y2 = 440, 230
        cv2.ellipse(frame, (x2, y2), (65, 85), 0, 0, 360, (130, 150, 200), -1)
        cv2.circle(frame, (x2 - 20, y2 - 15), 6, (50, 40, 30), -1)
        cv2.circle(frame, (x2 + 20, y2 - 15), 6, (50, 40, 30), -1)
        frames.append(frame)

    return frames


def load_frames(source: str, max_frames: Optional[int] = None) -> List[np.ndarray]:
    """Loads video frames from a folder of image files or a video container."""
    frames = []
    if os.path.isdir(source):
        valid_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        files = sorted([os.path.join(source, f) for f in os.listdir(source) if os.path.splitext(f)[1].lower() in valid_exts])
        for p in files:
            img = cv2.imread(p)
            if img is not None:
                frames.append(img)
                if max_frames and len(frames) >= max_frames:
                    break
    elif os.path.isfile(source):
        cap = cv2.VideoCapture(source)
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            frames.append(frame)
            if max_frames and len(frames) >= max_frames:
                break
        cap.release()
    else:
        raise FileNotFoundError(f"Input path '{source}' not found.")
    return frames


def compute_percentiles(values: List[float]) -> Tuple[float, float, float]:
    """Returns (mean, p50, p95)."""
    if not values:
        return 0.0, 0.0, 0.0
    arr = np.array(values, dtype=np.float64)
    return float(np.mean(arr)), float(np.percentile(arr, 50)), float(np.percentile(arr, 95))


def run_benchmark(
    frames: List[np.ndarray],
    csv_output: Optional[str] = None
) -> Dict[str, Any]:
    """Executes benchmark loop through FaceEngine and compiles metrics."""
    logger.info(f"Initializing FaceEngine for benchmark on {len(frames)} frames...")
    engine = FaceEngine(match_threshold=0.48, require_active_challenge=False)

    # Attach dummy enrolled profile so matching and temporal logic execute
    dummy_template = PeekTemplate(
        template_id="bench_t1",
        embedding=list(np.random.randn(512).astype(float)),
        pose_label="CENTER",
        quality_score=0.9
    )
    dummy_profile = PeekProfile(
        profile_id="bench_p1",
        display_name="Benchmark User",
        templates=[dummy_template]
    )
    engine.enabled_profiles = [dummy_profile]
    engine.cached_template_embeddings = np.array([dummy_template.embedding], dtype=np.float32)
    engine.cached_template_poses = [dummy_template.pose_label]

    monitor = NativeProcessMonitor(os.getpid())
    monitor.get_cpu_percent()

    peak_rss_mb = 0.0
    cpu_samples = []

    per_frame_records = []

    logger.info("Starting pipeline benchmark replay...")
    start_time = time.time()

    for idx, frame in enumerate(frames):
        mem_mb = monitor.get_rss_mb()
        if mem_mb > peak_rss_mb:
            peak_rss_mb = mem_mb

        res: EngineFrameResult = engine.process_frame(frame)

        if idx % 10 == 0:
            cpu_samples.append(monitor.get_cpu_percent())

        face_count = len(res.all_tracks) if res.all_tracks else (1 if res.has_face else 0)
        timings = getattr(res, "stage_timings", {}) or {}

        rec = {
            "frame": idx,
            "faces": face_count,
            "detect_ms": timings.get("detect_ms", 0.0),
            "track_ms": timings.get("track_pose_ms", 0.0),
            "align_ms": timings.get("align_ms", 0.0),
            "embed_ms": timings.get("embed_ms", 0.0),
            "match_ms": timings.get("match_ms", 0.0),
            "temporal_ms": timings.get("temporal_ms", 0.0),
            "liveness_ms": timings.get("liveness_ms", 0.0),
            "total_ms": timings.get("total_inference_ms", 0.0),
        }
        per_frame_records.append(rec)

    total_duration = time.time() - start_time
    fps = len(frames) / max(0.001, total_duration)
    mean_cpu = float(np.mean(cpu_samples)) if cpu_samples else 0.0

    mem_mb = monitor.get_rss_mb()
    if mem_mb > peak_rss_mb:
        peak_rss_mb = mem_mb

    # Split by face count
    single_face = [r for r in per_frame_records if r["faces"] == 1]
    multi_face = [r for r in per_frame_records if r["faces"] > 1]
    no_face = [r for r in per_frame_records if r["faces"] == 0]

    stages = ["detect_ms", "track_ms", "align_ms", "embed_ms", "match_ms", "temporal_ms", "liveness_ms", "total_ms"]

    def compute_stage_table(records: List[Dict[str, Any]]) -> Dict[str, Tuple[float, float, float]]:
        out = {}
        for s in stages:
            vals = [r[s] for r in records]
            out[s] = compute_percentiles(vals)
        return out

    stats_overall = compute_stage_table(per_frame_records)
    stats_single = compute_stage_table(single_face) if single_face else {}
    stats_multi = compute_stage_table(multi_face) if multi_face else {}

    # Print summary report
    print("\n" + "=" * 70)
    print("             PEEK PIPELINE BENCHMARK REPORT")
    print("=" * 70)
    print(f"Total Frames Processed : {len(frames)}")
    print(f"Total Replay Time      : {total_duration:.2f} s")
    print(f"Processing Throughput  : {fps:.1f} FPS")
    print(f"Average CPU Usage      : {mean_cpu:.1f} %")
    print(f"Peak RSS Memory        : {peak_rss_mb:.2f} MB")
    print("-" * 70)

    def print_stage_stats(title: str, stats_dict: Dict[str, Tuple[float, float, float]], count: int):
        print(f"\n--- {title} (N = {count} frames) ---")
        print(f"{'Stage':<20} {'Mean (ms)':<15} {'p50 (ms)':<15} {'p95 (ms)':<15}")
        print("-" * 65)
        for s in stages:
            if s in stats_dict:
                m, p50, p95 = stats_dict[s]
                name = s.replace("_ms", "").capitalize()
                print(f"{name:<20} {m:<15.2f} {p50:<15.2f} {p95:<15.2f}")

    print_stage_stats("OVERALL (ALL FRAMES)", stats_overall, len(per_frame_records))
    if stats_single:
        print_stage_stats("SINGLE-FACE FRAMES", stats_single, len(single_face))
    if stats_multi:
        print_stage_stats("MULTI-FACE FRAMES", stats_multi, len(multi_face))

    print("\n" + "=" * 70)

    # Optional CSV export
    if csv_output:
        out_dir = os.path.dirname(os.path.abspath(csv_output))
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(csv_output, "w", encoding="utf-8") as f:
            headers = ["frame", "faces", "detect_ms", "track_ms", "align_ms", "embed_ms", "match_ms", "temporal_ms", "liveness_ms", "total_ms"]
            f.write(",".join(headers) + "\n")
            for r in per_frame_records:
                line = ",".join(str(r[h]) for h in headers)
                f.write(line + "\n")
        logger.info(f"Frame-level benchmark metrics exported to: {csv_output}")

    return {
        "total_frames": len(frames),
        "fps": fps,
        "peak_rss_mb": peak_rss_mb,
        "mean_cpu": mean_cpu,
        "overall": stats_overall,
        "single_face": stats_single,
        "multi_face": stats_multi,
    }


def main():
    parser = argparse.ArgumentParser(description="Peek Pipeline Stage Latency & Resource Benchmark")
    parser.add_argument(
        "--input", "-i",
        type=str,
        default=None,
        help="Path to image directory or video file to replay (default: generates synthetic multi-face clip)"
    )
    parser.add_argument(
        "--max-frames", "-n",
        type=int,
        default=None,
        help="Maximum frames to process"
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Optional path to output per-frame CSV"
    )
    args = parser.parse_args()

    if args.input:
        frames = load_frames(args.input, args.max_frames)
    else:
        logger.info("No input clip provided; generating synthetic single-face and multi-face benchmark frames...")
        frames = generate_synthetic_benchmark_clip(num_single=30, num_multi=30)
        if args.max_frames:
            frames = frames[:args.max_frames]

    run_benchmark(frames, csv_output=args.csv)


if __name__ == "__main__":
    main()
