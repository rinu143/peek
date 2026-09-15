"""
Peek — Desktop Face Recognition Prototype (Phase 3: Recognition Hardening)
Deliverable: Hardened face recognition desktop app with dominant-face tracking,
rolling temporal verification window, and multi-template scoring.
"""

import sys
import os
import cv2
import time
import numpy as np
import logging
from typing import Optional, List, Tuple

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
COLOR_CARD = (38, 32, 32)
COLOR_ACCENT = (235, 160, 52)      # Peek Blue/Cyan (BGR: 52, 160, 235)
COLOR_GREEN = (72, 200, 80)       # Success Green
COLOR_RED = (60, 60, 220)         # Alert Red
COLOR_YELLOW = (40, 210, 240)     # Warning Yellow
COLOR_WHITE = (245, 245, 245)
COLOR_GRAY = (150, 150, 150)
COLOR_MUTED = (90, 85, 85)


def draw_hud_header(frame: np.ndarray, title: str, subtitle: str, fps: float, total_tracks: int):
    """Draws a modern translucent header bar."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 68), COLOR_BG_DARK, -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    # Title & Tagline
    cv2.putText(frame, title, (20, 30), cv2.FONT_HERSHEY_DUPLEX, 0.78, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, subtitle, (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.42, COLOR_ACCENT, 1, cv2.LINE_AA)
    
    # FPS badge & Face counter
    badge_text = f"{fps:.1f} FPS"
    if total_tracks > 1:
        badge_text += f" | {total_tracks} Faces"
    cv2.putText(frame, badge_text, (w - 170, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.52, COLOR_WHITE, 1, cv2.LINE_AA)


def draw_hud_footer(frame: np.ndarray, active_profile: str, status_text: str, temporal_res):
    """Draws status bar, temporal confirmation meter, and controls footer."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 75), (w, h), COLOR_BG_DARK, -1)
    cv2.addWeighted(overlay, 0.82, frame, 0.18, 0, frame)

    # Security constraint badge
    sec_notice = "Gate: Liveness Required (Phase 4) | Frames Discarded Immediately | DPAPI Encrypted"
    cv2.putText(frame, sec_notice, (20, h - 52), cv2.FONT_HERSHEY_SIMPLEX, 0.36, COLOR_GRAY, 1, cv2.LINE_AA)

    # Temporal verification progress meter
    if temporal_res and temporal_res.window_length > 0:
        bar_x = w - 240
        bar_y = h - 54
        bar_w = 120
        cv2.putText(frame, f"Temporal {temporal_res.positive_matches_in_window}/{temporal_res.required_matches}",
                    (bar_x - 105, bar_y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.40, COLOR_WHITE, 1, cv2.LINE_AA)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + 8), COLOR_CARD, -1)
        fill = int((min(temporal_res.positive_matches_in_window, temporal_res.required_matches) / float(temporal_res.required_matches)) * bar_w)
        fill_color = COLOR_GREEN if temporal_res.is_temporally_confirmed else COLOR_YELLOW
        if fill > 0:
            cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill, bar_y + 8), fill_color, -1)

    # Active profile & Controls
    prof_text = f"Profile: {active_profile}"
    cv2.putText(frame, prof_text, (20, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.48, COLOR_WHITE, 1, cv2.LINE_AA)

    ctrls_text = "[E] Quick Enroll  |  [C] Clear  |  [Q] Quit"
    cv2.putText(frame, ctrls_text, (w - 380, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.44, COLOR_ACCENT, 1, cv2.LINE_AA)


def draw_face_overlay(
    frame: np.ndarray,
    detection,
    match_result,
    track_id: Optional[int] = None,
    is_dominant: bool = True,
    is_temporally_confirmed: bool = False,
    pose_label: Optional[str] = None
):
    """Draws bounding bracket, track ID tag, and landmarks."""
    bbox = detection.bbox.astype(int)
    x1, y1, x2, y2 = bbox
    landmarks = detection.landmarks.astype(int)

    # Secondary bystander face
    if not is_dominant:
        cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_MUTED, 1, cv2.LINE_AA)
        tid_str = f"Bystander #{track_id}" if track_id else "Bystander"
        cv2.putText(frame, tid_str, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_GRAY, 1, cv2.LINE_AA)
        return

    # Dominant target face: determine status color & label
    id_tag = f"#{track_id} " if track_id else ""
    pose_tag = f" [{pose_label}]" if pose_label else ""

    if match_result is None:
        color = COLOR_YELLOW
        label = f"{id_tag}DETECTED{pose_tag}"
    elif is_temporally_confirmed:
        color = COLOR_GREEN
        label = f"✓ {id_tag}MATCH {int(match_result.confidence * 100)}%{pose_tag}"
    elif match_result.is_match:
        color = COLOR_YELLOW
        label = f"⚡ {id_tag}VERIFYING...{pose_tag}"
    else:
        color = COLOR_RED
        label = f"✗ {id_tag}NO MATCH {int(match_result.confidence * 100)}%{pose_tag}"

    # Stylized corner bracket bounding box
    length = max(14, int((x2 - x1) * 0.18))
    thickness = 2
    
    # Corners
    cv2.line(frame, (x1, y1), (x1 + length, y1), color, thickness)
    cv2.line(frame, (x1, y1), (x1, y1 + length), color, thickness)
    cv2.line(frame, (x2, y1), (x2 - length, y1), color, thickness)
    cv2.line(frame, (x2, y1), (x2, y1 + length), color, thickness)
    cv2.line(frame, (x1, y2), (x1 + length, y2), color, thickness)
    cv2.line(frame, (x1, y2), (x1, y2 - length), color, thickness)
    cv2.line(frame, (x2, y2), (x2 - length, y2), color, thickness)
    cv2.line(frame, (x2, y2), (x2 - length, y2), color, thickness)

    # 5 landmarks
    for pt in landmarks:
        cv2.circle(frame, tuple(pt), 3, (50, 240, 255), -1, cv2.LINE_AA)

    # Label badge above face
    tag_y = max(24, y1 - 10)
    cv2.putText(frame, label, (x1, tag_y), cv2.FONT_HERSHEY_SIMPLEX, 0.56, color, 2, cv2.LINE_AA)


def run_prototype():
    logger.info("Initializing Peek Face Engine Prototype (Phase 3 Hardened)...")
    
    profile_store = SecureProfileStore()
    engine = FaceEngine(profile_store=profile_store, match_threshold=0.48)
    camera = CameraCapture(camera_index=0, width=640, height=480)

    window_name = "Peek — Face Recognition Prototype (Phase 3 Hardened)"
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
            ret, frame = camera.read_frame()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            # Mirror frame horizontally for natural webcam feel
            frame = cv2.flip(frame, 1)

            # Process frame through Face Engine (detection + tracking + pose + align + embed + temporal)
            result = engine.process_frame(frame)

            # Draw secondary tracks first
            for trk in result.all_tracks:
                if result.detection and trk.track_id != result.track_id:
                    draw_face_overlay(
                        frame,
                        trk,
                        match_result=None,
                        track_id=trk.track_id,
                        is_dominant=False
                    )

            # Draw dominant tracked face
            if result.has_face and result.detection:
                draw_face_overlay(
                    frame,
                    result.detection,
                    result.match_result,
                    track_id=result.track_id,
                    is_dominant=True,
                    is_temporally_confirmed=result.is_temporally_confirmed,
                    pose_label=result.estimated_pose
                )

            # Measure FPS
            fps_counter += 1
            if time.time() - fps_start_time >= 1.0:
                current_fps = fps_counter / (time.time() - fps_start_time)
                fps_counter = 0
                fps_start_time = time.time()

            # Render HUD Header
            draw_hud_header(frame, "Peek Face-Unlock", "Look, and you're in.", current_fps, len(result.all_tracks))
            
            # Active profile name & template count
            if engine.active_profile:
                tmpl_cnt = len(engine.active_profile.templates)
                prof_name = f"{engine.active_profile.display_name} ({tmpl_cnt} templates)"
            else:
                prof_name = "[None Enrolled]"
            
            # Notification banner if recent
            status_text = result.details
            if time.time() - status_notification_time < 3.0:
                status_text = status_notification

            draw_hud_footer(frame, prof_name, status_text, result.temporal_result)

            # Central status banner when searching or no profile
            if not result.has_face:
                cv2.putText(frame, "Looking for you...", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_YELLOW, 2, cv2.LINE_AA)
            elif not engine.active_profile:
                cv2.putText(frame, "Face Detected — Run Guided Enrollment or Press [E]", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.58, COLOR_YELLOW, 2, cv2.LINE_AA)

            cv2.imshow(window_name, frame)

            # Handle keystrokes
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord('q'), ord('Q')):
                logger.info("User requested exit.")
                break
            elif key in (ord('e'), ord('E')):
                logger.info("Quick enrollment requested...")
                success, tmpl, msg = engine.enroll_from_frame(frame, pose_label="CENTER")
                if success and tmpl is not None:
                    profile = PeekProfile(
                        profile_id="user_profile_default",
                        display_name="Primary User",
                        matching_threshold=0.48,
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
