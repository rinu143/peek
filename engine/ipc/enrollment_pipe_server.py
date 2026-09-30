"""
Peek Face Engine - Enrollment Named Pipe Server
Layer F: Secure Named Pipe Server on \\\\.\\pipe\\PeekEnrollment streaming guided enrollment
preview frames, pose states, and guidance to external native UI applications.

SECURITY DESCRIPTOR & SDDL RATIONALE:
------------------------------------
Named pipe: \\\\.\\pipe\\PeekEnrollment
SDDL String: D:(A;;GRGW;;;AU)(A;;GA;;;BA)(A;;GA;;;SY)

Rationale:
1. Matches the auth pipe security posture: Authenticated Users (AU), Admins (BA), SYSTEM (SY).
2. PIPE_REJECT_REMOTE_CLIENTS (0x00000008): Enforces local-only communication; remote SMB connections rejected.
3. This pipe intentionally carries composited preview frames for UI display, but ONLY within the user's
   own session and never across the lock-screen boundary or over the network.
4. Preview frames are transient, in-memory JPEG buffers; they are never written to disk by the IPC server.
5. Biometric templates are encrypted via Windows DPAPI before persistent storage.
6. Mutually exclusive with the authentication pipe via SessionCoordinator to prevent camera hardware contention.
"""

import os
import sys
import time
import base64
import logging
import threading
from typing import Optional, Dict, Any, Tuple

import cv2
import numpy as np
import pywintypes
import win32pipe
import win32file
import win32security

from engine.ipc.enrollment_protocol import (
    PROTOCOL_VERSION,
    MSG_START_ENROLLMENT,
    MSG_CANCEL_ENROLLMENT,
    MSG_PING,
    MSG_ENROLLMENT_READY,
    MSG_PREVIEW_FRAME,
    MSG_POSE_COMPLETE,
    MSG_ENROLLMENT_COMPLETE,
    MSG_ENROLLMENT_FAILED,
    MSG_ERROR,
    MSG_PONG,
    REASON_CANCELLED,
    REASON_CAMERA_ERROR,
    REASON_CAMERA_UNAVAILABLE,
    REASON_SESSION_BUSY,
    REASON_INVALID_MESSAGE,
    REASON_INTERNAL_ERROR,
    ProtocolError,
    serialize_message,
    parse_message,
    make_enrollment_ready,
    make_preview_frame,
    make_pose_complete,
    make_enrollment_complete,
    make_enrollment_failed,
    make_error,
    make_pong,
)
from engine.enrollment.enrollment_runner import EnrollmentRunner, EnrollmentStepResult
from engine.enrollment.enrollment_session import EnrollmentState
from storage.template_store import SecureProfileStore
from engine.ipc.session_coordinator import SessionCoordinator

logger = logging.getLogger("Peek.EnrollmentPipeServer")

DEFAULT_ENROLLMENT_PIPE_NAME = r"\\.\pipe\PeekEnrollment"
DEFAULT_SDDL = "D:(A;;GRGW;;;AU)(A;;GA;;;BA)(A;;GA;;;SY)"
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_PIPE_CONNECTED = 535


class EnrollmentPipeClientSession:
    """Manages buffered read/write operations for a single client pipe handle on PeekEnrollment."""

    def __init__(self, handle):
        self.handle = handle
        self._read_buffer = b""

    def write_message(self, msg: Dict[str, Any]) -> bool:
        """Serializes and sends an enrollment protocol message to the client."""
        try:
            payload = serialize_message(msg)
            win32file.WriteFile(self.handle, payload)
            return True
        except pywintypes.error as ex:
            logger.debug(f"WriteFile failed (client likely disconnected): {ex}")
            return False

    def peek_available_bytes(self) -> int:
        """Returns number of unread bytes waiting in the pipe buffer without blocking."""
        try:
            _, avail, _ = win32pipe.PeekNamedPipe(self.handle, 1)
            return avail
        except pywintypes.error:
            return -1

    def read_pending_lines(self) -> Tuple[bool, list]:
        """
        Reads any available bytes from the pipe and returns (is_alive, [lines]).
        Non-blocking if peek indicates data is ready.
        """
        avail = self.peek_available_bytes()
        if avail < 0:
            return False, []
        if avail == 0:
            return True, []

        try:
            _, chunk = win32file.ReadFile(self.handle, avail)
            self._read_buffer += chunk
        except pywintypes.error as ex:
            if ex.winerror in (ERROR_BROKEN_PIPE, ERROR_NO_DATA):
                return False, []
            logger.warning(f"ReadFile error: {ex}")
            return False, []

        lines = []
        while b"\n" in self._read_buffer:
            line_bytes, self._read_buffer = self._read_buffer.split(b"\n", 1)
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)

        return True, lines

    def read_blocking_line(self, timeout_seconds: float = 1.0) -> Tuple[bool, Optional[str]]:
        """
        Waits up to timeout_seconds for a complete line from the client.
        Returns (is_connected, line_or_None).
        """
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            is_alive, lines = self.read_pending_lines()
            if not is_alive:
                return False, None
            if lines:
                return True, lines[0]
            time.sleep(0.02)
        return True, None


class EnrollmentPipeServer:
    """
    Windows Named Pipe server on \\\\.\\pipe\\PeekEnrollment for streaming guided enrollment
    preview frames, guidance, and state to external UI clients.
    """

    def __init__(
        self,
        pipe_name: str = DEFAULT_ENROLLMENT_PIPE_NAME,
        runner: Optional[EnrollmentRunner] = None,
        coordinator: Optional[SessionCoordinator] = None,
        sddl: str = DEFAULT_SDDL,
        target_fps: float = 15.0,
        camera: Optional[Any] = None,
        profile_store: Optional[SecureProfileStore] = None,
    ):
        self.pipe_name = pipe_name
        self._runner = runner
        self.coordinator = coordinator
        self.sddl = sddl
        self.target_fps = target_fps
        self._injected_camera = camera
        self.profile_store = profile_store or SecureProfileStore()

        self._stop_event = threading.Event()
        self._is_running = False
        self._server_thread: Optional[threading.Thread] = None
        self._current_session: Optional[EnrollmentPipeClientSession] = None
        self._active_runner: Optional[EnrollmentRunner] = None
        self._runner_lock = threading.Lock()

    def _create_security_attributes(self) -> win32security.SECURITY_ATTRIBUTES:
        """Builds SECURITY_ATTRIBUTES from configured SDDL string."""
        sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            self.sddl, win32security.SDDL_REVISION_1
        )
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False
        return sa

    def _ensure_runner(self, display_name: str = "Primary User") -> EnrollmentRunner:
        with self._runner_lock:
            if self._runner is not None:
                self._runner.display_name = display_name
                self._active_runner = self._runner
                return self._runner

            if self._active_runner is None:
                self._active_runner = EnrollmentRunner(
                    display_name=display_name,
                    camera=self._injected_camera,
                    profile_store=self.profile_store,
                )
            else:
                self._active_runner.display_name = display_name

            return self._active_runner

    def start(self):
        """Starts the enrollment pipe server in a background thread."""
        if self._is_running:
            logger.warning("Enrollment pipe server is already running.")
            return

        self._stop_event.clear()
        self._is_running = True
        self._server_thread = threading.Thread(target=self._run_server_loop, daemon=True)
        self._server_thread.start()
        logger.info(f"Peek Enrollment Named Pipe Server started on {self.pipe_name}")

    def stop(self, timeout: float = 3.0):
        """Stops the enrollment pipe server and releases all handles and camera resources."""
        if not self._is_running:
            return

        logger.info("Stopping Peek Enrollment Named Pipe Server...")
        self._stop_event.set()

        # Stop active runner if running
        if self._active_runner:
            try:
                self._active_runner.cancel()
                self._active_runner.stop()
            except Exception:
                pass

        # Unblock ConnectNamedPipe by creating a transient local connection
        try:
            h = win32file.CreateFile(
                self.pipe_name,
                win32file.GENERIC_READ,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
            win32file.CloseHandle(h)
        except Exception:
            pass

        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=timeout)

        self._is_running = False
        logger.info("Peek Enrollment Named Pipe Server stopped successfully.")

    def serve_forever(self):
        """Blocking server loop for standalone service entrypoints."""
        self._stop_event.clear()
        self._is_running = True
        logger.info(f"Peek Enrollment Named Pipe Server listening on {self.pipe_name} (blocking)...")
        try:
            self._run_server_loop()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received; stopping enrollment server...")
        finally:
            self.stop()

    def _run_server_loop(self):
        """Continuous listener loop supporting multiple sequential client connections."""
        sa = self._create_security_attributes()

        while not self._stop_event.is_set():
            h_pipe = None
            try:
                h_pipe = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                    1,        # Max 1 client instance at a time
                    524288,   # Out buffer size (512KB for JPEG base64 frames)
                    524288,   # In buffer size
                    0,        # Default timeout
                    sa
                )

                if self._stop_event.is_set():
                    if h_pipe:
                        win32file.CloseHandle(h_pipe)
                    break

                logger.debug(f"Waiting for client connection on Named Pipe {self.pipe_name}...")
                try:
                    win32pipe.ConnectNamedPipe(h_pipe, None)
                except pywintypes.error as ex:
                    if ex.winerror == ERROR_PIPE_CONNECTED:
                        # Client connected before ConnectNamedPipe was called
                        pass
                    else:
                        raise

                if self._stop_event.is_set():
                    try:
                        win32pipe.DisconnectNamedPipe(h_pipe)
                    except Exception:
                        pass
                    if h_pipe:
                        win32file.CloseHandle(h_pipe)
                    break

                logger.info("Client connected to Peek Enrollment Named Pipe.")
                session = EnrollmentPipeClientSession(h_pipe)
                self._current_session = session

                # Serve the connected client session
                self._handle_client(session)

            except Exception as ex:
                if not self._stop_event.is_set():
                    logger.error(f"Unexpected error in enrollment pipe server loop: {ex}")
                time.sleep(0.1)

            finally:
                self._current_session = None
                if h_pipe:
                    try:
                        win32pipe.DisconnectNamedPipe(h_pipe)
                    except Exception:
                        pass
                    try:
                        win32file.CloseHandle(h_pipe)
                    except Exception:
                        pass

    def _handle_client(self, session: EnrollmentPipeClientSession):
        """Processes requests from a single connected client session."""
        # 1. Greet client with ENROLLMENT_READY
        session.write_message(make_enrollment_ready("Peek Guided Enrollment ready"))

        # 2. Main command processing loop for this connection
        while not self._stop_event.is_set():
            is_connected, line = session.read_blocking_line(timeout_seconds=0.25)
            if not is_connected:
                logger.info("Client disconnected from Enrollment Named Pipe.")
                break

            if line is None:
                continue

            try:
                msg = parse_message(line)
            except ProtocolError as ex:
                logger.warning(f"Malformed protocol message received: {ex}")
                session.write_message(make_error(REASON_INVALID_MESSAGE, str(ex)))
                continue
            except Exception as ex:
                logger.error(f"Error parsing client message: {ex}")
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Invalid JSON payload"))
                continue

            msg_type = msg.get("type")

            if msg_type == MSG_PING:
                session.write_message(make_pong())

            elif msg_type == MSG_START_ENROLLMENT:
                logger.info("START_ENROLLMENT message received from client")
                if self.coordinator is not None and not self.coordinator.acquire_enrollment():
                    logger.warning("Rejecting START_ENROLLMENT: authentication session currently active.")
                    session.write_message(
                        make_error(REASON_SESSION_BUSY, "Cannot start enrollment: authentication session is currently active")
                    )
                    continue

                try:
                    display_name = msg.get("display_name", "Primary User")
                    self._run_enrollment_session(session, display_name=display_name)
                finally:
                    if self.coordinator is not None:
                        self.coordinator.release_enrollment()

            elif msg_type == MSG_CANCEL_ENROLLMENT:
                logger.debug("CANCEL_ENROLLMENT received while idle; acknowledged.")
                session.write_message(make_enrollment_ready("Idle; ready for enrollment"))

            else:
                logger.warning(f"Unknown message type '{msg_type}' received from client.")
                session.write_message(make_error(REASON_INVALID_MESSAGE, f"Unknown message type '{msg_type}'"))

    def _run_enrollment_session(self, session: EnrollmentPipeClientSession, display_name: str):
        """
        Executes a live guided enrollment session:
        1. Initializes and starts EnrollmentRunner
        2. Steps frame by frame
        3. Encodes composited frame as transient base64 JPEG in memory
        4. Emits PREVIEW_FRAME messages with guidance & target pose
        5. Emits POSE_COMPLETE when transitioning poses
        6. Emits ENROLLMENT_COMPLETE when all poses captured and profile saved
        7. Honors CANCEL_ENROLLMENT promptly (<100ms) with zero persisted templates
        8. Enforces target FPS capping (~15fps)
        """
        logger.info(f"Starting enrollment session for display_name='{display_name}'")
        runner = self._ensure_runner(display_name=display_name)

        if hasattr(runner, "start_camera") and not runner.start_camera():
            logger.error("Camera acquisition failed for enrollment.")
            session.write_message(make_error(REASON_CAMERA_UNAVAILABLE, "Camera feed unavailable or failed to initialize"))
            return

        runner.start(display_name=display_name)
        target_interval = 1.0 / max(1.0, self.target_fps)

        try:
            while not self._stop_event.is_set():
                loop_start = time.time()

                # 1. Bounded-latency cancellation check (<50ms)
                is_alive, pending_lines = session.read_pending_lines()
                if not is_alive:
                    logger.info("Client disconnected during enrollment session.")
                    runner.cancel()
                    break

                cancelled = False
                for line in pending_lines:
                    try:
                        c_msg = parse_message(line)
                        if c_msg.get("type") == MSG_CANCEL_ENROLLMENT:
                            logger.info("CANCEL_ENROLLMENT received from client; terminating session.")
                            runner.cancel()
                            session.write_message(
                                make_enrollment_failed(REASON_CANCELLED, "Enrollment cancelled by user")
                            )
                            cancelled = True
                            break
                    except Exception as ex:
                        logger.warning(f"Error reading client line during enrollment: {ex}")

                if cancelled:
                    break

                # 2. Step enrollment runner
                step_result: Optional[EnrollmentStepResult] = runner.step()
                if step_result is None:
                    if runner.is_cancelled:
                        break
                    logger.error("Runner failed to deliver frame or was aborted.")
                    session.write_message(make_error(REASON_CAMERA_ERROR, "Camera feed lost or unavailable"))
                    break

                # 3. Transient in-memory JPEG compression
                # SECURITY REQUIREMENT: Never write preview frames to disk
                success, buffer = cv2.imencode(
                    ".jpg", step_result.composited_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
                )
                if not success:
                    logger.error("Failed to JPEG-encode composited preview frame.")
                    session.write_message(make_error(REASON_INTERNAL_ERROR, "Failed to encode preview frame"))
                    break

                frame_b64 = base64.b64encode(buffer).decode("ascii")

                # 4. Stream PREVIEW_FRAME
                preview_msg = make_preview_frame(
                    frame_jpeg_b64=frame_b64,
                    target_pose=step_result.target_pose,
                    quality_state=step_result.quality_state,
                    progress_pct=step_result.progress_pct,
                    guidance_text=step_result.guidance_text,
                )
                if not session.write_message(preview_msg):
                    logger.info("Failed to send preview frame; client likely disconnected.")
                    runner.cancel()
                    break

                # 5. Pose transition detection
                if step_result.pose_just_completed:
                    pose_msg = make_pose_complete(
                        completed_pose=step_result.completed_pose,
                        pose_index=step_result.completed_pose_idx,
                        total_poses=9,
                        detail=f"Pose {step_result.completed_pose} completed successfully",
                    )
                    session.write_message(pose_msg)

                # 6. Completion check
                if runner.is_complete:
                    if runner.saved_profile is not None and runner.saved_profile.profile_id:
                        complete_msg = make_enrollment_complete(
                            profile_id=runner.saved_profile.profile_id,
                            display_name=runner.saved_profile.display_name,
                            detail="Biometric profile enrolled and encrypted successfully via DPAPI",
                        )
                        session.write_message(complete_msg)
                        logger.info(
                            "ENROLLMENT_COMPLETE emitted for profile '%s' (id=%s)",
                            runner.saved_profile.display_name,
                            runner.saved_profile.profile_id,
                        )
                    else:
                        logger.error("Enrollment reached complete state but profile could not be saved.")
                        session.write_message(
                            make_enrollment_failed(REASON_INTERNAL_ERROR, "Failed to save biometric profile")
                        )
                    break

                # 7. Frame rate throttle (~15fps)
                elapsed = time.time() - loop_start
                sleep_sec = target_interval - elapsed
                if sleep_sec > 0:
                    time.sleep(sleep_sec)

        finally:
            try:
                runner.stop()
            except Exception as ex:
                logger.warning(f"Error stopping runner: {ex}")
            if self._runner is None:
                self._active_runner = None
