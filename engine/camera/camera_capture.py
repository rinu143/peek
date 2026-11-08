"""
Peek Face Engine - Camera Capture
Layer A: Windows camera capture via Media Foundation with graceful degradation,
color camera auto-selection, infrared/grayscale rejection, and recovery backoff.
"""

import os
import json
import time
import cv2
import numpy as np
import logging
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger("Peek.Camera")

DEFAULT_CONFIG_PATH = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "engine.json"
)


def get_config_override_index(config_path: Optional[str] = None) -> Optional[int]:
    """
    Checks %ProgramData%\\Peek\\engine.json for a camera_index override.
    Returns the integer index if specified and valid, else None.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "camera_index" in data:
                val = data["camera_index"]
                if isinstance(val, int) and val >= 0:
                    logger.info(f"Found camera_index override in {path}: {val}")
                    return val
                elif isinstance(val, str) and val.isdigit():
                    idx = int(val)
                    logger.info(f"Found camera_index override in {path}: {idx}")
                    return idx
    except Exception as ex:
        logger.warning(f"Error reading camera configuration from {path}: {ex}")
    return None


def is_color_frame(
    frame: np.ndarray,
    min_channel_diff: float = 3.0,
    min_saturation: float = 8.0
) -> Tuple[bool, float, float]:
    """
    Evaluates whether a frame is from a color camera versus an infrared/grayscale stream.
    Rejects frames where:
      - Mean per-pixel difference between R, G, and B channels is near zero, AND
      - Mean saturation in HSV space is very low.

    Returns:
        (is_color: bool, mean_channel_diff: float, mean_saturation: float)
    """
    if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
        return False, 0.0, 0.0

    # Ensure float conversion before subtraction to prevent uint8 underflow/wrap-around
    b = frame[:, :, 0].astype(np.float32)
    g = frame[:, :, 1].astype(np.float32)
    r = frame[:, :, 2].astype(np.float32)

    diff = (np.abs(r - g) + np.abs(g - b) + np.abs(b - r)) / 3.0
    mean_diff = float(np.mean(diff))

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mean_sat = float(np.mean(hsv[:, :, 1]))

    # Reject if both channel difference and saturation are below threshold
    is_color = bool(mean_diff >= min_channel_diff and mean_sat >= min_saturation)
    return is_color, mean_diff, mean_sat


def probe_camera_index(
    index: int,
    backend: int = cv2.CAP_MSMF,
    num_probe_frames: int = 5,
    min_channel_diff: float = 3.0,
    min_saturation: float = 8.0
) -> Dict[str, Any]:
    """
    Probes camera index using the specified backend (default MSMF).
    Reads a few frames, checks for IR/grayscale characteristics, and releases the camera.
    Returns diagnostic dict with status, metrics, and color classification.
    """
    logger.debug(f"Probing camera index {index} with backend {backend}...")
    try:
        cap = cv2.VideoCapture(index, backend)
    except Exception as ex:
        return {
            "index": index,
            "status": "unavailable",
            "reason": f"VideoCapture exception: {ex}",
            "is_color": False,
        }

    if not cap or not cap.isOpened():
        return {
            "index": index,
            "status": "unavailable",
            "reason": "Camera unavailable or in use by another application",
            "is_color": False,
        }

    last_frame = None
    frames_read = 0
    try:
        for _ in range(num_probe_frames):
            ret, frame = cap.read()
            if ret and frame is not None:
                last_frame = frame
                frames_read += 1
    finally:
        cap.release()

    if frames_read == 0 or last_frame is None:
        return {
            "index": index,
            "status": "read_failed",
            "reason": "Camera opened but failed to deliver frames (busy or disconnected)",
            "is_color": False,
        }

    is_col, mean_diff, mean_sat = is_color_frame(
        last_frame, min_channel_diff=min_channel_diff, min_saturation=min_saturation
    )

    if not is_col:
        return {
            "index": index,
            "status": "rejected_ir",
            "reason": f"Rejected infrared/grayscale stream (mean_diff={mean_diff:.2f}, mean_sat={mean_sat:.2f})",
            "mean_diff": mean_diff,
            "mean_sat": mean_sat,
            "is_color": False,
        }

    return {
        "index": index,
        "status": "color",
        "reason": f"Accepted color stream (mean_diff={mean_diff:.2f}, mean_sat={mean_sat:.2f})",
        "mean_diff": mean_diff,
        "mean_sat": mean_sat,
        "is_color": True,
    }


def find_best_camera_index(
    max_indices: int = 4,
    config_path: Optional[str] = None,
    backend: int = cv2.CAP_MSMF
) -> Tuple[Optional[int], str, List[Dict[str, Any]]]:
    """
    Selects the best camera:
    1. Checks for config override in %ProgramData%\\Peek\\engine.json.
    2. Probes indices 0..max_indices-1 with MSMF, rejecting IR/grayscale streams.
    3. Picks the first color camera.
    4. Logs all probe results.
    5. Returns (selected_index_or_None, reason_str, probe_results_list).
    """
    override = get_config_override_index(config_path)
    if override is not None:
        logger.info(f"Using camera_index {override} from config override.")
        return override, f"Override from engine.json ({override})", [
            {"index": override, "status": "override", "reason": "Config override", "is_color": True}
        ]

    probe_results = []
    chosen_index = None

    for idx in range(max_indices):
        result = probe_camera_index(idx, backend=backend)
        probe_results.append(result)
        logger.info(f"Camera probe index {idx}: status={result['status']} ({result['reason']})")
        if chosen_index is None and result.get("is_color", False):
            chosen_index = idx

    if chosen_index is not None:
        logger.info(f"Selected camera index {chosen_index} (first valid color camera).")
        return chosen_index, "Success", probe_results

    # Report clear reason when camera is busy or missing
    has_ir = any(r["status"] == "rejected_ir" for r in probe_results)
    if has_ir:
        reason = "No color camera found (all detected devices are infrared/grayscale streams)."
    else:
        reason = "No camera devices available (indices 0..3 missing or in use by another application)."

    logger.warning(reason)
    return None, reason, probe_results


class CameraCapture:
    """
    Manages camera acquisition using Windows Media Foundation (CAP_MSMF).
    Provides automatic color camera selection, IR rejection, auto-exposure warm-up,
    and reopen backoff recovery.
    """

    def __init__(
        self,
        camera_index: Optional[int] = None,
        width: int = 640,
        height: int = 480,
        warm_up_frames: int = 5,
        backend: int = cv2.CAP_MSMF,
        config_path: Optional[str] = None
    ):
        self.camera_index = camera_index
        self.target_width = width
        self.target_height = height
        self.warm_up_frames = warm_up_frames
        self.backend = backend
        self.config_path = config_path

        self.cap: Optional[cv2.VideoCapture] = None
        self.is_opened = False
        self.error_message: Optional[str] = None
        self.probe_results: List[Dict[str, Any]] = []

    def start(self) -> bool:
        """
        Attempts to start the camera using MSMF.
        If camera_index is not explicitly specified, automatically probes and selects
        the first color camera (or respects engine.json override).
        Discards the first ~5 frames for auto-exposure warm-up.
        Returns True if successful, False if unavailable.
        """
        if self.is_opened and self.cap is not None and self.cap.isOpened():
            return True

        # Resolve camera index if auto-selecting
        if self.camera_index is None:
            chosen_idx, err, probes = find_best_camera_index(
                config_path=self.config_path, backend=self.backend
            )
            self.probe_results = probes
            if chosen_idx is None:
                self.error_message = err
                self.is_opened = False
                return False
            self.camera_index = chosen_idx

        logger.info(f"Opening camera index {self.camera_index} via Media Foundation (MSMF)...")
        try:
            self.cap = cv2.VideoCapture(self.camera_index, self.backend)
            if not self.cap or not self.cap.isOpened():
                if self.backend == cv2.CAP_MSMF:
                    logger.warning("MSMF backend failed to open camera; falling back to CAP_ANY...")
                    if self.cap:
                        self.cap.release()
                    self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_ANY)

            if not self.cap or not self.cap.isOpened():
                self.error_message = f"Camera {self.camera_index} could not be opened (in use by another application or device disconnected)."
                logger.error(self.error_message)
                self.is_opened = False
                return False

            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)

            # Auto-exposure / sensor stabilization warm-up: discard initial frames
            warmed_frames = 0
            for _ in range(self.warm_up_frames):
                ret, _ = self.cap.read()
                if ret:
                    warmed_frames += 1

            self.is_opened = True
            self.error_message = None
            logger.info(
                f"Camera {self.camera_index} successfully initialized ({warmed_frames}/{self.warm_up_frames} warm-up frames discarded)."
            )
            return True

        except Exception as ex:
            self.error_message = f"Unexpected error opening camera {self.camera_index}: {ex}"
            logger.error(self.error_message)
            self.is_opened = False
            return False

    def read_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Reads a single frame from the camera.
        Returns (success: bool, frame: np.ndarray or None).
        """
        if not self.is_opened or self.cap is None:
            return False, None

        ret, frame = self.cap.read()
        if not ret or frame is None:
            logger.warning("Camera failed to deliver a frame (disconnected or in use).")
            return False, None

        return True, frame

    def reopen(self, tries: int = 3, backoff_base: float = 0.2) -> bool:
        """
        Releases and reopens the camera with exponential backoff.
        Useful for recovering from temporary camera disconnection or sleep/wake events.
        """
        logger.warning(f"Reopening camera index {self.camera_index} with backoff (up to {tries} attempts)...")
        self.stop()

        for attempt in range(1, tries + 1):
            backoff_sec = backoff_base * (2 ** (attempt - 1))
            logger.info(f"Reopen attempt {attempt}/{tries} (waiting {backoff_sec:.2f}s)...")
            time.sleep(backoff_sec)
            try:
                if self.start():
                    logger.info(f"Camera index {self.camera_index} successfully reopened on attempt {attempt}/{tries}.")
                    return True
            except Exception as ex:
                logger.warning(f"Reopen attempt {attempt}/{tries} exception: {ex}")

        self.error_message = f"Camera {self.camera_index} failed to recover after {tries} attempts."
        logger.error(self.error_message)
        return False

    def stop(self):
        """Releases the camera hardware."""
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception as ex:
                logger.debug(f"Error releasing VideoCapture: {ex}")
            finally:
                self.cap = None
        self.is_opened = False
        logger.info(f"Released camera capture (index {self.camera_index}).")
