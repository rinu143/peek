"""
Peek Face Engine - Windows Named Pipe IPC Server
Layer F: Secure Named Pipe Server on \\\\.\\pipe\\PeekEngine for Windows Credential Provider.

SECURITY DESCRIPTOR & SDDL RATIONALE:
------------------------------------
Named pipe: \\\\.\\pipe\\PeekEngine
SDDL String: D:(A;;GRGW;;;AU)(A;;GA;;;BA)(A;;GA;;;SY)

Rationale:
1. D: Discretionary Access Control List (DACL).
2. (A;;GRGW;;;AU): Allow Authenticated Users (AU) Generic Read and Generic Write (GRGW).
   This allows interactive desktop users and logon processes running in local user sessions
   to connect and exchange authentication requests/responses.
3. (A;;GA;;;BA): Allow Built-in Administrators (BA) Generic All (GA) for full management control.
4. (A;;GA;;;SY): Allow Local System (SY) Generic All (GA). This is essential because the
   Windows LogonUI and Credential Provider host processes execute under the SYSTEM security context.
5. PIPE_REJECT_REMOTE_CLIENTS (0x00000008): Rejects all remote SMB network connections.
   The pipe is accessible ONLY to processes running on the local machine.

ZERO BIOMETRIC LEAKAGE GUARANTEE:
---------------------------------
No facial images, embeddings, landmarks, or template vectors ever cross this pipe.
Only versioned state labels, booleans, display names, and sanitized status text are emitted.

DUAL-GATE AUTHORIZATION INVARIANT:
----------------------------------
The server will NEVER emit an AUTHENTICATED message unless EngineFrameResult.is_authorized_to_unlock
is strictly True. Biometric match alone or temporal match alone will never trigger unlock.
"""

import os
import sys
import time
import json
import logging
import threading
from typing import Optional, Dict, Any, Tuple, Callable

import pywintypes
import win32pipe
import win32file
import win32security

from engine.ipc.protocol import (
    PROTOCOL_VERSION,
    MSG_START_AUTH,
    MSG_CANCEL_AUTH,
    MSG_PING,
    MSG_ENGINE_READY,
    MSG_CAMERA_STARTING,
    MSG_SEARCHING,
    MSG_FACE_FOUND,
    MSG_VERIFYING,
    MSG_LIVENESS_CHECK,
    MSG_AUTHENTICATED,
    MSG_AUTH_FAILED,
    MSG_TIMEOUT,
    MSG_ERROR,
    MSG_PONG,
    REASON_SPOOF_DETECTED,
    REASON_NO_MATCH,
    REASON_CHALLENGE_FAILED,
    REASON_CANCELLED,
    REASON_NO_PROFILE,
    REASON_TIMEOUT,
    REASON_CAMERA_ERROR,
    REASON_CAMERA_UNAVAILABLE,
    REASON_ENGINE_ERROR,
    REASON_INVALID_MESSAGE,
    REASON_INTERNAL_ERROR,
    ProtocolError,
    serialize_message,
    parse_message,
    make_engine_ready,
    make_camera_starting,
    make_searching,
    make_face_found,
    make_verifying,
    make_liveness_check,
    make_authenticated,
    make_auth_failed,
    make_timeout,
    make_error,
    make_pong,
)
from engine.camera.camera_capture import CameraCapture
from engine.pipeline.face_engine import FaceEngine, EngineFrameResult

logger = logging.getLogger("Peek.PipeServer")

DEFAULT_PIPE_NAME = r"\\.\pipe\PeekEngine"
DEFAULT_SDDL = "D:(A;;GRGW;;;AU)(A;;GA;;;BA)(A;;GA;;;SY)"
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_PIPE_CONNECTED = 535


class PipeClientSession:
    """Manages buffered read/write operations for a single client pipe handle."""

    def __init__(self, handle):
        self.handle = handle
        self._read_buffer = b""

    def write_message(self, msg: Dict[str, Any]) -> bool:
        """Serializes and sends a message to the client."""
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


class PipeServer:
    """
    Windows Named Pipe IPC server coordinating FaceEngine and CameraCapture
    for external Credential Provider clients.
    """

    def __init__(
        self,
        pipe_name: str = DEFAULT_PIPE_NAME,
        face_engine: Optional[FaceEngine] = None,
        camera: Optional[Any] = None,
        default_auth_timeout: float = 12.0,
        sddl: str = DEFAULT_SDDL,
    ):
        self.pipe_name = pipe_name
        self.face_engine = face_engine
        self._injected_camera = camera
        self.default_auth_timeout = default_auth_timeout
        self.sddl = sddl

        self._stop_event = threading.Event()
        self._is_running = False
        self._server_thread: Optional[threading.Thread] = None
        self._current_session: Optional[PipeClientSession] = None
        self._active_camera: Optional[Any] = None
        self._camera_lock = threading.Lock()

    def _create_security_attributes(self) -> win32security.SECURITY_ATTRIBUTES:
        """Builds SECURITY_ATTRIBUTES from configured SDDL string."""
        sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            self.sddl, win32security.SDDL_REVISION_1
        )
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False
        return sa

    def _ensure_face_engine(self) -> FaceEngine:
        """Lazily initializes FaceEngine if not provided in constructor."""
        if self.face_engine is None:
            logger.info("Initializing default FaceEngine instance for IPC server...")
            self.face_engine = FaceEngine(match_threshold=0.48)
        return self.face_engine

    def start(self):
        """Starts the pipe server in a background thread."""
        if self._is_running:
            logger.warning("Pipe server is already running.")
            return

        self._stop_event.clear()
        self._is_running = True
        self._server_thread = threading.Thread(target=self._run_server_loop, daemon=True)
        self._server_thread.start()
        logger.info(f"Peek Named Pipe Server started on {self.pipe_name}")

    def stop(self, timeout: float = 3.0):
        """Stops the pipe server and releases all handles and camera resources."""
        if not self._is_running:
            return

        logger.info("Stopping Peek Named Pipe Server...")
        self._stop_event.set()
        self._release_camera()

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
        logger.info("Peek Named Pipe Server stopped successfully.")

    def serve_forever(self):
        """Blocking server loop for standalone service entrypoints."""
        self._stop_event.clear()
        self._is_running = True
        logger.info(f"Peek Named Pipe Server listening on {self.pipe_name} (blocking)...")
        try:
            self._run_server_loop()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received; stopping server...")
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
                    1,       # Max 1 client instance at a time
                    65536,   # Out buffer size
                    65536,   # In buffer size
                    0,       # Default timeout (50ms)
                    sa
                )

                if self._stop_event.is_set():
                    if h_pipe:
                        win32file.CloseHandle(h_pipe)
                    break

                logger.debug("Waiting for client connection on Named Pipe...")
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

                logger.info("Client connected to Peek Named Pipe.")
                session = PipeClientSession(h_pipe)
                self._current_session = session

                # Serve the connected client session
                self._handle_client(session)

            except Exception as ex:
                if not self._stop_event.is_set():
                    logger.error(f"Unexpected error in pipe server loop: {ex}")
                time.sleep(0.1)

            finally:
                self._current_session = None
                self._release_camera()
                if h_pipe:
                    try:
                        win32pipe.DisconnectNamedPipe(h_pipe)
                    except Exception:
                        pass
                    try:
                        win32file.CloseHandle(h_pipe)
                    except Exception:
                        pass

    def _handle_client(self, session: PipeClientSession):
        """Processes requests from a single connected client session."""
        # 1. Greet client with ENGINE_READY
        session.write_message(make_engine_ready("Peek Face Engine ready for authentication"))

        # 2. Main command processing loop for this connection
        while not self._stop_event.is_set():
            is_connected, line = session.read_blocking_line(timeout_seconds=0.25)
            if not is_connected:
                logger.info("Client disconnected from Named Pipe.")
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

            elif msg_type == MSG_START_AUTH:
                timeout_sec = float(msg.get("timeout_seconds", self.default_auth_timeout))
                self._run_auth_session(session, timeout_seconds=timeout_sec)

            elif msg_type == MSG_CANCEL_AUTH:
                logger.debug("CANCEL_AUTH received while idle; acknowledged.")
                session.write_message(make_engine_ready("Idle; ready for authentication"))

            else:
                logger.warning(f"Unknown message type '{msg_type}' received from client.")
                session.write_message(make_error(REASON_INVALID_MESSAGE, f"Unknown message type '{msg_type}'"))

    def _acquire_camera(self) -> Tuple[bool, Optional[Any], str]:
        """Acquires the camera device safely with error reporting."""
        with self._camera_lock:
            if self._injected_camera is not None:
                self._active_camera = self._injected_camera
                if hasattr(self._active_camera, "start"):
                    ok = self._active_camera.start()
                    if not ok:
                        return False, None, "Failed to start injected camera"
                return True, self._active_camera, ""

            try:
                cam = CameraCapture(camera_index=0, width=640, height=480)
                if not cam.start():
                    err = cam.error_message or "Camera unavailable or in use by another application"
                    return False, None, err
                self._active_camera = cam
                return True, cam, ""
            except Exception as ex:
                logger.error(f"Camera acquisition exception: {ex}")
                return False, None, f"Camera error: {ex}"

    def _release_camera(self):
        """Releases the camera device so other applications can access it."""
        with self._camera_lock:
            if self._active_camera is not None:
                try:
                    if hasattr(self._active_camera, "stop"):
                        self._active_camera.stop()
                except Exception as ex:
                    logger.warning(f"Error stopping camera: {ex}")
                finally:
                    self._active_camera = None

    def _run_auth_session(self, session: PipeClientSession, timeout_seconds: float):
        """
        Executes a live authentication session:
        1. Emits CAMERA_STARTING
        2. Acquires camera
        3. Loops frames -> FaceEngine.process_frame()
        4. Streams progress (SEARCHING, FACE_FOUND, VERIFYING, LIVENESS_CHECK)
        5. Emits AUTHENTICATED, AUTH_FAILED, TIMEOUT, or ERROR
        6. Honors CANCEL_AUTH with bounded latency (<50ms)
        7. Releases camera upon completion
        """
        session.write_message(make_camera_starting("Acquiring camera..."))

        cam_ok, camera, err_detail = self._acquire_camera()
        if not cam_ok or camera is None:
            logger.error(f"Cannot perform authentication: {err_detail}")
            session.write_message(make_error(REASON_CAMERA_UNAVAILABLE, err_detail))
            return

        engine = self._ensure_face_engine()
        engine.reload_active_profile()

        # Check if an active profile exists
        if engine.active_profile is None:
            logger.warning("Authentication requested but no profile is enrolled in storage.")
            self._release_camera()
            session.write_message(make_auth_failed(REASON_NO_PROFILE, "No biometric profile is enrolled."))
            return

        start_time = time.time()
        last_state_label = None
        face_previously_found = False

        try:
            while not self._stop_event.is_set():
                # 1. Bounded-latency cancellation check (<50ms)
                is_alive, pending_lines = session.read_pending_lines()
                if not is_alive:
                    logger.info("Client disconnected during authentication session.")
                    break

                for line in pending_lines:
                    try:
                        msg = parse_message(line)
                        if msg.get("type") == MSG_CANCEL_AUTH:
                            logger.info("CANCEL_AUTH received from client; terminating session.")
                            session.write_message(make_auth_failed(REASON_CANCELLED, "Authentication cancelled by user"))
                            return
                    except Exception:
                        pass

                # 2. Timeout check
                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    logger.info(f"Authentication timed out after {elapsed:.1f}s.")
                    session.write_message(make_timeout("Authentication timed out without face confirmation"))
                    return

                # 3. Read camera frame
                ret, frame = camera.read_frame()
                if not ret or frame is None:
                    logger.error("Camera failed to deliver frame.")
                    session.write_message(make_error(REASON_CAMERA_ERROR, "Camera feed lost or unavailable"))
                    return

                # 4. Process frame through FaceEngine
                try:
                    result: EngineFrameResult = engine.process_frame(frame)
                except Exception as ex:
                    logger.error(f"FaceEngine.process_frame raised exception: {ex}", exc_info=True)
                    session.write_message(make_error(REASON_ENGINE_ERROR, "Internal recognition engine error"))
                    return

                # 5. Dual-Gate Authorization Check (NON-NEGOTIABLE SECURITY INVARIANT)
                if result.is_authorized_to_unlock:
                    display_name = (
                        engine.active_profile.display_name
                        if engine.active_profile and engine.active_profile.display_name
                        else "Enrolled User"
                    )
                    logger.info(f"Authentication verified for user '{display_name}'. Emitting AUTHENTICATED.")
                    session.write_message(
                        make_authenticated(
                            display_name=display_name,
                            is_authorized_to_unlock=True,
                            detail=result.details or "Biometric & liveness verification confirmed"
                        )
                    )
                    return

                # 6. Spoof Rejection Check
                if result.liveness_result and result.liveness_result.state == "SPOOF_DETECTED":
                    spoof_reason = result.liveness_result.spoof_reason or "Presentation Attack Detected"
                    logger.warning(f"Spoof attack detected: {spoof_reason}. Emitting AUTH_FAILED.")
                    session.write_message(
                        make_auth_failed(
                            reason_code=REASON_SPOOF_DETECTED,
                            detail=f"Spoof detected: {spoof_reason}"
                        )
                    )
                    return

                # 7. Challenge Failure Check
                if (result.challenge_result and
                    result.challenge_result.state in ("FAILED", "TIMEOUT") and
                    not result.is_authorized_to_unlock):
                    logger.warning(f"Active challenge failed: {result.challenge_result.details}. Emitting AUTH_FAILED.")
                    session.write_message(
                        make_auth_failed(
                            reason_code=REASON_CHALLENGE_FAILED,
                            detail=result.challenge_result.details or "Active challenge verification failed"
                        )
                    )
                    return

                # 8. Stream progressive intermediate state updates
                if not result.has_face:
                    if last_state_label != "SEARCHING":
                        session.write_message(make_searching("Looking for user face..."))
                        last_state_label = "SEARCHING"
                        face_previously_found = False
                else:
                    if not face_previously_found:
                        session.write_message(
                            make_face_found(
                                track_id=result.track_id,
                                estimated_pose=result.estimated_pose,
                                detail=result.details or "Face detected, tracking active"
                            )
                        )
                        face_previously_found = True

                    if result.state_label == "CHALLENGE" and result.challenge_result:
                        session.write_message(
                            make_liveness_check(
                                track_id=result.track_id,
                                prompt_text=result.challenge_result.prompt_text,
                                detail=f"Challenge active: {result.challenge_result.prompt_text}"
                            )
                        )
                        last_state_label = "CHALLENGE"

                    elif result.state_label == "LIVENESS_CHECK":
                        if last_state_label != "LIVENESS_CHECK":
                            session.write_message(
                                make_liveness_check(
                                    track_id=result.track_id,
                                    prompt_text=None,
                                    detail=result.details or "Evaluating liveness..."
                                )
                            )
                            last_state_label = "LIVENESS_CHECK"

                    elif result.state_label in ("VERIFYING", "MATCH"):
                        prog = None
                        if result.temporal_result:
                            prog = f"{result.temporal_result.positive_matches_in_window}/{result.temporal_result.required_matches}"
                        session.write_message(
                            make_verifying(
                                track_id=result.track_id,
                                progress=prog,
                                detail=result.details or "Verifying biometric match..."
                            )
                        )
                        last_state_label = "VERIFYING"

                # Brief loop throttle to emulate ~30 FPS camera cycle
                time.sleep(0.015)

        finally:
            self._release_camera()
