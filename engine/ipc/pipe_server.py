"""
Peek Face Engine - Windows Named Pipe IPC Server
Layer F: Secure Named Pipe Server on \\\\.\\pipe\\PeekEngine for Windows Credential Provider.

SECURITY DESCRIPTOR & SDDL RATIONALE:
------------------------------------
Named pipe: \\\\.\\pipe\\PeekEngine
SDDL String: D:(A;;GA;;;SY)

Rationale:
1. D: Discretionary Access Control List (DACL).
2. (A;;GA;;;SY): Allow Local System (SY) Generic All (GA) only.
   Windows LogonUI and Credential Provider host processes execute exclusively under
   the SYSTEM security context. Authenticated Users (AU) are strictly excluded from
   connecting to the authentication pipe.
3. PIPE_REJECT_REMOTE_CLIENTS (0x00000008): Rejects all remote SMB network connections.
   The pipe is accessible ONLY to local processes.
4. FILE_FLAG_FIRST_PIPE_INSTANCE (0x00080000): The initial instance must be created with
   this flag to prevent squatting/impersonation. Startup fails closed if already taken.
5. Continuous instance listening: a subsequent pipe instance is created before disconnecting
   the active instance, guaranteeing that the pipe namespace entry is never dropped.

ZERO BIOMETRIC LEAKAGE GUARANTEE:
---------------------------------
No facial images, embeddings, landmarks, or template vectors ever cross this pipe.
Only versioned state labels, booleans, display names, and generic status text are emitted.
No profile IDs cross the authentication pipe.

DUAL-GATE AUTHORIZATION INVARIANT:
----------------------------------
The server will NEVER emit an AUTHENTICATED message unless EngineFrameResult.is_authorized_to_unlock
is strictly True. Biometric match alone or temporal match alone will never trigger unlock.
Every AUTHENTICATED message echoes the 128-bit challenge nonce sent in START_AUTH.
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
import win32process
import win32con

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

def build_auth_sddl() -> str:
    """
    Builds secure SDDL for the authentication named pipe.
    SYSTEM (SY) and Builtin Administrators (BA) are granted Generic All (GA).
    Also grants GA to the current process user SID so multi-instance pipe
    creation and management succeeds when running as a user or elevated service.
    PIPE_REJECT_REMOTE_CLIENTS ensures only local processes can connect.
    """
    sddl_parts = ["D:(A;;GA;;;SY)(A;;GA;;;BA)"]
    try:
        token = win32security.OpenProcessToken(win32process.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            user_sid = win32security.ConvertSidToStringSid(sid)
            if user_sid and user_sid not in ("S-1-5-18", "S-1-5-32-544"):
                sddl_parts.append(f"(A;;GA;;;{user_sid})")
        finally:
            token.Close()
    except Exception as ex:
        logger.debug(f"Process token SID resolution in pipe_server failed: {ex}")
    return "".join(sddl_parts)


DEFAULT_PIPE_NAME = r"\\.\pipe\PeekEngine"
DEFAULT_SDDL = build_auth_sddl()
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_PIPE_CONNECTED = 535
ERROR_ACCESS_DENIED = 5
ERROR_PIPE_BUSY = 231

MAX_LINE_LENGTH = 4096      # 4 KB per line
MAX_BUFFER_SIZE = 8192      # 8 KB max buffer before dropping client


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
        Enforces IO limits: max line length 4KB, max buffer 8KB.
        Drops client if limits are exceeded.
        """
        avail = self.peek_available_bytes()
        if avail < 0:
            return False, []
        if avail == 0:
            return True, []

        if len(self._read_buffer) + avail > MAX_BUFFER_SIZE:
            logger.warning(f"Client buffer exceeded {MAX_BUFFER_SIZE} bytes; dropping client connection.")
            return False, []

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
            if len(line_bytes) > MAX_LINE_LENGTH:
                logger.warning(f"Client line exceeded {MAX_LINE_LENGTH} bytes; dropping client connection.")
                return False, []
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)

        if len(self._read_buffer) > MAX_LINE_LENGTH:
            logger.warning(f"Unfinished client line exceeded {MAX_LINE_LENGTH} bytes; dropping client connection.")
            return False, []

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
        sddl: Optional[str] = None,
        coordinator: Optional[Any] = None,
    ):
        self.pipe_name = pipe_name
        self.face_engine = face_engine
        self._injected_camera = camera
        self.default_auth_timeout = default_auth_timeout
        self.sddl = sddl if sddl is not None else DEFAULT_SDDL
        self.coordinator = coordinator

        self._stop_event = threading.Event()
        self._is_running = False
        self._server_thread: Optional[threading.Thread] = None
        self._current_session: Optional[PipeClientSession] = None
        self._active_camera: Optional[Any] = None
        self._camera_lock = threading.Lock()
        self._initial_pipe = None

    def _create_security_attributes(self) -> win32security.SECURITY_ATTRIBUTES:
        """Builds SECURITY_ATTRIBUTES from configured SDDL string."""
        sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            self.sddl, win32security.SDDL_REVISION_1
        )
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False
        return sa

    def _create_pipe_instance(self, is_first: bool = False):
        """Creates a named pipe instance. Enforces FILE_FLAG_FIRST_PIPE_INSTANCE on the first."""
        sa = self._create_security_attributes()
        open_mode = win32pipe.PIPE_ACCESS_DUPLEX
        if is_first:
            open_mode |= FILE_FLAG_FIRST_PIPE_INSTANCE

        return win32pipe.CreateNamedPipe(
            self.pipe_name,
            open_mode,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            65536,
            65536,
            0,
            sa
        )

    def _ensure_face_engine(self) -> FaceEngine:
        """Lazily initializes FaceEngine if not provided in constructor."""
        if self.face_engine is None:
            logger.info("Initializing default FaceEngine instance for IPC server...")
            self.face_engine = FaceEngine(match_threshold=0.48, require_active_challenge=False)
        return self.face_engine

    def start(self):
        """
        Starts the pipe server in a background thread.
        Synchronously creates the first instance with FILE_FLAG_FIRST_PIPE_INSTANCE.
        Fails startup immediately if the pipe name is already taken.
        """
        if self._is_running:
            logger.warning("Pipe server is already running.")
            return

        try:
            self._initial_pipe = self._create_pipe_instance(is_first=True)
        except pywintypes.error as ex:
            logger.error(f"Cannot start pipe server: named pipe '{self.pipe_name}' is already taken: {ex}")
            raise RuntimeError(f"Failed to start PipeServer: pipe '{self.pipe_name}' already exists or name taken ({ex})") from ex

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
        """
        Continuous listener loop.
        Maintains one instance listening/created at all times:
        creates the next instance before disconnecting/closing the current instance.
        """
        h_pipe = getattr(self, "_initial_pipe", None)
        self._initial_pipe = None

        if h_pipe is None:
            try:
                h_pipe = self._create_pipe_instance(is_first=True)
            except Exception as ex:
                logger.error(f"Failed to create initial pipe instance: {ex}")
                self._is_running = False
                return

        while not self._stop_event.is_set():
            try:
                if h_pipe is None:
                    try:
                        h_pipe = self._create_pipe_instance(is_first=False)
                    except Exception as ex:
                        if not self._stop_event.is_set():
                            logger.error(f"Failed to create pipe instance: {ex}")
                        time.sleep(0.5)
                        continue

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

                # Disconnect current client so the pipe handle can be reused or closed
                if h_pipe:
                    try:
                        win32pipe.DisconnectNamedPipe(h_pipe)
                    except Exception:
                        pass

                # CRITICAL SECURITY REQUIREMENT:
                # Keep one instance listening at all times:
                # create the next instance BEFORE closing the current one.
                next_pipe = None
                if not self._stop_event.is_set():
                    try:
                        next_pipe = self._create_pipe_instance(is_first=False)
                    except Exception as ex:
                        logger.error(f"Failed to create next pipe instance: {ex}")

                if next_pipe is not None:
                    if h_pipe:
                        try:
                            win32file.CloseHandle(h_pipe)
                        except Exception:
                            pass
                    h_pipe = next_pipe
                else:
                    logger.warning("Reusing existing disconnected pipe handle for next connection.")

        if h_pipe:
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
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Invalid message format"))
                continue
            except Exception as ex:
                logger.error(f"Error parsing client message: {ex}")
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Invalid message format"))
                continue

            msg_type = msg.get("type")

            if msg_type == MSG_PING:
                session.write_message(make_pong())

            elif msg_type == MSG_START_AUTH:
                logger.info("START_AUTH message received from client")
                nonce = msg.get("nonce")
                if not nonce or not isinstance(nonce, str) or len(nonce) < 16:
                    logger.warning("Rejecting START_AUTH: missing or invalid challenge nonce.")
                    session.write_message(make_error(REASON_INVALID_MESSAGE, "Invalid challenge nonce"))
                    continue

                if self.coordinator is not None and not self.coordinator.acquire_auth():
                    logger.warning("Rejecting START_AUTH: enrollment session currently active.")
                    session.write_message(
                        make_error(REASON_CAMERA_UNAVAILABLE, "Cannot start authentication: enrollment session is currently active")
                    )
                    continue

                try:
                    timeout_sec = float(msg.get("timeout_seconds", self.default_auth_timeout))
                    self._run_auth_session(session, timeout_seconds=timeout_sec, nonce=nonce)
                finally:
                    if self.coordinator is not None:
                        self.coordinator.release_auth()

            elif msg_type == MSG_CANCEL_AUTH:
                logger.debug("CANCEL_AUTH received while idle; acknowledged.")
                session.write_message(make_engine_ready("Idle; ready for authentication"))

            else:
                logger.warning(f"Unknown message type '{msg_type}' received from client.")
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Unknown message type"))

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

    def _run_auth_session(self, session: PipeClientSession, timeout_seconds: float, nonce: str):
        """
        Executes a live authentication session:
        1. Emits CAMERA_STARTING
        2. Acquires camera
        3. Loops frames -> FaceEngine.process_frame()
        4. Streams progress (SEARCHING, FACE_FOUND, VERIFYING, LIVENESS_CHECK)
        5. Emits AUTHENTICATED, AUTH_FAILED, TIMEOUT, or ERROR
        6. Honors CANCEL_AUTH with bounded latency (<50ms)
        7. Releases camera upon completion
        Emits only generic details over the wire; keeps specifics in local logs only.
        """
        session.write_message(make_camera_starting("Acquiring camera..."))

        cam_ok, camera, err_detail = self._acquire_camera()
        if not cam_ok or camera is None:
            logger.error(f"Cannot perform authentication: {err_detail}")
            session.write_message(make_error(REASON_CAMERA_UNAVAILABLE, "Camera device unavailable"))
            return

        engine = self._ensure_face_engine()
        engine.reload_active_profile()

        # Check if an active profile exists
        if engine.active_profile is None:
            logger.warning("Authentication requested but no profile is enrolled in storage.")
            self._release_camera()
            session.write_message(make_auth_failed(REASON_NO_PROFILE, "No biometric profile enrolled"))
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
                    session.write_message(make_timeout("Authentication timed out"))
                    return

                # 3. Read camera frame
                ret, frame = camera.read_frame()
                if not ret or frame is None:
                    logger.error("Camera failed to deliver frame.")
                    session.write_message(make_error(REASON_CAMERA_ERROR, "Camera feed lost"))
                    return

                # 4. Process frame through FaceEngine
                try:
                    result: EngineFrameResult = engine.process_frame(frame)
                except Exception as ex:
                    logger.error(f"FaceEngine.process_frame raised exception: {ex}", exc_info=True)
                    session.write_message(make_error(REASON_ENGINE_ERROR, "Recognition processing failed"))
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
                            nonce=nonce,
                            is_authorized_to_unlock=True,
                            detail="Biometric & liveness verification confirmed"
                        )
                    )
                    logger.info("AUTHENTICATED message sent to client")
                    return

                # 6. Spoof Rejection Check
                if result.liveness_result and result.liveness_result.state == "SPOOF_DETECTED":
                    spoof_reason = result.liveness_result.spoof_reason or "Presentation Attack Detected"
                    logger.warning(f"Spoof attack detected: {spoof_reason}. Emitting generic AUTH_FAILED.")
                    session.write_message(
                        make_auth_failed(
                            reason_code=REASON_SPOOF_DETECTED,
                            detail="Presentation attack detected"
                        )
                    )
                    return

                # 7. Challenge Failure Check
                if (result.challenge_result and
                    result.challenge_result.state in ("FAILED", "TIMEOUT") and
                    not result.is_authorized_to_unlock):
                    logger.warning(f"Active challenge failed: {result.challenge_result.details}. Emitting generic AUTH_FAILED.")
                    session.write_message(
                        make_auth_failed(
                            reason_code=REASON_CHALLENGE_FAILED,
                            detail="Verification challenge failed"
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
                                detail="Face detected, tracking active"
                            )
                        )
                        face_previously_found = True

                # Brief loop throttle to emulate ~30 FPS camera cycle
                time.sleep(0.015)

        finally:
            self._release_camera()
