"""
Peek Face Engine - Camera Capture
Layer A: Windows camera capture via Media Foundation with graceful degradation,
color camera auto-selection, infrared/grayscale rejection, low-light handling,
module-level caching, and recovery backoff.
"""

import os
import json
import time
import cv2
import numpy as np
import logging
import platform
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger("Peek.Camera")

_IS_WINDOWS = platform.system() == "Windows"

DEFAULT_CONFIG_PATH = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "engine.json"
)

DEFAULT_MIN_LUMINANCE: float = 40.0

# Module-level cache for camera selection and device verdicts
_CACHED_CAMERA_INDEX: Optional[int] = None
_CACHED_DEVICE_VERDICTS: Dict[int, Dict[str, Any]] = {}

WRITE_PERMISSIONS_MASK = (
    0x0002       # FILE_WRITE_DATA
    | 0x0004     # FILE_APPEND_DATA
    | 0x0010     # FILE_WRITE_EA
    | 0x0100     # FILE_WRITE_ATTRIBUTES
    | 0x00040000 # WRITE_DAC
    | 0x00080000 # WRITE_OWNER
    | 0x40000000 # GENERIC_WRITE
    | 0x10000000 # GENERIC_ALL
)


def get_cached_camera_index() -> Optional[int]:
    """Returns the currently cached camera index, or None if not cached."""
    global _CACHED_CAMERA_INDEX
    return _CACHED_CAMERA_INDEX


def get_cached_device_verdicts() -> Dict[int, Dict[str, Any]]:
    """Returns a copy of the cached per-device IR/color verdicts."""
    global _CACHED_DEVICE_VERDICTS
    return dict(_CACHED_DEVICE_VERDICTS)


def clear_camera_cache() -> None:
    """Clears the module-level camera cache."""
    global _CACHED_CAMERA_INDEX, _CACHED_DEVICE_VERDICTS
    _CACHED_CAMERA_INDEX = None
    _CACHED_DEVICE_VERDICTS.clear()
    logger.debug("Cleared camera module cache.")


def update_camera_cache(selected_index: Optional[int], verdicts: Optional[List[Dict[str, Any]]] = None) -> None:
    """Updates the module-level camera cache with selected index and verdicts."""
    global _CACHED_CAMERA_INDEX, _CACHED_DEVICE_VERDICTS
    _CACHED_CAMERA_INDEX = selected_index
    if verdicts:
        for v in verdicts:
            idx = v.get("index")
            if idx is not None:
                _CACHED_DEVICE_VERDICTS[idx] = v
    logger.info(
        f"Updated camera cache: selected_index={selected_index}, "
        f"{len(_CACHED_DEVICE_VERDICTS)} device verdicts recorded."
    )


def is_file_writable_by_non_admins(file_path: str) -> bool:
    """
    Checks whether a file's DACL grants write or modification permissions
    to any non-administrative user (e.g. Everyone, Builtin Users, or standard users).
    Returns True if writable by non-admins, False if strictly restricted to SYSTEM / Administrators.
    """
    if not _IS_WINDOWS:
        return False

    try:
        import win32security
        import ntsecuritycon

        sd = win32security.GetFileSecurity(
            file_path,
            win32security.DACL_SECURITY_INFORMATION
        )
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None:
            # NULL DACL grants Full Control to Everyone
            logger.warning(f"File {file_path} has a NULL DACL (unrestricted write access).")
            return True

        for i in range(dacl.GetAceCount()):
            ace = dacl.GetAce(i)
            ace_type = ace[0][0]
            mask = ace[1]
            sid = ace[2]
            sid_str = win32security.ConvertSidToStringSid(sid)

            if ace_type == ntsecuritycon.ACCESS_ALLOWED_ACE_TYPE:
                if mask & WRITE_PERMISSIONS_MASK:
                    # Allowed privileged write access:
                    # S-1-5-18: Local SYSTEM
                    # S-1-5-32-544: Builtin Administrators
                    # S-1-5-80-*: TrustedInstaller / NT SERVICE
                    if sid_str in ("S-1-5-18", "S-1-5-32-544") or sid_str.startswith("S-1-5-80-"):
                        continue
                    logger.warning(
                        f"DACL on {file_path} grants write permission (mask 0x{mask:X}) to non-admin SID {sid_str}"
                    )
                    return True

        return False
    except Exception as ex:
        logger.warning(f"Failed to evaluate DACL on {file_path}: {ex}")
        return True


def get_config_override_index(
    config_path: Optional[str] = None,
    check_dacl: bool = True
) -> Optional[int]:
    """
    Checks %ProgramData%\\Peek\\engine.json for a camera_index override.
    Returns the integer index if specified and valid, else None.
    If check_dacl is True on Windows, verifies the file is NOT writable by non-admin users.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    try:
        if os.path.isfile(path):
            if check_dacl and is_file_writable_by_non_admins(path):
                logger.warning(
                    f"Security violation: Config file {path} is writable by non-admin users. "
                    "Ignoring camera_index override."
                )
                return None

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


class ColorCheckResult(tuple):
    """
    Tuple subclass (verdict, mean_diff, mean_sat) for backward compatibility
    with code unpacking 3 items, with additional .mean_lum attribute access.
    verdict is True (color), False (IR/grayscale), or 'unknown' (low-light / dark frame).
    """
    def __new__(cls, verdict: Any, mean_diff: float, mean_sat: float, mean_lum: float = 0.0):
        obj = super(ColorCheckResult, cls).__new__(cls, (verdict, mean_diff, mean_sat))
        obj.verdict = verdict
        obj.mean_diff = mean_diff
        obj.mean_sat = mean_sat
        obj.mean_lum = mean_lum
        return obj


def is_color_frame(
    frame: np.ndarray,
    min_channel_diff: float = 3.0,
    min_saturation: float = 8.0,
    min_luminance: float = DEFAULT_MIN_LUMINANCE
) -> ColorCheckResult:
    """
    Evaluates whether a frame is from a color camera versus an infrared/grayscale stream.
    Only decides IR-vs-color when the frame is bright enough (mean luminance >= min_luminance).
    Dark frames return verdict="unknown" and never reject a device.
    """
    if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
        return ColorCheckResult(False, 0.0, 0.0, 0.0)

    # Ensure float conversion before subtraction to prevent uint8 underflow/wrap-around
    b = frame[:, :, 0].astype(np.float32)
    g = frame[:, :, 1].astype(np.float32)
    r = frame[:, :, 2].astype(np.float32)

    lum = 0.299 * r + 0.587 * g + 0.114 * b
    mean_lum = float(np.mean(lum))

    diff = (np.abs(r - g) + np.abs(g - b) + np.abs(b - r)) / 3.0
    mean_diff = float(np.mean(diff))

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mean_sat = float(np.mean(hsv[:, :, 1]))

    # If the frame is too dark, return "unknown" so we don't falsely reject low-light or covered lens
    if mean_lum < min_luminance:
        return ColorCheckResult("unknown", mean_diff, mean_sat, mean_lum)

    # Reject if both channel difference and saturation are below threshold
    is_color = bool(mean_diff >= min_channel_diff and mean_sat >= min_saturation)
    return ColorCheckResult(is_color, mean_diff, mean_sat, mean_lum)


def probe_camera_index(
    index: int,
    backend: int = cv2.CAP_MSMF,
    num_probe_frames: int = 5,
    min_channel_diff: float = 3.0,
    min_saturation: float = 8.0,
    min_luminance: float = DEFAULT_MIN_LUMINANCE
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

    check = is_color_frame(
        last_frame,
        min_channel_diff=min_channel_diff,
        min_saturation=min_saturation,
        min_luminance=min_luminance
    )

    if check.verdict == "unknown":
        return {
            "index": index,
            "status": "unknown",
            "reason": (
                f"Low-light frame (mean_lum={check.mean_lum:.2f} < {min_luminance:.1f}); "
                "color determination unknown (device not rejected)"
            ),
            "mean_diff": check.mean_diff,
            "mean_sat": check.mean_sat,
            "mean_lum": check.mean_lum,
            "is_color": "unknown",
        }

    if not check.verdict:
        return {
            "index": index,
            "status": "rejected_ir",
            "reason": (
                f"Rejected infrared/grayscale stream (mean_diff={check.mean_diff:.2f}, "
                f"mean_sat={check.mean_sat:.2f}, mean_lum={check.mean_lum:.2f})"
            ),
            "mean_diff": check.mean_diff,
            "mean_sat": check.mean_sat,
            "mean_lum": check.mean_lum,
            "is_color": False,
        }

    return {
        "index": index,
        "status": "color",
        "reason": (
            f"Accepted color stream (mean_diff={check.mean_diff:.2f}, "
            f"mean_sat={check.mean_sat:.2f}, mean_lum={check.mean_lum:.2f})"
        ),
        "mean_diff": check.mean_diff,
        "mean_sat": check.mean_sat,
        "mean_lum": check.mean_lum,
        "is_color": True,
    }


def find_best_camera_index(
    max_indices: int = 4,
    config_path: Optional[str] = None,
    backend: int = cv2.CAP_MSMF,
    force_refresh: bool = False,
    min_luminance: float = DEFAULT_MIN_LUMINANCE,
    check_dacl: bool = True
) -> Tuple[Optional[int], str, List[Dict[str, Any]]]:
    """
    Selects the best camera:
    1. Checks for config override in %ProgramData%\\Peek\\engine.json.
    2. Checks module-level cache if not force_refresh.
    3. Probes indices 0..max_indices-1 with MSMF, rejecting IR/grayscale streams.
    4. Picks the first color camera.
    5. If all devices are rejected or unknown, falls back to cached index,
       otherwise the first device that opened, and logs the reason.
    6. Returns (selected_index_or_None, reason_str, probe_results_list).
    """
    override = get_config_override_index(config_path, check_dacl=check_dacl)
    if override is not None:
        logger.info(f"Using camera_index {override} from config override.")
        return override, f"Override from engine.json ({override})", [
            {"index": override, "status": "override", "reason": "Config override", "is_color": True}
        ]

    # Check cache if not explicitly refreshing
    if not force_refresh and _CACHED_CAMERA_INDEX is not None:
        logger.info(f"Camera cache hit: using cached camera index {_CACHED_CAMERA_INDEX} (skipping probe).")
        cached_verdicts = list(_CACHED_DEVICE_VERDICTS.values()) or [
            {"index": _CACHED_CAMERA_INDEX, "status": "cached", "reason": "Cache hit", "is_color": True}
        ]
        return _CACHED_CAMERA_INDEX, f"Cache hit ({_CACHED_CAMERA_INDEX})", cached_verdicts

    probe_results = []
    chosen_index = None

    for idx in range(max_indices):
        result = probe_camera_index(idx, backend=backend, min_luminance=min_luminance)
        probe_results.append(result)
        logger.info(f"Camera probe index {idx}: status={result['status']} ({result['reason']})")
        if chosen_index is None and result.get("status") == "color":
            chosen_index = idx

    # Update verdicts cache
    for r in probe_results:
        _CACHED_DEVICE_VERDICTS[r["index"]] = r

    if chosen_index is not None:
        logger.info(f"Selected camera index {chosen_index} (first valid color camera).")
        return chosen_index, "Success", probe_results

    # If every device is rejected or unknown, fall back to cached index,
    # otherwise the first device that opens, and log the reason.
    opened_devices = [r for r in probe_results if r["status"] in ("unknown", "color", "rejected_ir")]
    unknown_devices = [r for r in probe_results if r["status"] == "unknown"]

    if _CACHED_CAMERA_INDEX is not None and any(
        r["index"] == _CACHED_CAMERA_INDEX and r["status"] in ("unknown", "color", "rejected_ir")
        for r in probe_results
    ):
        fallback_idx = _CACHED_CAMERA_INDEX
        reason = (
            f"All devices rejected or unknown; falling back to cached camera index {fallback_idx}."
        )
        logger.info(reason)
        return fallback_idx, reason, probe_results

    if opened_devices:
        first_opened = unknown_devices[0] if unknown_devices else opened_devices[0]
        fallback_idx = first_opened["index"]
        reason = (
            f"All devices rejected or unknown; falling back to first opened camera index {fallback_idx} "
            f"(status: {first_opened['status']})."
        )
        logger.info(reason)
        return fallback_idx, reason, probe_results

    reason = "No camera devices available (indices 0..3 missing or in use by another application)."
    logger.warning(reason)
    return None, reason, probe_results


class CameraCapture:
    """
    Manages camera acquisition using Windows Media Foundation (CAP_MSMF).
    Provides automatic color camera selection, IR rejection, auto-exposure warm-up,
    module-level caching, and reopen backoff recovery.
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

    def _open_and_warmup(self, index: int) -> bool:
        """Attempts to open the given index and discard warmup frames."""
        logger.info(f"Opening camera index {index} via Media Foundation (MSMF)...")
        try:
            self.cap = cv2.VideoCapture(index, self.backend)
            if not self.cap or not self.cap.isOpened():
                if self.backend == cv2.CAP_MSMF:
                    logger.warning("MSMF backend failed to open camera; falling back to CAP_ANY...")
                    if self.cap:
                        self.cap.release()
                    self.cap = cv2.VideoCapture(index, cv2.CAP_ANY)

            if not self.cap or not self.cap.isOpened():
                self.error_message = (
                    f"Camera {index} could not be opened (in use by another application "
                    "or device disconnected)."
                )
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
                f"Camera {index} successfully initialized "
                f"({warmed_frames}/{self.warm_up_frames} warm-up frames discarded)."
            )
            return True

        except Exception as ex:
            self.error_message = f"Unexpected error opening camera {index}: {ex}"
            logger.error(self.error_message)
            self.is_opened = False
            return False

    def start(self) -> bool:
        """
        Attempts to start the camera using MSMF.
        Uses cached camera index when available to avoid probing latency on subsequent unlocks.
        Re-probes once on opening/read failure and refreshes cache.
        """
        if self.is_opened and self.cap is not None and self.cap.isOpened():
            return True

        # Resolve camera index if auto-selecting
        if self.camera_index is None:
            override = get_config_override_index(self.config_path)
            if override is not None:
                self.camera_index = override
                self.probe_results = [
                    {"index": override, "status": "override", "reason": "Config override", "is_color": True}
                ]
            elif _CACHED_CAMERA_INDEX is not None:
                logger.info(f"Camera cache hit: using cached camera index {_CACHED_CAMERA_INDEX} (skipping probe).")
                self.camera_index = _CACHED_CAMERA_INDEX
                self.probe_results = list(_CACHED_DEVICE_VERDICTS.values())
            else:
                chosen_idx, err, probes = find_best_camera_index(
                    config_path=self.config_path, backend=self.backend, force_refresh=True
                )
                self.probe_results = probes
                if chosen_idx is None:
                    self.error_message = err
                    self.is_opened = False
                    return False
                self.camera_index = chosen_idx

        # Attempt opening selected camera_index
        if self._open_and_warmup(self.camera_index):
            update_camera_cache(self.camera_index, self.probe_results)
            return True

        # If opening or reading the cached index failed, re-probe once and refresh cache!
        if _CACHED_CAMERA_INDEX is not None:
            failed_idx = self.camera_index
            logger.warning(
                f"Opening or reading cached camera index {failed_idx} failed; re-probing devices to refresh cache..."
            )
            clear_camera_cache()
            chosen_idx, err, probes = find_best_camera_index(
                config_path=self.config_path, backend=self.backend, force_refresh=True
            )
            self.probe_results = probes
            if chosen_idx is not None and chosen_idx != failed_idx:
                self.camera_index = chosen_idx
                if self._open_and_warmup(self.camera_index):
                    update_camera_cache(self.camera_index, self.probe_results)
                    return True

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
        If reopen on current index fails, re-probes once to refresh the cache.
        """
        logger.warning(f"Reopening camera index {self.camera_index} with backoff (up to {tries} attempts)...")
        self.stop()

        for attempt in range(1, tries + 1):
            backoff_sec = backoff_base * (2 ** (attempt - 1))
            logger.info(f"Reopen attempt {attempt}/{tries} (waiting {backoff_sec:.2f}s)...")
            time.sleep(backoff_sec)
            try:
                if self.start():
                    logger.info(
                        f"Camera index {self.camera_index} successfully reopened on attempt {attempt}/{tries}."
                    )
                    return True
            except Exception as ex:
                logger.warning(f"Reopen attempt {attempt}/{tries} exception: {ex}")

        # If reopen failed on this index, re-probe once to see if another device is available
        logger.warning(
            f"Camera index {self.camera_index} failed to recover; attempting single re-probe to refresh cache..."
        )
        clear_camera_cache()
        chosen_idx, err, probes = find_best_camera_index(
            config_path=self.config_path, backend=self.backend, force_refresh=True
        )
        if chosen_idx is not None and chosen_idx != self.camera_index:
            self.camera_index = chosen_idx
            self.probe_results = probes
            if self.start():
                logger.info(f"Recovered by switching to camera index {chosen_idx}.")
                return True

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
