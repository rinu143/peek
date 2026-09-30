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


def _verify_windows_password(username: str, password: str) -> bool:
    token = wintypes.HANDLE()
    ok = ctypes.windll.advapi32.LogonUserW(
        username, None, password, 2, 0, ctypes.byref(token))  # interactive/default provider
    if ok and token:
        ctypes.windll.kernel32.CloseHandle(token)
    return bool(ok)


def link_windows_password(profile_id: str, password: str, store: SecureProfileStore | None = None,
                          verify_password=None, entropy: bytes | None = None) -> bool:
    """Verify and DPAPI-wrap a password for this profile; never log password material.
    
    SECURITY RESIDUAL-RISK NOTE:
    Wrapping the password with machine-bound entropy provides defense-in-depth against
    casual or offline decryption, but does NOT prevent malicious code executing in the
    enrolled user's context from discovering the entropy value and decrypting the password.
    """
    if os.name != "nt" or not profile_id or not password:
        return False
    username = _current_windows_username()
    if not (verify_password or _verify_windows_password)(username, password):
        return False
    plaintext = bytearray(struct.pack("<I", len(password.encode("utf-16-le"))))
    plaintext.extend(password.encode("utf-16-le"))
    try:
        from storage.template_store import dpapi_encrypt, get_machine_entropy
        entropy_to_use = entropy if entropy is not None else get_machine_entropy()
        encrypted = dpapi_encrypt(
            bytes(plaintext), description="PeekCredentialSecret", entropy=entropy_to_use
        )
        profiles_dir = (store or SecureProfileStore()).storage_dir
        with open(os.path.join(profiles_dir, f"{profile_id}.secret"), "wb") as secret_file:
            secret_file.write(encrypted)
        return True
    finally:
        for i in range(len(plaintext)):
            plaintext[i] = 0


def migrate_legacy_secret(profile_id: str, store: SecureProfileStore | None = None,
                          entropy: bytes | None = None) -> bool:
    """Migrate a legacy no-entropy .secret file to machine-bound entropy in place.
    
    If decryption with machine entropy succeeds, the secret is already migrated.
    If decryption with machine entropy fails, attempts legacy decryption (entropy=None).
    Upon legacy success, re-encrypts with machine entropy and overwrites the file.
    """
    if os.name != "nt" or not profile_id:
        return False
    profiles_dir = (store or SecureProfileStore()).storage_dir
    secret_path = os.path.join(profiles_dir, f"{profile_id}.secret")
    if not os.path.exists(secret_path):
        return False

    with open(secret_path, "rb") as f:
        ciphertext = f.read()

    from storage.template_store import dpapi_encrypt, dpapi_decrypt, get_machine_entropy
    entropy_to_use = entropy if entropy is not None else get_machine_entropy()

    # If it already decrypts with entropy, no migration required
    try:
        decrypted = dpapi_decrypt(ciphertext, entropy=entropy_to_use)
        del decrypted
        return True
    except Exception:
        pass

    # Attempt legacy decrypt with NULL entropy
    plaintext = None
    try:
        plaintext = bytearray(dpapi_decrypt(ciphertext, entropy=None))
        new_encrypted = dpapi_encrypt(
            bytes(plaintext), description="PeekCredentialSecret", entropy=entropy_to_use
        )
        with open(secret_path, "wb") as f:
            f.write(new_encrypted)
        logger.info("Migrated legacy secret file for profile %s to machine-bound entropy", profile_id)
        return True
    except Exception as ex:
        logger.error("Failed to migrate legacy secret file for profile %s: %s", profile_id, ex)
        return False
    finally:
        if plaintext is not None:
            for i in range(len(plaintext)):
                plaintext[i] = 0


def prompt_optional_password_link(profile_id: str, store: SecureProfileStore) -> None:
    """Offer linking only after enrollment; skipping leaves enrollment unchanged."""
    print("Optional: link your current Windows password to enable Peek unlock at the lock screen.")
    print("The password is verified now and stored only DPAPI-wrapped for this Windows account.")
    while True:
        choice = input("Link password now? [y/N]: ").strip().lower()
        if choice not in ("y", "yes"):
            print("Password linking skipped. You can still use password or PIN to unlock.")
            return
        password = getpass.getpass("Confirm current Windows password: ")
        try:
            if link_windows_password(profile_id, password, store):
                print("Peek unlock password linked successfully.")
                return
            print("That Windows password was not accepted; nothing was saved.")
        finally:
            # CPython strings cannot be reliably wiped; drop this reference immediately.
            password = None
        if input("Retry? [y/N]: ").strip().lower() not in ("y", "yes"):
            return


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
