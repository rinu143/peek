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
COLOR_AMBER = (50, 180, 220)      # Challenge Amber/Orange (BGR: 220, 180, 50)
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


def draw_hud_footer(
    frame: np.ndarray,
    active_profile: str,
    status_text: str,
    temporal_res,
    liveness_res,
    is_authorized: bool = False,
    status_color=None
):
    """Draws status bar, temporal confirmation meter, liveness meter, and controls footer."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 82), (w, h), COLOR_BG_DARK, -1)
    cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)

    # Security constraint badge
    if is_authorized:
        sec_notice = "✓ SECURITY CLEARED: Biometric Match Verified + Passive Liveness Confirmed"
        sec_color = COLOR_GREEN
    elif liveness_res and liveness_res.state == "SPOOF_DETECTED":
        sec_notice = f"⚠ SECURITY ALERT: Spoof Rejected ({liveness_res.spoof_reason}) — Unlock Blocked"
        sec_color = COLOR_RED
    else:
        sec_notice = "Dual Gates: Biometric Temporal Match + Passive Liveness | DPAPI Protected"
        sec_color = COLOR_GRAY
    cv2.putText(frame, sec_notice, (20, h - 58), cv2.FONT_HERSHEY_SIMPLEX, 0.36, sec_color, 1, cv2.LINE_AA)

    # Prominent status text when provided (e.g., challenge prompts)
    if status_text:
        text_color = status_color if status_color else COLOR_ACCENT
        cv2.putText(frame, status_text, (20, h - 38), cv2.FONT_HERSHEY_SIMPLEX, 0.52, text_color, 1, cv2.LINE_AA)

    # Telemetry bars: Temporal Verification & Liveness Meters
    right_anchor = w - 20

    # 1. Liveness Meter
    if liveness_res:
        live_w = 90
        live_x = right_anchor - live_w
        live_y = h - 60
        live_score = liveness_res.fused_score
        
        if liveness_res.state == "LIVE":
            live_label = f"Live: {int(live_score*100)}%"
            live_color = COLOR_GREEN
        elif liveness_res.state == "SPOOF_DETECTED":
            live_label = "SPOOF"
            live_color = COLOR_RED
        else:
            live_label = f"Live: {int(live_score*100)}%"
            live_color = COLOR_YELLOW

        cv2.putText(frame, live_label, (live_x - 70, live_y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.38, COLOR_WHITE, 1, cv2.LINE_AA)
        cv2.rectangle(frame, (live_x, live_y), (live_x + live_w, live_y + 8), COLOR_CARD, -1)
        fill_live = int(np.clip(live_score, 0.0, 1.0) * live_w)
        if fill_live > 0:
            cv2.rectangle(frame, (live_x, live_y), (live_x + fill_live, live_y + 8), live_color, -1)

    # 2. Temporal verification progress meter
    if temporal_res and temporal_res.window_length > 0:
        bar_w = 90
        bar_x = right_anchor - 270
        bar_y = h - 60
        cv2.putText(frame, f"Temporal {temporal_res.positive_matches_in_window}/{temporal_res.required_matches}",
                    (bar_x - 85, bar_y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.38, COLOR_WHITE, 1, cv2.LINE_AA)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + 8), COLOR_CARD, -1)
        fill = int((min(temporal_res.positive_matches_in_window, temporal_res.required_matches) / float(temporal_res.required_matches)) * bar_w)
        fill_color = COLOR_GREEN if temporal_res.is_temporally_confirmed else COLOR_YELLOW
        if fill > 0:
            cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill, bar_y + 8), fill_color, -1)

    # Active profile & Controls
    prof_text = f"Profile: {active_profile}"
    cv2.putText(frame, prof_text, (20, h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.46, COLOR_WHITE, 1, cv2.LINE_AA)

    ctrls_text = "[E] Quick Enroll  |  [C] Clear  |  [Q] Quit"
    cv2.putText(frame, ctrls_text, (w - 380, h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.44, COLOR_ACCENT, 1, cv2.LINE_AA)


def draw_face_overlay(
    frame: np.ndarray,
    detection,
    match_result,
    track_id: Optional[int] = None,
    is_dominant: bool = True,
    is_temporally_confirmed: bool = False,
    liveness_res=None,
    is_authorized: bool = False,
    pose_label: Optional[str] = None,
    challenge_result=None
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

    if is_authorized:
        color = COLOR_GREEN
        label = f"✓ {id_tag}AUTHORIZED TO UNLOCK{pose_tag}"
    elif challenge_result and challenge_result.state == "PENDING":
        color = COLOR_AMBER
        prompt = challenge_result.prompt_text if challenge_result.prompt_text else "CHALLENGE"
        label = f"⚡ {id_tag}CHALLENGE: {prompt}{pose_tag}"
    elif liveness_res and liveness_res.state == "SPOOF_DETECTED":
        color = COLOR_RED
        label = f"⚠ {id_tag}SPOOF DETECTED ({liveness_res.spoof_reason}){pose_tag}"
    elif match_result is None:
        color = COLOR_YELLOW
        label = f"{id_tag}DETECTED{pose_tag}"
    elif is_temporally_confirmed:
        if liveness_res and liveness_res.is_live:
            color = COLOR_GREEN
            label = f"✓ {id_tag}MATCH & LIVE {int(match_result.confidence * 100)}%{pose_tag}"
        else:
            color = COLOR_YELLOW
            label = f"⚡ {id_tag}MATCH CONFIRMED (CHECKING LIVENESS...){pose_tag}"
    elif match_result.is_match:
        color = COLOR_YELLOW
        label = f"⚡ {id_tag}VERIFYING...{pose_tag}"
    else:
        color = COLOR_RED
        label = f"✗ {id_tag}NO MATCH {int(match_result.confidence * 100)}%{pose_tag}"

    # Stylized corner bracket bounding box
    length = max(14, int((x2 - x1) * 0.18))
    thickness = 3 if is_authorized else 2
    
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
    for (lx, ly) in landmarks:
        cv2.circle(frame, (lx, ly), 2, color, -1, cv2.LINE_AA)

    # Label background tag
    label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
    tag_y1 = max(10, y1 - 24)
    tag_y2 = tag_y1 + 18
    tag_x2 = min(frame.shape[1] - 5, x1 + label_size[0] + 12)
    cv2.rectangle(frame, (x1, tag_y1), (tag_x2, tag_y2), COLOR_BG_DARK, -1)
    cv2.rectangle(frame, (x1, tag_y1), (tag_x2, tag_y2), color, 1)
    cv2.putText(frame, label, (x1 + 6, tag_y2 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def run_prototype():
    """Main interactive loop for the Peek Face Recognition desktop prototype."""
    logger.info("Initializing Peek Face Engine Prototype (Phase 4 Liveness Hardened)...")
    
    profile_store = SecureProfileStore()
    engine = FaceEngine(profile_store=profile_store, match_threshold=0.48)
    camera = CameraCapture(camera_index=0, width=640, height=480)

    window_name = "Peek — Face Recognition Prototype (Phase 4 Liveness Hardened)"
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

            # Process frame through Face Engine (detection + tracking + pose + align + embed + temporal + liveness)
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
                    liveness_res=result.liveness_result,
                    is_authorized=result.is_authorized_to_unlock,
                    pose_label=result.estimated_pose,
                    challenge_result=result.challenge_result
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

            # Determine status color for footer (amber for challenge)
            status_color = None
            if result.state_label == "CHALLENGE":
                status_color = COLOR_AMBER

            draw_hud_footer(
                frame,
                prof_name,
                status_text,
                result.temporal_result,
                result.liveness_result,
                result.is_authorized_to_unlock,
                status_color
            )

            # Central status banner when searching or unlocked
            if result.is_authorized_to_unlock:
                # Visual celebration halo
                h, w = frame.shape[:2]
                cv2.rectangle(frame, (0, 0), (w - 1, h - 1), COLOR_GREEN, 4)
                cv2.putText(frame, "✓ UNLOCKED — LOOK, AND YOU'RE IN.", (35, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.72, COLOR_GREEN, 2, cv2.LINE_AA)
            elif result.state_label == "CHALLENGE":
                # Active challenge prompt with pulsing border
                h, w = frame.shape[:2]
                pulse = int(4 + 2 * np.sin(time.time() * 8))  # Pulsing thickness
                cv2.rectangle(frame, (0, 0), (w - 1, h - 1), COLOR_AMBER, pulse)

                # Challenge prompt text
                prompt_text = result.details if result.details else "Action Required"
                cv2.putText(frame, prompt_text, (20, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.68, COLOR_AMBER, 2, cv2.LINE_AA)

                # Countdown timer if available
                if result.challenge_result and result.challenge_result.time_remaining > 0:
                    time_left = result.challenge_result.time_remaining
                    cv2.putText(frame, f"Time: {time_left:.1f}s", (20, 125), cv2.FONT_HERSHEY_SIMPLEX, 0.58, COLOR_AMBER, 2, cv2.LINE_AA)
            elif not result.has_face:
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
