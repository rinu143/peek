"""
Peek — Guided Face Enrollment Application (Phase 2)
Deliverable: Standalone 9-direction guided enrollment app inspired by Glance.
Enforces pose detection via measured landmarks, strict quality gating, multi-frame candidate selection,
DPAPI encryption at rest, and immediate raw camera frame discard.
"""

import sys
import os
import cv2
import time
import math
import numpy as np
import logging
import ctypes
import getpass
import struct
from ctypes import wintypes

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.camera.camera_capture import CameraCapture
from engine.detection.scrfd_detector import SCRFDDetector
from engine.alignment.aligner import FaceAligner
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.pose.pose_estimator import HeadPose, ORDERED_ENROLLMENT_POSES
from engine.enrollment.enrollment_session import EnrollmentSession, EnrollmentState
from engine.enrollment.enrollment_runner import (
    EnrollmentRunner,
    EnrollmentStepResult,
    COLOR_BG_DARK,
    COLOR_CARD,
    COLOR_ACCENT,
    COLOR_ACCENT_GLOW,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_YELLOW,
    COLOR_WHITE,
    COLOR_GRAY,
    COLOR_MUTED,
    POSE_DIAL_COORDS,
    POSE_FRIENDLY_NAMES,
    draw_hud_header,
    draw_progress_pills,
    draw_pose_guide_dial,
)
from storage.template_store import SecureProfileStore

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.EnrollmentApp")


def _current_windows_username() -> str:
    """Return the interactive account name without relying on environment variables."""
    size = wintypes.DWORD(256)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
        raise ctypes.WinError()
    return buffer.value


def prompt_optional_password_link(profile_id: str, store: SecureProfileStore) -> None:
    """Offer linking pointer after enrollment; password linking is managed by PeekSetup."""
    print("To link your Windows password for instant unlock, run PeekSetup.")


def run_enrollment_app(profile_name: str = "Primary User"):
    logger.info("Starting Peek Guided Enrollment Application...")

    runner = EnrollmentRunner(display_name=profile_name)

    window_name = "Peek — Guided Face Enrollment"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    if not runner.start_camera():
        logger.error("Camera not available for enrollment.")
        blank = np.zeros((480, 720, 3), dtype=np.uint8)
        cv2.putText(blank, "Webcam unavailable or in use by another app.", (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_RED, 2)
        cv2.imshow(window_name, blank)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        return

    # Start guided enrollment runner
    runner.start(display_name=profile_name)
    password_prompted = False

    try:
        for composited_frame, state, guidance_text in runner.run():
            # When complete, prompt for optional password linking once
            if state == EnrollmentState.COMPLETED and not password_prompted:
                password_prompted = True
                if runner.saved_profile is not None:
                    try:
                        prompt_optional_password_link(runner.saved_profile.profile_id, runner.profile_store)
                    except (EOFError, OSError, RuntimeError) as ex:
                        logger.warning("Optional password linking was skipped: %s", ex)

            cv2.imshow(window_name, composited_frame)

            # Handle keystrokes
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord('q'), ord('Q')):
                runner.cancel()
                break
            elif key == ord(' '):
                # Restart enrollment
                runner.restart()
                password_prompted = False

    finally:
        runner.stop()
        cv2.destroyAllWindows()
        logger.info("Peek Enrollment App exited cleanly.")


if __name__ == "__main__":
    run_enrollment_app()
