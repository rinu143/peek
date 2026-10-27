"""
Peek — Guided Face Enrollment Application
Launches the native Windows Setup window (PeekSetup) with Glance / Face-ID style circular aperture
and native WPF controls. No OpenCV GUI windows are used.
"""

import sys
import os
import time
import logging
import ctypes
import shutil
import subprocess
from typing import Optional
from ctypes import wintypes

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.pose.pose_estimator import HeadPose, ORDERED_ENROLLMENT_POSES
from engine.enrollment.enrollment_session import EnrollmentSession, EnrollmentState
from engine.enrollment.enrollment_runner import (
    EnrollmentRunner,
    EnrollmentStepResult,
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


def find_peek_setup_exe() -> Optional[str]:
    """Locate the native PeekSetup.exe Windows application."""
    candidates = [
        os.path.join(PROJECT_ROOT, "app", "PeekSetup", "bin", "Release", "net8.0-windows", "win-x64", "publish", "PeekSetup.exe"),
        os.path.join(PROJECT_ROOT, "scripts", "packaging", "PeekSetup.exe"),
        os.path.join(PROJECT_ROOT, "dist", "PeekSetup.exe"),
        os.path.join(PROJECT_ROOT, "app", "PeekSetup", "bin", "Release", "net8.0-windows", "PeekSetup.exe"),
        os.path.join(PROJECT_ROOT, "app", "PeekSetup", "bin", "Debug", "net8.0-windows", "PeekSetup.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Peek", "PeekSetup.exe"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def run_headless_enrollment(profile_name: str = "Primary User") -> int:
    """Headless enrollment fallback when GUI cannot be displayed (no OpenCV windows)."""
    runner = EnrollmentRunner(display_name=profile_name)
    if not runner.start_camera():
        logger.error("Camera not available for enrollment.")
        return 1

    runner.start(display_name=profile_name)
    try:
        for _composited_frame, state, guidance_text in runner.run():
            if state == EnrollmentState.COMPLETED:
                logger.info("Enrollment completed successfully.")
                if runner.saved_profile is not None:
                    prompt_optional_password_link(runner.saved_profile.profile_id, runner.profile_store)
                return 0
            elif state == EnrollmentState.FAILED:
                logger.error("Enrollment failed: %s", guidance_text)
                return 1
    finally:
        runner.stop()
    return 0


def run_enrollment_app(profile_name: str = "Primary User", headless: bool = False) -> int:
    """
    Main enrollment entrypoint.
    Delegates to the native Windows Setup UI (PeekSetup) with Face-ID circular aperture.
    Never opens an OpenCV or Python GUI window.
    """
    logger.info("Starting Peek Face Enrollment...")

    if not headless:
        setup_exe = find_peek_setup_exe()
        if setup_exe:
            logger.info("Launching native Peek Setup UI (%s)...", setup_exe)
            try:
                proc = subprocess.run([setup_exe], check=False)
                return proc.returncode
            except Exception as ex:
                logger.warning("Failed to start PeekSetup.exe: %s. Trying dotnet...", ex)

        if shutil.which("dotnet"):
            project_dir = os.path.join(PROJECT_ROOT, "app", "PeekSetup")
            if os.path.isdir(project_dir):
                logger.info("Launching Peek Setup via dotnet run (%s)...", project_dir)
                try:
                    proc = subprocess.run(["dotnet", "run", "--project", project_dir], check=False)
                    return proc.returncode
                except Exception as ex:
                    logger.warning("dotnet run failed: %s", ex)

    logger.info("Falling back to headless enrollment...")
    return run_headless_enrollment(profile_name)


if __name__ == "__main__":
    is_headless = "--headless" in sys.argv or "--cli" in sys.argv
    sys.exit(run_enrollment_app(headless=is_headless))
