"""
Peek — Desktop Face Recognition Prototype (Phase 1)
Deliverable: Desktop application that reliably recognizes one enrolled user.
Camera -> Detection -> Landmarks -> Alignment -> ArcFace -> Cosine Similarity.
"""

import sys
import os
import cv2
import time
import numpy as np
import logging

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.camera.camera_capture import CameraCapture
from engine.pipeline.face_engine import FaceEngine
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.PrototypeApp")

# Color palette (BGR format for OpenCV)
COLOR_BG_DARK = (24, 20, 20)
COLOR_ACCENT = (235, 160, 52)      # Peek Blue/Cyan (BGR: 52, 160, 235)
COLOR_GREEN = (72, 200, 80)       # Success Green
COLOR_RED = (60, 60, 220)         # Alert Red
COLOR_YELLOW = (40, 210, 240)     # Warning Yellow
COLOR_WHITE = (245, 245, 245)
COLOR_GRAY = (150, 150, 150)


def draw_hud_header(frame: np.ndarray, title: str, subtitle: str, fps: float):
    """Draws a modern translucent header bar."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 65), COLOR_BG_DARK, -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    # Title & Tagline
    cv2.putText(frame, title, (20, 30), cv2.FONT_HERSHEY_DUPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, subtitle, (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_ACCENT, 1, cv2.LINE_AA)
    
    # FPS badge
    fps_text = f"{fps:.1f} FPS"
    cv2.putText(frame, fps_text, (w - 110, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WHITE, 1, cv2.LINE_AA)


def draw_hud_footer(frame: np.ndarray, active_profile: str, status_text: str, is_match: bool):
    """Draws status bar and controls footer."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 70), (w, h), COLOR_BG_DARK, -1)
    cv2.addWeighted(overlay, 0.80, frame, 0.20, 0, frame)

    # Security constraint badge
    sec_notice = "Security Gate: Liveness required before unlock | Frames discarded immediately"
    cv2.putText(frame, sec_notice, (20, h - 48), cv2.FONT_HERSHEY_SIMPLEX, 0.38, COLOR_GRAY, 1, cv2.LINE_AA)

    # Active profile & Controls
    prof_text = f"Profile: {active_profile}"
    cv2.putText(frame, prof_text, (20, h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_WHITE, 1, cv2.LINE_AA)

    ctrls_text = "[E] Enroll Face  |  [C] Clear Profile  |  [Q] Quit"
    cv2.putText(frame, ctrls_text, (w - 410, h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_ACCENT, 1, cv2.LINE_AA)


def draw_face_overlay(frame: np.ndarray, detection, match_result):
    """Draws bounding box with stylized corner brackets and 5-point landmarks."""
    bbox = detection.bbox.astype(int)
    x1, y1, x2, y2 = bbox
    landmarks = detection.landmarks.astype(int)

    # Determine status color
    if match_result is None:
        color = COLOR_YELLOW
        label = "DETECTED"
    elif match_result.is_match:
        color = COLOR_GREEN
        label = f"MATCH {int(match_result.confidence * 100)}%"
    else:
        color = COLOR_RED
        label = f"NO MATCH {int(match_result.confidence * 100)}%"

    # Corner bracket style bounding box
    length = max(12, int((x2 - x1) * 0.15))
    thickness = 2
    
    # Top-left
    cv2.line(frame, (x1, y1), (x1 + length, y1), color, thickness)
    cv2.line(frame, (x1, y1), (x1, y1 + length), color, thickness)
    # Top-right
    cv2.line(frame, (x2, y1), (x2 - length, y1), color, thickness)
    cv2.line(frame, (x2, y1), (x2, y1 + length), color, thickness)
    # Bottom-left
    cv2.line(frame, (x1, y2), (x1 + length, y2), color, thickness)
    cv2.line(frame, (x1, y2), (x1, y2 - length), color, thickness)
    # Bottom-right
    cv2.line(frame, (x2, y2), (x2 - length, y2), color, thickness)
    cv2.line(frame, (x2, y2), (x2, y2 - length), color, thickness)

    # 5 landmarks
    for pt in landmarks:
        cv2.circle(frame, tuple(pt), 3, (50, 240, 255), -1, cv2.LINE_AA)

    # Label tag above face
    tag_y = max(20, y1 - 10)
    cv2.putText(frame, label, (x1, tag_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)


def run_prototype():
    logger.info("Initializing Peek Face Engine Prototype...")
    
    profile_store = SecureProfileStore()
    engine = FaceEngine(profile_store=profile_store, match_threshold=0.50)
    camera = CameraCapture(camera_index=0, width=640, height=480)

    window_name = "Peek — Face-Unlock Prototype (Phase 1)"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    if not camera.start():
        logger.warning("Default camera not available. Running fallback test screen.")
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(blank, "Camera unavailable or in use.", (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_RED, 2)
        cv2.putText(blank, "Press 'Q' to exit.", (60, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_WHITE, 1)
        cv2.imshow(window_name, blank)
        cv2.waitKey(0)
        camera.stop()
        cv2.destroyAllWindows()
        return

    logger.info("Camera started successfully. Entering main interactive loop...")

    fps_counter = 0
    fps_start_time = time.time()
    current_fps = 0.0
    status_notification = ""
    status_notification_time = 0

    try:
        while True:
            t0 = time.time()
            ret, frame = camera.read_frame()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            # Mirror frame horizontally for natural webcam feel
            frame = cv2.flip(frame, 1)

            # Process frame through Face Engine
            result = engine.process_frame(frame)

            # Draw visual feedback
            if result.has_face and result.detection:
                draw_face_overlay(frame, result.detection, result.match_result)

            # Measure FPS
            fps_counter += 1
            if time.time() - fps_start_time >= 1.0:
                current_fps = fps_counter / (time.time() - fps_start_time)
                fps_counter = 0
                fps_start_time = time.time()

            # Render HUD
            draw_hud_header(frame, "Peek Face-Unlock", "Look, and you're in.", current_fps)
            
            # Active profile name
            prof_name = engine.active_profile.display_name if engine.active_profile else "[None Enrolled]"
            
            # Notification banner if recent
            status_text = result.details
            if time.time() - status_notification_time < 3.0:
                status_text = status_notification

            is_match = result.match_result.is_match if result.match_result else False
            draw_hud_footer(frame, prof_name, status_text, is_match)

            # Central status badge when searching or no profile
            if not result.has_face:
                cv2.putText(frame, "Looking for you...", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_YELLOW, 2, cv2.LINE_AA)
            elif not engine.active_profile:
                cv2.putText(frame, "Face Detected — Press [E] to Enroll", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_YELLOW, 2, cv2.LINE_AA)

            cv2.imshow(window_name, frame)

            # Handle keystrokes
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord('q'), ord('Q')):
                logger.info("User requested exit.")
                break
            elif key in (ord('e'), ord('E')):
                logger.info("Enrollment requested...")
                success, tmpl, msg = engine.enroll_from_frame(frame, pose_label="CENTER")
                if success and tmpl is not None:
                    # Create or update profile
                    profile = PeekProfile(
                        profile_id="user_profile_default",
                        display_name="Primary User",
                        matching_threshold=0.50,
                        templates=[tmpl]
                    )
                    profile_store.save_profile(profile)
                    engine.reload_active_profile()
                    status_notification = "✓ User Enrolled Successfully! (DPAPI Protected)"
                    status_notification_time = time.time()
                    logger.info("Profile created and encrypted with DPAPI.")
                else:
                    status_notification = f"Enrollment Failed: {msg}"
                    status_notification_time = time.time()
                    logger.warning(status_notification)
            elif key in (ord('c'), ord('C')):
                if engine.active_profile:
                    profile_store.delete_profile(engine.active_profile.profile_id)
                    engine.reload_active_profile()
                    status_notification = "Profile cleared."
                    status_notification_time = time.time()
                    logger.info("Active profile cleared.")

    finally:
        camera.stop()
        cv2.destroyAllWindows()
        logger.info("Peek Prototype closed cleanly.")


if __name__ == "__main__":
    run_prototype()
