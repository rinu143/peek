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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.camera.camera_capture import CameraCapture
from engine.detection.scrfd_detector import SCRFDDetector
from engine.alignment.aligner import FaceAligner
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.pose.pose_estimator import HeadPose, ORDERED_ENROLLMENT_POSES
from engine.enrollment.enrollment_session import EnrollmentSession, EnrollmentState
from storage.template_store import SecureProfileStore

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.EnrollmentApp")

# Color palette (BGR format for OpenCV)
COLOR_BG_DARK = (20, 18, 18)
COLOR_CARD = (32, 28, 28)
COLOR_ACCENT = (235, 160, 52)       # Cyan/Blue
COLOR_ACCENT_GLOW = (255, 200, 100)
COLOR_GREEN = (80, 210, 90)        # Success Green
COLOR_RED = (70, 70, 230)          # Alert Red
COLOR_YELLOW = (40, 210, 240)      # Warning Yellow
COLOR_WHITE = (245, 245, 245)
COLOR_GRAY = (140, 140, 140)
COLOR_MUTED = (80, 75, 75)

# Target pose vector offsets for the 9-direction dial (dx, dy) normalized to [-1.0, 1.0]
POSE_DIAL_COORDS = {
    HeadPose.CENTER: (0.0, 0.0),
    HeadPose.LEFT: (-1.0, 0.0),
    HeadPose.RIGHT: (1.0, 0.0),
    HeadPose.UP: (0.0, -1.0),
    HeadPose.DOWN: (0.0, 1.0),
    HeadPose.UPPER_LEFT: (-0.75, -0.75),
    HeadPose.UPPER_RIGHT: (0.75, -0.75),
    HeadPose.LOWER_LEFT: (-0.75, 0.75),
    HeadPose.LOWER_RIGHT: (0.75, 0.75),
}

POSE_FRIENDLY_NAMES = {
    HeadPose.CENTER: "Look Straight",
    HeadPose.LEFT: "Turn Head Left",
    HeadPose.RIGHT: "Turn Head Right",
    HeadPose.UP: "Tilt Head Up",
    HeadPose.DOWN: "Tilt Head Down",
    HeadPose.UPPER_LEFT: "Look Up-Left",
    HeadPose.UPPER_RIGHT: "Look Up-Right",
    HeadPose.LOWER_LEFT: "Look Down-Left",
    HeadPose.LOWER_RIGHT: "Look Down-Right",
}


def draw_hud_header(canvas: np.ndarray, current_step: int, total_steps: int, title: str):
    """Draws top title bar and step counter."""
    h, w = canvas.shape[:2]
    
    cv2.putText(canvas, "Peek — Face Setup", (30, 36), cv2.FONT_HERSHEY_DUPLEX, 0.75, COLOR_WHITE, 2, cv2.LINE_AA)
    cv2.putText(canvas, "Look, and you're in.", (30, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.42, COLOR_ACCENT, 1, cv2.LINE_AA)

    step_text = f"Step {current_step} of {total_steps}"
    cv2.putText(canvas, step_text, (w - 150, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_WHITE, 1, cv2.LINE_AA)

    # Divider line
    cv2.line(canvas, (30, 72), (w - 30, 72), COLOR_MUTED, 1, cv2.LINE_AA)


def draw_progress_pills(canvas: np.ndarray, completed_steps: int, total_steps: int = 9):
    """Draws 9-step progress indicators: ● ● ● ○ ○ ○ ○ ○ ○."""
    h, w = canvas.shape[:2]
    start_x = (w - (total_steps * 24)) // 2
    y = h - 60

    for i in range(total_steps):
        cx = start_x + i * 24
        if i < completed_steps:
            # Completed pill (Green)
            cv2.circle(canvas, (cx, y), 6, COLOR_GREEN, -1, cv2.LINE_AA)
        elif i == completed_steps:
            # Active pill (Cyan with glow)
            cv2.circle(canvas, (cx, y), 7, COLOR_ACCENT, -1, cv2.LINE_AA)
            cv2.circle(canvas, (cx, y), 9, COLOR_ACCENT_GLOW, 1, cv2.LINE_AA)
        else:
            # Upcoming pill (Gray circle)
            cv2.circle(canvas, (cx, y), 5, COLOR_MUTED, 2, cv2.LINE_AA)


def draw_pose_guide_dial(canvas: np.ndarray, target_pose: HeadPose, user_yaw: float, user_pitch: float, progress_pct: float):
    """
    Renders a modern Glance-inspired circular dial with the moving target dot
    and user head position indicator.
    """
    dial_center = (canvas.shape[1] - 110, 170)
    dial_radius = 55

    # Dial outer ring
    cv2.circle(canvas, dial_center, dial_radius, COLOR_CARD, -1, cv2.LINE_AA)
    cv2.circle(canvas, dial_center, dial_radius, COLOR_MUTED, 2, cv2.LINE_AA)
    cv2.circle(canvas, dial_center, 4, COLOR_GRAY, -1, cv2.LINE_AA)

    # 9 Target direction reference dots on dial
    for pose, (dx, dy) in POSE_DIAL_COORDS.items():
        if pose == HeadPose.CENTER:
            continue
        pt_x = int(dial_center[0] + dx * (dial_radius - 12))
        pt_y = int(dial_center[1] + dy * (dial_radius - 12))
        cv2.circle(canvas, (pt_x, pt_y), 2, COLOR_MUTED, -1, cv2.LINE_AA)

    # Target indicator (Moving target circle for user to aim toward)
    tdx, tdy = POSE_DIAL_COORDS.get(target_pose, (0.0, 0.0))
    target_x = int(dial_center[0] + tdx * (dial_radius - 14))
    target_y = int(dial_center[1] + tdy * (dial_radius - 14))
    cv2.circle(canvas, (target_x, target_y), 10, COLOR_ACCENT, 2, cv2.LINE_AA)

    # Progress arc around target marker
    if progress_pct > 0.0:
        angle_end = int(progress_pct * 3.6)
        cv2.ellipse(canvas, (target_x, target_y), (14, 14), -90, 0, angle_end, COLOR_GREEN, 3, cv2.LINE_AA)

    # User current measured pose dot
    clamped_yaw = max(-1.0, min(1.0, user_yaw / 0.30))
    clamped_pitch = max(-1.0, min(1.0, user_pitch / 0.20))
    user_x = int(dial_center[0] + clamped_yaw * (dial_radius - 14))
    user_y = int(dial_center[1] + clamped_pitch * (dial_radius - 14))
    cv2.circle(canvas, (user_x, user_y), 5, COLOR_WHITE, -1, cv2.LINE_AA)

    # Dial label
    cv2.putText(canvas, "Target Pose", (dial_center[0] - 38, dial_center[1] + dial_radius + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, COLOR_GRAY, 1, cv2.LINE_AA)


def run_enrollment_app(profile_name: str = "Primary User"):
    logger.info("Starting Peek Guided Enrollment Application...")

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    models_dir = os.path.join(base_dir, "engine", "models")
    det_path = os.path.join(models_dir, "scrfd_500m_bnkps.onnx")
    emb_path = os.path.join(models_dir, "w600k_mbf.onnx")

    detector = SCRFDDetector(det_path)
    aligner = FaceAligner()
    embedder = ArcFaceEmbedder(emb_path)
    store = SecureProfileStore()

    session = EnrollmentSession(
        detector=detector,
        aligner=aligner,
        embedder=embedder,
        profile_store=store,
        candidates_per_pose=2,
        keep_best_per_pose=1
    )

    camera = CameraCapture(camera_index=0, width=640, height=480)
    window_name = "Peek — Guided Face Enrollment"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    if not camera.start():
        logger.error("Camera not available for enrollment.")
        blank = np.zeros((480, 720, 3), dtype=np.uint8)
        cv2.putText(blank, "Webcam unavailable or in use by another app.", (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_RED, 2)
        cv2.imshow(window_name, blank)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        return

    # Start guided enrollment session
    session.start()
    saved_profile = None

    try:
        while True:
            ret, frame = camera.read_frame()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            # Mirror webcam feed for intuitive gaze direction
            frame = cv2.flip(frame, 1)

            # Create layout canvas: 760 width, 520 height
            canvas = np.zeros((520, 760, 3), dtype=np.uint8)
            canvas[:] = COLOR_BG_DARK

            # Process frame through enrollment session
            progress = session.process_frame(frame, mirrored=True)

            # Extract user pose for the HUD dial
            user_yaw = 0.0
            user_pitch = 0.0
            det = session.detector.detect(frame)
            if det:
                pose_est = session.pose_estimator.estimate(det[0].landmarks)
                user_yaw = -pose_est.yaw  # Mirrored
                user_pitch = pose_est.pitch

                # Draw subtle face tracking brackets on camera preview
                x1, y1, x2, y2 = det[0].bbox.astype(int)
                # Offset coordinates into canvas window (cam preview placed at (30, 85))
                cx1, cy1 = x1 + 30, y1 + 85
                cx2, cy2 = x2 + 30, y2 + 85
                color = COLOR_GREEN if progress.pose_matched and progress.quality_passed else COLOR_YELLOW
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)
                for pt in det[0].landmarks.astype(int):
                    cv2.circle(frame, tuple(pt), 2, (50, 230, 255), -1)

            # Resize & embed live camera preview into canvas (480x360)
            preview = cv2.resize(frame, (480, 360), interpolation=cv2.INTER_LINEAR)
            canvas[85:85 + 360, 30:30 + 480] = preview
            cv2.rectangle(canvas, (30, 85), (30 + 480, 85 + 360), COLOR_MUTED, 1)

            # Draw Header & Steps
            step_num = min(session.current_pose_idx + 1, 9)
            draw_hud_header(canvas, step_num, 9, "Peek Setup")

            # Draw 9-Direction Guidance Dial
            pct_cand = (progress.candidates_collected / float(progress.target_candidates)) * 100.0
            draw_pose_guide_dial(canvas, progress.current_pose, user_yaw, user_pitch, pct_cand)

            # Draw Target Pose Banner & Guidance
            target_name = POSE_FRIENDLY_NAMES.get(progress.current_pose, progress.current_pose.value)
            cv2.putText(canvas, f"Target: {target_name}", (530, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.58, COLOR_WHITE, 2, cv2.LINE_AA)

            # Guidance message with status color
            msg_color = COLOR_GREEN if progress.pose_matched and progress.quality_passed else COLOR_YELLOW
            if not progress.quality_passed:
                msg_color = COLOR_RED
            cv2.putText(canvas, progress.guidance_message, (530, 312), cv2.FONT_HERSHEY_SIMPLEX, 0.44, msg_color, 1, cv2.LINE_AA)

            # Candidate collection progress bar
            cv2.putText(canvas, f"Samples: {progress.candidates_collected}/{progress.target_candidates}",
                        (530, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_GRAY, 1, cv2.LINE_AA)
            cv2.rectangle(canvas, (530, 375), (530 + 190, 383), COLOR_CARD, -1)
            fill_w = int((progress.candidates_collected / float(progress.target_candidates)) * 190)
            if fill_w > 0:
                cv2.rectangle(canvas, (530, 375), (530 + fill_w, 383), COLOR_GREEN, -1)

            # Draw Bottom Progress Pills
            completed_poses = session.current_pose_idx
            if session.is_complete:
                completed_poses = 9
            draw_progress_pills(canvas, completed_poses, 9)

            # Draw Footer Security / Controls
            sec_note = "Security Guarantee: Camera frames discarded immediately | Embeddings encrypted via DPAPI"
            cv2.putText(canvas, sec_note, (30, canvas.shape[0] - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.35, COLOR_GRAY, 1, cv2.LINE_AA)

            # Check if enrollment is complete
            if session.is_complete:
                if saved_profile is None:
                    # Save profile to DPAPI store
                    saved_profile = session.finalize_and_save_profile(display_name=profile_name)
                    logger.info("Enrollment finished and profile '%s' secured via DPAPI.", profile_name)

                # Completion overlay card
                overlay = canvas.copy()
                cv2.rectangle(overlay, (120, 130), (canvas.shape[1] - 120, 390), COLOR_BG_DARK, -1)
                cv2.addWeighted(overlay, 0.90, canvas, 0.10, 0, canvas)
                cv2.rectangle(canvas, (120, 130), (canvas.shape[1] - 120, 390), COLOR_GREEN, 2, cv2.LINE_AA)

                cv2.putText(canvas, "Enrollment Complete!", (220, 190), cv2.FONT_HERSHEY_DUPLEX, 0.95, COLOR_GREEN, 2, cv2.LINE_AA)
                cv2.putText(canvas, f"Profile: {saved_profile.display_name}", (220, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_WHITE, 1, cv2.LINE_AA)
                cv2.putText(canvas, f"Templates Stored: {len(saved_profile.templates)} multi-angle embeddings", (220, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WHITE, 1, cv2.LINE_AA)
                cv2.putText(canvas, "Encrypted via Windows DPAPI (CryptProtectData)", (220, 290), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_ACCENT, 1, cv2.LINE_AA)
                cv2.putText(canvas, "Press [SPACE] to Re-enroll  |  [Q] or [ESC] to Finish", (180, 345), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_WHITE, 1, cv2.LINE_AA)

            cv2.imshow(window_name, canvas)

            # Handle keystrokes
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord('q'), ord('Q')):
                break
            elif key == ord(' '):
                # Restart enrollment
                session.start()
                saved_profile = None

    finally:
        camera.stop()
        cv2.destroyAllWindows()
        logger.info("Peek Enrollment App exited cleanly.")


if __name__ == "__main__":
    run_enrollment_app()
