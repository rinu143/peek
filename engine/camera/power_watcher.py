"""
Peek Face Engine - Power / Sleep State Watcher
Detects Windows suspend / sleep and resume cycles so camera and session
resources can be cleanly invalidated and re-created on the next session.
"""

import time
import threading
import logging
from typing import Optional

logger = logging.getLogger("Peek.PowerWatcher")


class PowerStateWatcher:
    """
    Watches system time progression to detect system suspend/resume (sleep/wake).
    When system sleeps, standard threads stop ticking; upon wake, wall-clock
    time advances significantly compared to the 1-second sleep tick.
    """

    def __init__(self, check_interval: float = 1.0, sleep_threshold: float = 3.5):
        self.check_interval = check_interval
        self.sleep_threshold = sleep_threshold
        self._resumed_from_sleep = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Starts the background sleep/resume monitor thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="PeekPowerWatcher")
        self._thread.start()
        logger.debug("PowerStateWatcher monitor thread started.")

    def stop(self):
        """Stops the background monitor thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self):
        last_wall_time = time.time()
        while not self._stop_event.is_set():
            time.sleep(self.check_interval)
            now = time.time()
            delta = now - last_wall_time
            if delta > self.sleep_threshold:
                logger.warning(
                    f"System sleep/resume detected: wall clock jumped {delta:.2f}s "
                    f"while monitor slept for {self.check_interval:.1f}s."
                )
                with self._lock:
                    self._resumed_from_sleep = True
            last_wall_time = now

    def check_and_clear_resume(self) -> bool:
        """
        Returns True if a system resume from sleep occurred since the last check,
        and atomically resets the flag.
        """
        with self._lock:
            if self._resumed_from_sleep:
                self._resumed_from_sleep = False
                return True
            return False

    def simulate_resume(self):
        """Simulates a sleep/resume event for automated testing."""
        with self._lock:
            self._resumed_from_sleep = True
        logger.info("Simulated sleep/resume event triggered.")


# Global default instance
_global_power_watcher: Optional[PowerStateWatcher] = None
_global_lock = threading.Lock()


def get_power_watcher() -> PowerStateWatcher:
    """Returns singleton PowerStateWatcher instance, starting it if necessary."""
    global _global_power_watcher
    with _global_lock:
        if _global_power_watcher is None:
            _global_power_watcher = PowerStateWatcher()
            _global_power_watcher.start()
        return _global_power_watcher
