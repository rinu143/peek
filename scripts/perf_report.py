"""
Peek Performance & Latency Report Generator
Parses PeekEngine and Credential Provider log files, calculates p50 and p95 latencies
per authentication stage and end-to-end, split by number of faces seen.
Also parses and summarizes CPU and RAM utilization from performance sample CSVs.

Usage:
    python scripts/perf_report.py [--engine-log <path>] [--cp-log <path>] [--sample-csv <path>]
                                  [--output-csv <path>] [--sample-now] [--duration <sec>]

Acceptance:
    One command produces a latency table and a CPU/RAM CSV.
"""

import os
import sys
import re
import csv
import json
import argparse
from typing import Dict, List, Any, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_ENGINE_LOG = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "logs", "engine.log"
)
DEFAULT_CP_LOG = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "logs", "credential_provider.log"
)
DEFAULT_SAMPLE_CSV = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "logs", "perf_samples.csv"
)

# Regex to capture [LATENCY] lines emitted by engine and credential provider
# Format: [LATENCY] session_id=<id> stage=<stage name> timestamp_ms=<epoch_ms> [extra key=val ...]
LATENCY_PATTERN = re.compile(
    r"\[LATENCY\]\s+session_id=(?P<session_id>\S+)\s+stage=(?P<stage>.+?)\s+timestamp_ms=(?P<timestamp_ms>\d+)(?:\s+(?P<extra>.*))?$"
)


def calculate_percentile(data: List[float], p: float) -> float:
    """Calculates percentile p (0-100) using linear interpolation."""
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


class SessionData:
    def __init__(self, session_id: str):
        self.session_id = session_id
        # Stage milestones: stage_name -> timestamp_ms
        self.milestones: Dict[str, int] = {}
        # Repeated inference measurements: metric_name -> list of float ms
        self.inference_timings: Dict[str, List[float]] = {
            "detect_ms": [],
            "track_pose_ms": [],
            "align_ms": [],
            "embed_ms": [],
            "match_ms": [],
            "temporal_ms": [],
            "liveness_ms": [],
            "total_inference_ms": [],
        }
        self.max_faces_seen: int = 0
        self.authenticated: bool = False

    def add_event(self, stage: str, timestamp_ms: int, extra_dict: Dict[str, str]):
        if stage == "per-stage inference time":
            # Per-frame inference measurement
            for key in self.inference_timings.keys():
                if key in extra_dict:
                    try:
                        self.inference_timings[key].append(float(extra_dict[key]))
                    except ValueError:
                        pass
            if "faces_seen" in extra_dict:
                try:
                    f = int(extra_dict["faces_seen"])
                    if f > self.max_faces_seen:
                        self.max_faces_seen = f
                except ValueError:
                    pass
        else:
            if stage not in self.milestones:
                self.milestones[stage] = timestamp_ms
            if stage == "AUTHENTICATED":
                self.authenticated = True

    @property
    def face_category(self) -> str:
        if self.max_faces_seen == 0:
            return "0 faces"
        elif self.max_faces_seen == 1:
            return "1 face"
        else:
            return ">1 faces"


def parse_latency_logs(engine_log_path: Optional[str], cp_log_path: Optional[str]) -> Dict[str, SessionData]:
    """Parses both log files and correlates events into SessionData objects by session_id."""
    sessions: Dict[str, SessionData] = {}

    def process_file(path: str, source_name: str):
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "[LATENCY]" not in line:
                        continue
                    m = LATENCY_PATTERN.search(line.strip())
                    if not m:
                        continue
                    sess_id = m.group("session_id")
                    stage = m.group("stage").strip()
                    ts_ms = int(m.group("timestamp_ms"))
                    extra_str = m.group("extra") or ""
                    
                    extra_dict = {}
                    if extra_str:
                        for token in extra_str.strip().split():
                            if "=" in token:
                                k, v = token.split("=", 1)
                                extra_dict[k] = v

                    if sess_id not in sessions:
                        sessions[sess_id] = SessionData(sess_id)
                    sessions[sess_id].add_event(stage, ts_ms, extra_dict)
        except Exception as ex:
            print(f"Warning: Failed reading {source_name} ({path}): {ex}", file=sys.stderr)

    if engine_log_path:
        process_file(engine_log_path, "Engine Log")
    if cp_log_path:
        process_file(cp_log_path, "Credential Provider Log")

    return sessions


def analyze_sessions(sessions: Dict[str, SessionData]) -> Dict[str, Any]:
    """
    Computes per-stage and end-to-end latencies split by faces seen:
    'Overall', '0 faces', '1 face', '>1 faces'.
    """
    categories = ["Overall", "0 faces", "1 face", ">1 faces"]
    
    # Milestone transitions we track
    STAGE_TRANSITIONS = [
        ("CP: SetSelected -> pipe connected", "SetSelected", "pipe connected"),
        ("CP: pipe connected -> first state", "pipe connected", "first state"),
        ("Engine: session start -> camera open", "session start", "camera open"),
        ("Engine: camera open -> first frame", "camera open", "first frame"),
        ("Engine: first frame -> first face", "first frame", "first face"),
        ("Engine: first face -> temporal confirmed", "first face", "temporal confirmed"),
        ("Engine: temporal confirmed -> liveness passed", "temporal confirmed", "liveness passed"),
        ("Engine: liveness passed -> AUTHENTICATED", "liveness passed", "AUTHENTICATED"),
        ("CP: AUTHENTICATED -> GetSerialization done", "AUTHENTICATED", "GetSerialization done"),
        ("Engine Capture to Auth (first frame -> AUTH)", "first frame", "AUTHENTICATED"),
        ("Engine End-to-End (session start -> AUTH)", "session start", "AUTHENTICATED"),
        ("Full End-to-End (SetSelected -> Serialization)", "SetSelected", "GetSerialization done"),
    ]

    # Collect transition deltas per category
    # cat -> stage_label -> list of ms
    deltas: Dict[str, Dict[str, List[float]]] = {cat: {lbl: [] for lbl, _, _ in STAGE_TRANSITIONS} for cat in categories}
    
    # Collect inference times per category
    # cat -> metric_name -> list of ms
    inference_metrics = [
        "detect_ms", "track_pose_ms", "align_ms", "embed_ms",
        "match_ms", "temporal_ms", "liveness_ms", "total_inference_ms"
    ]
    inference_data: Dict[str, Dict[str, List[float]]] = {cat: {m: [] for m in inference_metrics} for cat in categories}

    counts = {cat: 0 for cat in categories}

    for sess in sessions.values():
        cat = sess.face_category
        target_cats = ["Overall", cat]
        counts["Overall"] += 1
        counts[cat] += 1

        # Calculate stage deltas
        for lbl, start_stage, end_stage in STAGE_TRANSITIONS:
            if start_stage in sess.milestones and end_stage in sess.milestones:
                delta = float(sess.milestones[end_stage] - sess.milestones[start_stage])
                if delta >= 0:
                    for c in target_cats:
                        deltas[c][lbl].append(delta)

        # Inference metrics
        for m in inference_metrics:
            vals = sess.inference_timings.get(m, [])
            if vals:
                for c in target_cats:
                    inference_data[c][m].extend(vals)

    # Compute p50 and p95 summary
    summary: Dict[str, Any] = {
        "counts": counts,
        "stages": {},
        "inference": {},
    }

    for cat in categories:
        summary["stages"][cat] = {}
        for lbl, _, _ in STAGE_TRANSITIONS:
            data = deltas[cat][lbl]
            summary["stages"][cat][lbl] = {
                "count": len(data),
                "p50": round(calculate_percentile(data, 50.0), 2),
                "p95": round(calculate_percentile(data, 95.0), 2),
                "mean": round(sum(data) / len(data), 2) if data else 0.0,
            }

        summary["inference"][cat] = {}
        for m in inference_metrics:
            data = inference_data[cat][m]
            summary["inference"][cat][m] = {
                "count": len(data),
                "p50": round(calculate_percentile(data, 50.0), 2),
                "p95": round(calculate_percentile(data, 95.0), 2),
                "mean": round(sum(data) / len(data), 2) if data else 0.0,
                "max": round(max(data), 2) if data else 0.0,
            }

    return summary


def parse_perf_samples_csv(csv_path: str) -> Optional[Dict[str, Any]]:
    """Parses CPU and RSS memory sample CSV generated by perf_sample.py."""
    if not os.path.isfile(csv_path):
        return None

    idle_cpu, idle_rss = [], []
    active_cpu, active_rss = [], []

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    cpu = float(row.get("cpu_percent", 0.0))
                    rss = float(row.get("rss_mb", 0.0))
                    state = (row.get("state") or "idle").lower()
                    if "active" in state:
                        active_cpu.append(cpu)
                        active_rss.append(rss)
                    else:
                        idle_cpu.append(cpu)
                        idle_rss.append(rss)
                except (ValueError, KeyError):
                    continue
    except Exception as ex:
        print(f"Warning: Failed reading {csv_path}: {ex}", file=sys.stderr)
        return None

    def calc_stats(cpu_list: List[float], rss_list: List[float]) -> Dict[str, Any]:
        return {
            "count": len(cpu_list),
            "cpu_avg": round(sum(cpu_list) / len(cpu_list), 2) if cpu_list else 0.0,
            "cpu_p50": round(calculate_percentile(cpu_list, 50.0), 2),
            "cpu_p95": round(calculate_percentile(cpu_list, 95.0), 2),
            "cpu_max": round(max(cpu_list), 2) if cpu_list else 0.0,
            "rss_min": round(min(rss_list), 2) if rss_list else 0.0,
            "rss_avg": round(sum(rss_list) / len(rss_list), 2) if rss_list else 0.0,
            "rss_p50": round(calculate_percentile(rss_list, 50.0), 2),
            "rss_p95": round(calculate_percentile(rss_list, 95.0), 2),
            "rss_peak": round(max(rss_list), 2) if rss_list else 0.0,
        }

    return {
        "idle": calc_stats(idle_cpu, idle_rss),
        "active": calc_stats(active_cpu, active_rss),
        "total_samples": len(idle_cpu) + len(active_cpu),
    }


def print_report_tables(analysis: Dict[str, Any], perf_stats: Optional[Dict[str, Any]] = None):
    """Renders formatted ASCII/Markdown tables for latency and resource usage."""
    counts = analysis["counts"]
    print("\n" + "=" * 90)
    print("                     PEEK PERFORMANCE & LATENCY REPORT")
    print("=" * 90)
    print(f"Total Sessions Analyzed: {counts['Overall']} (0 faces: {counts['0 faces']}, 1 face: {counts['1 face']}, >1 faces: {counts['>1 faces']})")
    print("-" * 90)

    # 1. Stage & End-to-End Latency Table
    print("\n[1] AUTHENTICATION STAGE & END-TO-END LATENCY (p50 / p95 in ms)")
    print(f"{'Stage / Milestone':<46} | {'Overall (p50/p95)':<20} | {'1 Face (p50/p95)':<18} | {'>1 Faces (p50/p95)':<18}")
    print("-" * 110)

    stages = analysis["stages"]
    for lbl in stages["Overall"].keys():
        o = stages["Overall"][lbl]
        f1 = stages["1 face"][lbl]
        fm = stages[">1 faces"][lbl]

        o_str = f"{o['p50']:>6.1f} / {o['p95']:>6.1f} ms" if o["count"] > 0 else "      N/A      "
        f1_str = f"{f1['p50']:>5.1f} / {f1['p95']:>5.1f} ms" if f1["count"] > 0 else "     N/A     "
        fm_str = f"{fm['p50']:>5.1f} / {fm['p95']:>5.1f} ms" if fm["count"] > 0 else "     N/A     "

        print(f"{lbl:<46} | {o_str:<20} | {f1_str:<18} | {fm_str:<18}")

    print("-" * 110)

    # 2. Per-Stage Inference Breakdown
    print("\n[2] PIPELINE INFERENCE BREAKDOWN PER FRAME (ms)")
    print(f"{'Inference Stage':<28} | {'Overall p50':<12} | {'Overall p95':<12} | {'Overall Mean':<12} | {'Max ms':<10}")
    print("-" * 84)

    inf = analysis["inference"]["Overall"]
    for m, data in inf.items():
        clean_name = m.replace("_ms", "").replace("_", " ").title()
        if data["count"] > 0:
            print(f"{clean_name:<28} | {data['p50']:>9.2f} ms | {data['p95']:>9.2f} ms | {data['mean']:>9.2f} ms | {data['max']:>8.2f} ms")
        else:
            print(f"{clean_name:<28} |        N/A   |        N/A   |        N/A   |      N/A  ")
    print("-" * 84)

    # 3. CPU & RAM Utilization Table
    if perf_stats:
        print("\n[3] ENGINE CPU & RAM UTILIZATION (perf_samples.csv)")
        print(f"{'State':<16} | {'Samples':<8} | {'CPU Avg%':<10} | {'CPU p50%':<10} | {'CPU p95%':<10} | {'CPU Max%':<10} | {'RSS Avg MB':<11} | {'Peak RSS MB':<12}")
        print("-" * 105)
        for state_key, label in [("idle", "Idle (Monitoring)"), ("active", "Active Session")]:
            s = perf_stats[state_key]
            if s["count"] > 0:
                print(
                    f"{label:<16} | {s['count']:<8} | {s['cpu_avg']:>8.1f}% | {s['cpu_p50']:>8.1f}% | {s['cpu_p95']:>8.1f}% | {s['cpu_max']:>8.1f}% | {s['rss_avg']:>9.1f} MB | {s['rss_peak']:>9.1f} MB"
                )
            else:
                print(f"{label:<16} | {0:<8} |      N/A   |      N/A   |      N/A   |      N/A   |       N/A   |        N/A  ")
        print("-" * 105)
    print("\n" + "=" * 90 + "\n")


def export_report_csv(analysis: Dict[str, Any], perf_stats: Optional[Dict[str, Any]], output_csv_path: str):
    """Exports full performance analysis metrics to a structured CSV file."""
    out_dir = os.path.dirname(os.path.abspath(output_csv_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["category", "metric_type", "metric_name", "count", "p50", "p95", "mean", "max_or_peak"])

        # Stage deltas
        for cat, stages in analysis["stages"].items():
            for stage_name, s in stages.items():
                writer.writerow([cat, "stage_latency_ms", stage_name, s["count"], s["p50"], s["p95"], s["mean"], ""])

        # Inference metrics
        for cat, inf in analysis["inference"].items():
            for m_name, s in inf.items():
                writer.writerow([cat, "inference_latency_ms", m_name, s["count"], s["p50"], s["p95"], s["mean"], s["max"]])

        # Resource stats
        if perf_stats:
            for state_key in ["idle", "active"]:
                s = perf_stats[state_key]
                writer.writerow([state_key, "resource_cpu_percent", "cpu", s["count"], s["cpu_p50"], s["cpu_p95"], s["cpu_avg"], s["cpu_max"]])
                writer.writerow([state_key, "resource_rss_mb", "rss_mb", s["count"], s["rss_p50"], s["rss_p95"], s["rss_avg"], s["rss_peak"]])

    print(f"Metrics CSV exported successfully to: {output_csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Peek Performance & Latency Report Generator")
    parser.add_argument("--engine-log", default=DEFAULT_ENGINE_LOG, help="Path to engine.log")
    parser.add_argument("--cp-log", default=DEFAULT_CP_LOG, help="Path to credential_provider.log")
    parser.add_argument("--sample-csv", default=DEFAULT_SAMPLE_CSV, help="Path to perf_samples.csv")
    parser.add_argument("--output-csv", default=None, help="Path to save latency & performance CSV")
    parser.add_argument("--sample-now", action="store_true", help="Sample live engine CPU/RAM for duration before reporting")
    parser.add_argument("--duration", type=float, default=10.0, help="Duration in seconds if --sample-now is specified")
    parser.add_argument("--json", action="store_true", help="Output summary in JSON format")

    args = parser.parse_args()

    # Optional live sampling
    if args.sample_now:
        try:
            from scripts.perf_sample import run_sampler
            print(f"Sampling engine performance for {args.duration} seconds...")
            sample_out = args.sample_csv or DEFAULT_SAMPLE_CSV
            run_sampler(output_path=sample_out, interval=1.0, duration=args.duration)
        except Exception as ex:
            print(f"Live sampling warning: {ex}", file=sys.stderr)

    sessions = parse_latency_logs(args.engine_log, args.cp_log)
    analysis = analyze_sessions(sessions)
    perf_stats = parse_perf_samples_csv(args.sample_csv)

    if args.json:
        result = {
            "sessions_count": len(sessions),
            "analysis": analysis,
            "resources": perf_stats,
        }
        print(json.dumps(result, indent=2))
    else:
        print_report_tables(analysis, perf_stats)

    if args.output_csv:
        export_report_csv(analysis, perf_stats, args.output_csv)


if __name__ == "__main__":
    main()
