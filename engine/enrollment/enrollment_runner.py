"""
Peek Face Engine - Guided Enrollment Runner
Layer D: Reusable enrollment loop orchestrating camera capture, EnrollmentSession,
HUD/pose-dial overlay composition, candidate selection, and DPAPI profile persistence.

This runner separates the core enrollment workflow from specific UI presentations (OpenCV window vs Named Pipe IPC),
yielding composited preview frames and state updates per iteration while accepting external advance/cancel signals.
"""

import os
import sys
import time
import logging
from typing import Optional, Generator, Tuple, Dict, Any, List

import cv2
import numpy as np

from engine.camera.camera_capture import CameraCapture
from engine.detection.scrfd_detector import SCRFDDetector
from engine.alignment.aligner import FaceAligner
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.pose.pose_estimator import HeadPose, ORDERED_ENROLLMENT_POSES
from engine.enrollment.enrollment_session import (
    EnrollmentSession,
    EnrollmentState,
    EnrollmentProgress,
)
from storage.template_store import SecureProfileStore, PeekProfile

logger = logging.getLogger("Peek.EnrollmentRunner")

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
    Renders circular dial with moving target dot and measured user pose indicator.
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


class EnrollmentStepResult:
    """
    Represents the output of one iteration of the enrollment runner.
    Unpacks directly to (composited_frame, state, guidance_text) for tuple compatibility,
    while also exposing detailed attributes.
    """

    def __init__(
        self,
        composited_frame: np.ndarray,
        state: EnrollmentState,
        guidance_text: str,
        target_pose: str = "",
        quality_state: str = "",
        progress_pct: float = 0.0,
        pose_just_completed: bool = False,
        completed_pose: str = "",
        completed_pose_idx: int = -1,
        saved_profile: Optional[PeekProfile] = None,
        progress: Optional[EnrollmentProgress] = None,
        raw_frame: Optional[np.ndarray] = None,
    ):
        self.composited_frame = composited_frame
        self.state = state
        self.guidance_text = guidance_text
        self.target_pose = target_pose
        self.quality_state = quality_state
        self.progress_pct = progress_pct
        self.pose_just_completed = pose_just_completed
        self.completed_pose = completed_pose
        self.completed_pose_idx = completed_pose_idx
        self.saved_profile = saved_profile
        self.progress = progress
        self.raw_frame = raw_frame

    def __iter__(self):
        yield self.composited_frame
        yield self.state
        yield self.guidance_text

    def __len__(self) -> int:
        return 3

    def __getitem__(self, index: int):
        return (self.composited_frame, self.state, self.guidance_text)[index]

    def __repr__(self) -> str:
        return (
            f"EnrollmentStepResult(state={self.state.value if hasattr(self.state, 'value') else self.state}, "
            f"target_pose='{self.target_pose}', progress_pct={self.progress_pct:.1f}%)"
        )


class EnrollmentRunner:
    """
    Orchestrates the 9-pose guided facial enrollment loop.
    Wraps EnrollmentSession, CameraCapture, and HUD drawing into a reusable driver.
    """

    def __init__(
        self,
        display_name: str = "Primary User",
        session: Optional[EnrollmentSession] = None,
        camera: Optional[Any] = None,
        profile_store: Optional[SecureProfileStore] = None,
        candidates_per_pose: int = 2,
        keep_best_per_pose: int = 1,
        camera_index: int = 0,
    ):
        self.display_name = display_name
        self.profile_store = profile_store or SecureProfileStore()
        self.candidates_per_pose = candidates_per_pose
        self.keep_best_per_pose = keep_best_per_pose
        self.camera_index = camera_index

        self._camera = camera
        self._camera_owned = camera is None
        self.session = session or self._create_default_session()

        self._cancelled = False
        self._advance_requested = False
        self.saved_profile: Optional[PeekProfile] = None
        self._last_progress: Optional[EnrollmentProgress] = None
        self._prev_pose_idx = 0

    def _create_default_session(self) -> EnrollmentSession:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        models_dir = os.path.join(base_dir, "engine", "models")
        det_path = os.path.join(models_dir, "scrfd_500m_bnkps.onnx")
        emb_path = os.path.join(models_dir, "w600k_mbf.onnx")

        detector = SCRFDDetector(det_path)
        aligner = FaceAligner()
        embedder = ArcFaceEmbedder(emb_path)

        return EnrollmentSession(
            detector=detector,
            aligner=aligner,
            embedder=embedder,
            profile_store=self.profile_store,
            candidates_per_pose=self.candidates_per_pose,
            keep_best_per_pose=self.keep_best_per_pose,
        )

    def start_camera(self) -> bool:
        """Starts the camera device if not already running."""
        if self._camera is None:
            self._camera = CameraCapture(camera_index=self.camera_index, width=640, height=480)
        if hasattr(self._camera, "start"):
            return bool(self._camera.start())
        return True

    def stop_camera(self):
        """Stops the camera device if owned by this runner."""
        if self._camera is not None and hasattr(self._camera, "stop"):
            try:
                self._camera.stop()
            except Exception as ex:
                logger.warning("Error stopping camera: %s", ex)
        if self._camera_owned:
            self._camera = None

    def start(self, display_name: Optional[str] = None):
        """Starts or restarts the enrollment session."""
        if display_name:
            self.display_name = display_name
        self._cancelled = False
        self._advance_requested = False
        self.saved_profile = None
        self._prev_pose_idx = 0

        self.start_camera()
        self.session.start()
        logger.info("EnrollmentRunner started for profile '%s'", self.display_name)

    def restart(self):
        """Restarts the session from pose 1 without stopping the camera."""
        self.start(self.display_name)

    def advance(self):
        """External signal to manually advance to the next pose."""
        self._advance_requested = True
        logger.info("External advance signal received.")

    def cancel(self):
        """External signal to cancel enrollment immediately without saving."""
        self._cancelled = True
        self.stop_camera()
        logger.info("External cancel signal received; enrollment aborted.")

    def stop(self):
        """Releases all resources and stops camera."""
        self.stop_camera()

    @property
    def is_complete(self) -> bool:
        return self.session.is_complete

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def current_pose(self) -> HeadPose:
        return self.session.current_pose

    @property
    def progress(self) -> Optional[EnrollmentProgress]:
        return self._last_progress

    def step(self) -> Optional[EnrollmentStepResult]:
        """
        Executes one iteration:
        1. Reads camera frame
        2. Steps EnrollmentSession
        3. Composes HUD preview canvas
        4. Detects pose transitions & completion
        5. Finalizes profile upon 9-pose completion
        Returns EnrollmentStepResult or None if no camera frame was available.
        """
        if self._cancelled:
            return None

        # Handle external advance signal
        if self._advance_requested:
            self._advance_requested = False
            if hasattr(self.session, "_finalize_current_pose"):
                self.session._finalize_current_pose()

        if self._camera is None:
            if not self.start_camera():
                return None

        ret, frame = self._camera.read_frame()
        if not ret or frame is None:
            return None

        raw_copy = frame.copy()

        # Mirror webcam feed for intuitive gaze direction
        frame = cv2.flip(frame, 1)

        # Create layout canvas: 760 width, 520 height
        canvas = np.zeros((520, 760, 3), dtype=np.uint8)
        canvas[:] = COLOR_BG_DARK

        prev_idx = self.session.current_pose_idx
        prev_pose = self.session.current_pose

        # Process frame through enrollment session
        progress = self.session.process_frame(frame, mirrored=True)
        self._last_progress = progress

        # Check if pose just completed
        pose_just_completed = (self.session.current_pose_idx > prev_idx)
        completed_pose_str = prev_pose.value if pose_just_completed else ""
        completed_pose_idx = prev_idx if pose_just_completed else -1

        # Extract user pose for the HUD dial
        user_yaw = 0.0
        user_pitch = 0.0
        det = None
        if hasattr(self.session, "detector") and self.session.detector:
            try:
                det = self.session.detector.detect(frame)
            except Exception:
                det = None

        if det:
            if hasattr(self.session, "pose_estimator") and self.session.pose_estimator:
                try:
                    pose_est = self.session.pose_estimator.estimate(det[0].landmarks)
                    user_yaw = -pose_est.yaw  # Mirrored
                    user_pitch = pose_est.pitch
                except Exception:
                    pass

            # Draw subtle face tracking brackets on camera preview
            try:
                x1, y1, x2, y2 = det[0].bbox.astype(int)
                color = COLOR_GREEN if progress.pose_matched and progress.quality_passed else COLOR_YELLOW
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)
                for pt in det[0].landmarks.astype(int):
                    cv2.circle(frame, tuple(pt), 2, (50, 230, 255), -1)
            except Exception:
                pass

        # Resize & embed live camera preview into canvas (480x360 at (30, 85))
        preview = cv2.resize(frame, (480, 360), interpolation=cv2.INTER_LINEAR)
        canvas[85:85 + 360, 30:30 + 480] = preview
        cv2.rectangle(canvas, (30, 85), (30 + 480, 85 + 360), COLOR_MUTED, 1)

        # Draw Header & Steps
        step_num = min(self.session.current_pose_idx + 1, 9)
        draw_hud_header(canvas, step_num, 9, "Peek Setup")

        # Draw 9-Direction Guidance Dial
        cand_count = progress.candidates_collected
        target_cand = max(1, progress.target_candidates)
        pct_cand = (cand_count / float(target_cand)) * 100.0
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
        cv2.putText(canvas, f"Samples: {cand_count}/{target_cand}",
                    (530, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_GRAY, 1, cv2.LINE_AA)
        cv2.rectangle(canvas, (530, 375), (530 + 190, 383), COLOR_CARD, -1)
        fill_w = int((cand_count / float(target_cand)) * 190)
        if fill_w > 0:
            cv2.rectangle(canvas, (530, 375), (530 + fill_w, 383), COLOR_GREEN, -1)

        # Draw Bottom Progress Pills
        completed_poses = self.session.current_pose_idx
        if self.session.is_complete:
            completed_poses = 9
        draw_progress_pills(canvas, completed_poses, 9)

        # Draw Footer Security / Controls
        sec_note = "Security Guarantee: Camera frames discarded immediately | Embeddings encrypted via DPAPI"
        cv2.putText(canvas, sec_note, (30, canvas.shape[0] - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.35, COLOR_GRAY, 1, cv2.LINE_AA)

        # Quality state string
        if progress.pose_matched and progress.quality_passed:
            quality_state = "PASSED"
        elif not progress.quality_passed:
            quality_state = f"QUALITY_FAIL: {progress.guidance_message}"
        else:
            quality_state = f"ALIGNING: {progress.guidance_message}"

        # Check if enrollment is complete
        if self.session.is_complete:
            if self.saved_profile is None and not self._cancelled:
                try:
                    self.saved_profile = self.session.finalize_and_save_profile(display_name=self.display_name)
                    logger.info("Enrollment finished and profile '%s' secured via DPAPI.", self.display_name)
                except Exception as ex:
                    logger.error("Failed to finalize and save profile: %s", ex, exc_info=True)

            if self.saved_profile is not None:
                # Completion overlay card
                overlay = canvas.copy()
                cv2.rectangle(overlay, (120, 130), (canvas.shape[1] - 120, 390), COLOR_BG_DARK, -1)
                cv2.addWeighted(overlay, 0.90, canvas, 0.10, 0, canvas)
                cv2.rectangle(canvas, (120, 130), (canvas.shape[1] - 120, 390), COLOR_GREEN, 2, cv2.LINE_AA)

                cv2.putText(canvas, "Enrollment Complete!", (220, 190), cv2.FONT_HERSHEY_DUPLEX, 0.95, COLOR_GREEN, 2, cv2.LINE_AA)
                cv2.putText(canvas, f"Profile: {self.saved_profile.display_name}", (220, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_WHITE, 1, cv2.LINE_AA)
                cv2.putText(canvas, f"Templates Stored: {len(self.saved_profile.templates)} multi-angle embeddings", (220, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WHITE, 1, cv2.LINE_AA)
                cv2.putText(canvas, "Encrypted via Windows DPAPI (CryptProtectData)", (220, 290), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_ACCENT, 1, cv2.LINE_AA)
                cv2.putText(canvas, "Press [SPACE] to Re-enroll  |  [Q] or [ESC] to Finish", (180, 345), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_WHITE, 1, cv2.LINE_AA)

        return EnrollmentStepResult(
            composited_frame=canvas,
            state=self.session.state,
            guidance_text=progress.guidance_message,
            target_pose=progress.current_pose.value,
            quality_state=quality_state,
            progress_pct=progress.progress_percentage,
            pose_just_completed=pose_just_completed,
            completed_pose=completed_pose_str,
            completed_pose_idx=completed_pose_idx,
            saved_profile=self.saved_profile,
            progress=progress,
            raw_frame=raw_copy,
        )

    def run(self, auto_stop_on_complete: bool = False) -> Generator[EnrollmentStepResult, None, None]:
        """
        Continuously yields (composited_frame, state, guidance_text) step results
        until cancelled or stopped.
        """
        self.start()
        try:
            while not self._cancelled:
                res = self.step()
                if res is None:
                    time.sleep(0.01)
                    continue
                yield res
                if auto_stop_on_complete and self.is_complete:
                    break
        finally:
            self.stop()

    def __iter__(self):
        return self.run()
