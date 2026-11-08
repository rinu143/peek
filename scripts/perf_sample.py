"""
Peek Performance Sampler
Samples PeekEngine CPU% and RSS every 5 seconds to a CSV, capturing both
idle periods and active authentication/enrollment sessions.

Usage:
    python scripts/perf_sample.py [--output perf_samples.csv] [--interval 5.0] [--duration 60] [--pid <pid>]
"""

import os
import sys
import time
import datetime
import argparse
import logging
import subprocess
from typing import Optional, List, Dict, Any, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import psutil
except ImportError:
    psutil = None

try:
    import win32api
    import win32process
    import win32con
    import pywintypes
except ImportError:
    win32api = None
    win32process = None
    win32con = None
    pywintypes = None

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [Peek.PerfSample]: %(message)s"
)
logger = logging.getLogger("Peek.PerfSample")

DEFAULT_CSV_PATH = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "logs", "perf_samples.csv"
)


class NativeProcessMonitor:
    """Provides cross-platform or native Win32 process metrics (CPU%, RSS MB)."""

    def __init__(self, pid: int):
        self.pid = pid
        self._last_time: Optional[float] = None
        self._last_cpu_time: Optional[float] = None

    def is_running(self) -> bool:
        if psutil is not None:
            try:
                return psutil.pid_exists(self.pid)
            except Exception:
                return False
        if win32api is not None and win32process is not None:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
                code = win32process.GetExitCodeProcess(h)
                win32api.CloseHandle(h)
                return code == 259  # STILL_ACTIVE
            except Exception:
                return False
        return False

    def get_rss_mb(self) -> float:
        if psutil is not None:
            try:
                p = psutil.Process(self.pid)
                return round(p.memory_info().rss / (1024.0 * 1024.0), 2)
            except Exception:
                pass
        if win32api is not None and win32process is not None:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, self.pid)
                info = win32process.GetProcessMemoryInfo(h)
                win32api.CloseHandle(h)
                return round(info.get("WorkingSetSize", 0) / (1024.0 * 1024.0), 2)
            except Exception:
                pass
        return 0.0

    def get_cpu_percent(self) -> float:
        if psutil is not None:
            try:
                p = psutil.Process(self.pid)
                return round(p.cpu_percent(interval=None), 1)
            except Exception:
                pass
        if win32api is not None and win32process is not None:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
                times = win32process.GetProcessTimes(h)
                win32api.CloseHandle(h)
                # KernelTime and UserTime are in 100ns intervals
                cpu_time_sec = (times["KernelTime"] + times["UserTime"]) / 10000000.0
                now = time.time()
                if self._last_time is None or self._last_cpu_time is None:
                    self._last_time = now
                    self._last_cpu_time = cpu_time_sec
                    return 0.0
                dt = now - self._last_time
                dcpu = cpu_time_sec - self._last_cpu_time
                self._last_time = now
                self._last_cpu_time = cpu_time_sec
                if dt > 0:
                    num_cpus = os.cpu_count() or 1
                    pct = (dcpu / (dt * num_cpus)) * 100.0
                    return round(max(0.0, min(100.0, pct)), 1)
            except Exception:
                pass
        return 0.0


def find_engine_process(explicit_pid: Optional[int] = None) -> Optional[NativeProcessMonitor]:
    """Locates the running PeekEngine process by explicit PID or executable/script name."""
    if explicit_pid is not None:
        mon = NativeProcessMonitor(explicit_pid)
        if mon.is_running():
            return mon
        return None

    # Try psutil if present
    if psutil is not None:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "peekengine" in name:
                    return NativeProcessMonitor(proc.pid)
                cmdline = proc.info.get("cmdline") or []
                cmd_str = " ".join(cmdline).lower()
                if "run_engine_service" in cmd_str:
                    return NativeProcessMonitor(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    # Fallback to PowerShell or tasklist
    try:
        cmd = ["powershell", "-NoProfile", "-Command", "Get-Process | Where-Object { $_.ProcessName -match 'PeekEngine|python' } | Select-Object -ExpandProperty Id"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                mon = NativeProcessMonitor(pid)
                if mon.is_running():
                    return mon
    except Exception:
        pass

    return None


def determine_engine_state(monitor: NativeProcessMonitor, log_path: Optional[str] = None) -> str:
    """
    Infers whether the engine is 'idle' or in an 'active_session'.
    Checks CPU utilization and recent log activity.
    """
    try:
        cpu = monitor.get_cpu_percent()
        if cpu > 5.0:
            return "active_session"

        if log_path and os.path.isfile(log_path):
            mtime = os.path.getmtime(log_path)
            if (time.time() - mtime) < 2.0:
                return "active_session"
    except Exception:
        pass
    return "idle"


def run_sampler(
    output_path: str,
    interval: float = 5.0,
    duration: Optional[float] = None,
    explicit_pid: Optional[int] = None,
    log_path: Optional[str] = None,
):
    """Samples engine CPU and RSS memory at regular intervals, saving to CSV."""
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    file_exists = os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    csv_file = open(output_path, "a", encoding="utf-8", buffering=1)

    if not file_exists:
        csv_file.write("timestamp_ms,iso_time,pid,cpu_percent,rss_mb,state\n")
        csv_file.flush()

    logger.info(f"Performance sampler started. Writing samples every {interval:.1f}s to: {output_path}")

    start_time = time.time()
    cached_monitor: Optional[NativeProcessMonitor] = None

    try:
        while True:
            now = time.time()
            if duration is not None and (now - start_time) >= duration:
                logger.info(f"Duration limit of {duration:.1f}s reached. Stopping sampler.")
                break

            # Find or refresh target process
            if cached_monitor is None or not cached_monitor.is_running():
                cached_monitor = find_engine_process(explicit_pid)
                if cached_monitor is not None:
                    cached_monitor.get_cpu_percent()

            now_ms = int(now * 1000)
            iso_str = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()

            if cached_monitor is not None and cached_monitor.is_running():
                try:
                    pid = cached_monitor.pid
                    cpu = cached_monitor.get_cpu_percent()
                    rss_mb = cached_monitor.get_rss_mb()
                    state = determine_engine_state(cached_monitor, log_path)

                    row = f"{now_ms},{iso_str},{pid},{cpu:.1f},{rss_mb:.2f},{state}\n"
                    csv_file.write(row)
                    csv_file.flush()
                    logger.info(f"[SAMPLE] PID={pid} CPU={cpu:.1f}% RSS={rss_mb:.2f}MB State={state}")
                except Exception as ex:
                    logger.warning(f"Engine process read error: {ex}")
                    cached_monitor = None
            else:
                row = f"{now_ms},{iso_str},-1,0.0,0.0,offline\n"
                csv_file.write(row)
                csv_file.flush()
                logger.info("[SAMPLE] PeekEngine offline (waiting for process...)")

            time.sleep(interval)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received; stopping sampler.")
    finally:
        csv_file.close()
        logger.info(f"Sampler finished. Output saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Peek Engine CPU/RSS Performance Sampler")
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_CSV_PATH,
        help=f"Path to output CSV file (default: {DEFAULT_CSV_PATH})"
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=5.0,
        help="Sampling interval in seconds (default: 5.0)"
    )
    parser.add_argument(
        "--duration", "-d",
        type=float,
        default=None,
        help="Sampling duration limit in seconds (default: run indefinitely)"
    )
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="Explicit process ID to monitor"
    )
    args = parser.parse_args()

    run_sampler(
        output_path=args.output,
        interval=args.interval,
        duration=args.duration,
        explicit_pid=args.pid
    )


if __name__ == "__main__":
    main()
