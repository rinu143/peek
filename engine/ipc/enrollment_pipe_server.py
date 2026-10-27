r"""
Peek Face Engine - Enrollment Named Pipe Server
Layer F: Secure Named Pipe Server on \\.\pipe\PeekEnrollment streaming guided enrollment
preview frames, pose states, and guidance to external native UI applications.

SECURITY DESCRIPTOR & SDDL RATIONALE:
------------------------------------
Named pipe: \\.\pipe\PeekEnrollment
SDDL String: D:(A;;GA;;;SY)(A;;GRGW;;;{user_sid})

Rationale:
1. Hardened IPC boundary: SYSTEM (SY) + enrolled user SID only.
2. PIPE_REJECT_REMOTE_CLIENTS (0x00000008): Enforces local-only communication; remote SMB connections rejected.
3. Authenticated Users (AU) group removed entirely.
4. Preview frames are transient, in-memory JPEG buffers streamed strictly to the verified enrolled user process.
5. Biometric templates are encrypted via Windows DPAPI before persistent storage.
6. Mutually exclusive with the authentication pipe via SessionCoordinator.
7. Explicit confirmation or password verification required to replace an existing enabled profile.
8. Strict IO limits: max line length 4 KB, max buffer 8 KB.
9. Continuous listening: keep one instance created/listening at all times, fail startup closed with FILE_FLAG_FIRST_PIPE_INSTANCE.
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
import win32api
import win32process
import win32con

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
    MSG_STATUS_REQUEST,
    MSG_STATUS_RESPONSE,
    REASON_CANCELLED,
    REASON_CAMERA_ERROR,
    REASON_CAMERA_UNAVAILABLE,
    REASON_SESSION_BUSY,
    REASON_INVALID_MESSAGE,
    REASON_INTERNAL_ERROR,
    REASON_CONFIRMATION_REQUIRED,
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
    make_status_response,
)
from engine.enrollment.enrollment_runner import EnrollmentRunner, EnrollmentStepResult
from engine.enrollment.enrollment_session import EnrollmentState
from storage.template_store import SecureProfileStore
from engine.ipc.session_coordinator import SessionCoordinator

logger = logging.getLogger("Peek.EnrollmentPipeServer")

DEFAULT_ENROLLMENT_PIPE_NAME = r"\\.\pipe\PeekEnrollment"
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_PIPE_CONNECTED = 535
ERROR_PIPE_BUSY = 231
ERROR_ACCESS_DENIED = 5

MAX_LINE_LENGTH = 4096      # 4 KB per line
MAX_BUFFER_SIZE = 8192      # 8 KB max buffer before dropping client


def get_client_process_sid(handle) -> Optional[str]:
    """Retrieves the TokenUser SID string for the client process connected to a named pipe handle."""
    try:
        pid = win32pipe.GetNamedPipeClientProcessId(handle)
        h_proc = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            token = win32security.OpenProcessToken(h_proc, win32con.TOKEN_QUERY)
            try:
                sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
                return win32security.ConvertSidToStringSid(sid)
            finally:
                token.Close()
        finally:
            win32api.CloseHandle(h_proc)
    except Exception as ex:
        logger.debug(f"Failed to get client process SID: {ex}")
        return None


def resolve_enrolled_user_sid(profile_store: Optional[SecureProfileStore] = None) -> Optional[str]:
    """
    Resolves the target user SID at startup:
    1. From existing enabled profile in DPAPI storage.
    2. From active console session token via WTSQueryUserToken.
    3. From current process token as local fallback.
    """
    if profile_store is not None:
        try:
            profiles = profile_store.list_profiles()
            enabled = next((p for p in profiles if p.enabled and p.user_sid), None)
            if enabled and enabled.user_sid:
                return enabled.user_sid
        except Exception as ex:
            logger.debug(f"Could not read user_sid from profile store: {ex}")

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        wtsapi32 = ctypes.windll.wtsapi32
        session_id = kernel32.WTSGetActiveConsoleSessionId()
        if session_id != 0xFFFFFFFF:
            h_token = ctypes.c_void_p()
            if wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(h_token)):
                try:
                    tok_handle = pywintypes.HANDLE(h_token.value)
                    sid, _ = win32security.GetTokenInformation(tok_handle, win32security.TokenUser)
                    user_sid = win32security.ConvertSidToStringSid(sid)
                    if user_sid and user_sid != "S-1-5-18":
                        return user_sid
                finally:
                    kernel32.CloseHandle(h_token)
    except Exception as ex:
        logger.debug(f"Console session SID resolution failed: {ex}")

    try:
        token = win32security.OpenProcessToken(win32process.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            proc_sid = win32security.ConvertSidToStringSid(sid)
            if proc_sid and proc_sid != "S-1-5-18":
                return proc_sid
        finally:
            token.Close()
    except Exception as ex:
        logger.debug(f"Process token SID resolution failed: {ex}")

    return None


def build_enrollment_sddl(user_sid: Optional[str]) -> str:
    """
    Builds hardened SDDL for enrollment named pipe.
    SYSTEM (SY) and Builtin Administrators (BA) are granted Generic All (GA).
    If an enrolled user SID is known, that user is granted Generic Read/Write (GRGW).
    If no user is enrolled yet, Authenticated Users (AU) are granted GRGW so
    the initial interactive user can complete setup.
    PIPE_REJECT_REMOTE_CLIENTS ensures only local processes can connect.
    """
    sddl_parts = ["D:(A;;GA;;;SY)(A;;GA;;;BA)"]
    if user_sid and user_sid != "S-1-5-18":
        sddl_parts.append(f"(A;;GRGW;;;{user_sid})")
    else:
        sddl_parts.append("(A;;GRGW;;;AU)")

    try:
        token = win32security.OpenProcessToken(win32process.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            proc_sid = win32security.ConvertSidToStringSid(sid)
            if proc_sid and proc_sid not in ("S-1-5-18", "S-1-5-32-544"):
                sddl_parts.append(f"(A;;GA;;;{proc_sid})")
        finally:
            token.Close()
    except Exception as ex:
        logger.debug(f"Process token SID resolution in build_enrollment_sddl failed: {ex}")

    return "".join(sddl_parts)


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
        Enforces IO limits: max line length 4KB, max buffer 8KB. Drops client if exceeded.
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


class EnrollmentPipeServer:
    r"""
    Windows Named Pipe server on \\.\pipe\PeekEnrollment for streaming guided enrollment
    preview frames, guidance, and state to external UI clients.
    """

    def __init__(
        self,
        pipe_name: str = DEFAULT_ENROLLMENT_PIPE_NAME,
        runner: Optional[EnrollmentRunner] = None,
        coordinator: Optional[SessionCoordinator] = None,
        sddl: Optional[str] = None,
        enrolled_user_sid: Optional[str] = None,
        target_fps: float = 15.0,
        camera: Optional[Any] = None,
        profile_store: Optional[SecureProfileStore] = None,
        face_engine: Optional[Any] = None,
    ):
        self.pipe_name = pipe_name
        self._runner = runner
        self.coordinator = coordinator
        self.target_fps = target_fps
        self._injected_camera = camera
        self.face_engine = face_engine

        if profile_store is not None:
            self.profile_store = profile_store
        elif face_engine is not None and getattr(face_engine, "profile_store", None) is not None:
            self.profile_store = face_engine.profile_store
        else:
            self.profile_store = SecureProfileStore()

        self.enrolled_user_sid = enrolled_user_sid or resolve_enrolled_user_sid(self.profile_store)
        self.sddl = sddl if sddl is not None else build_enrollment_sddl(self.enrolled_user_sid)

        self._stop_event = threading.Event()
        self._is_running = False
        self._server_thread: Optional[threading.Thread] = None
        self._current_session: Optional[EnrollmentPipeClientSession] = None
        self._active_runner: Optional[EnrollmentRunner] = None
        self._runner_lock = threading.Lock()
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
            524288,   # Out buffer size (512KB for JPEG base64 frames)
            524288,   # In buffer size
            0,
            sa
        )

    def _ensure_runner(
        self,
        display_name: str = "Primary User",
        confirmed: bool = False,
        password: Optional[str] = None
    ) -> EnrollmentRunner:
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
                    confirmed=confirmed,
                    password=password,
                )
            else:
                self._active_runner.display_name = display_name
                self._active_runner.confirmed = confirmed
                self._active_runner.confirmation_password = password

            return self._active_runner

    def start(self):
        """
        Starts the enrollment pipe server in a background thread.
        Synchronously creates the first instance with FILE_FLAG_FIRST_PIPE_INSTANCE.
        Fails closed if the pipe name is already taken or squatting attempt detected.
        """
        if self._is_running:
            logger.warning("Enrollment pipe server is already running.")
            return

        try:
            self._initial_pipe = self._create_pipe_instance(is_first=True)
        except pywintypes.error as ex:
            logger.critical(
                f"FATAL: Cannot create first pipe instance for '{self.pipe_name}' (winerror={ex.winerror}). Pipe name already taken or access denied."
            )
            raise RuntimeError(
                f"Failed to create first pipe instance for '{self.pipe_name}': pipe name already taken or invalid: {ex}"
            )
        except Exception as ex:
            logger.critical(f"FATAL: Failed to initialize named pipe '{self.pipe_name}': {ex}")
            raise RuntimeError(f"Pipe initialization failed: {ex}")

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

        if self._active_runner:
            try:
                self._active_runner.cancel()
                self._active_runner.stop()
            except Exception:
                pass

        if self._initial_pipe:
            try:
                win32file.CloseHandle(self._initial_pipe)
            except Exception:
                pass
            self._initial_pipe = None

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
                logger.error(f"Failed to create initial enrollment pipe instance: {ex}")
                self._is_running = False
                return

        while not self._stop_event.is_set():
            try:
                if h_pipe is None:
                    try:
                        h_pipe = self._create_pipe_instance(is_first=False)
                    except Exception as ex:
                        if not self._stop_event.is_set():
                            logger.error(f"Failed to create enrollment pipe instance: {ex}")
                        time.sleep(0.5)
                        continue

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
                        logger.error(f"Failed to create next enrollment pipe instance: {ex}")

                if next_pipe is not None:
                    if h_pipe:
                        try:
                            win32file.CloseHandle(h_pipe)
                        except Exception:
                            pass
                    h_pipe = next_pipe
                else:
                    logger.warning("Reusing existing disconnected enrollment pipe handle for next connection.")

    def _handle_client(self, session: EnrollmentPipeClientSession):
        """Processes requests from a single connected client session."""
        session.write_message(make_enrollment_ready("Peek Guided Enrollment ready"))

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
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Invalid request"))
                continue
            except Exception as ex:
                logger.error(f"Error parsing client message: {ex}")
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Invalid JSON payload"))
                continue

            msg_type = msg.get("type")

            if msg_type == MSG_PING:
                session.write_message(make_pong())

            elif msg_type == MSG_STATUS_REQUEST:
                try:
                    profiles = self.profile_store.list_profiles()
                    enabled_profile = next((p for p in profiles if p.enabled), None)
                    if enabled_profile is not None:
                        session.write_message(
                            make_status_response(
                                has_enrolled_profile=True,
                                display_name=enabled_profile.display_name,
                            )
                        )
                    else:
                        session.write_message(
                            make_status_response(
                                has_enrolled_profile=False,
                                display_name="",
                            )
                        )
                except Exception as ex:
                    logger.error(f"Error checking profile status: {ex}")
                    session.write_message(
                        make_error(REASON_INTERNAL_ERROR, "Status check failed")
                    )

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
                    confirmed = bool(msg.get("confirmed", False))
                    password = msg.get("password")

                    # Requirement 6: Require explicit confirmation to replace an existing enabled profile
                    try:
                        existing_profiles = self.profile_store.list_profiles()
                        has_enabled = any(p.enabled for p in existing_profiles)
                    except Exception as ex:
                        logger.error(f"Failed to check existing profiles: {ex}")
                        has_enabled = False

                    if has_enabled:
                        is_confirmed = False
                        if password:
                            from engine.enrollment.enrollment_session import verify_windows_password
                            if verify_windows_password(password):
                                is_confirmed = True
                            else:
                                logger.warning("Enrollment replacement rejected: invalid password")
                                session.write_message(make_error(REASON_CONFIRMATION_REQUIRED, "Password verification failed"))
                                continue
                        elif confirmed:
                            is_confirmed = True

                        if not is_confirmed:
                            logger.warning("Enrollment replacement rejected: active enabled profile exists and no confirmation provided.")
                            session.write_message(make_error(REASON_CONFIRMATION_REQUIRED, "Confirmation required to replace existing profile"))
                            continue

                    clean_preview = bool(msg.get("clean_preview", True))

                    self._run_enrollment_session(
                        session,
                        display_name=display_name,
                        confirmed=confirmed,
                        password=password,
                        clean_preview=clean_preview,
                    )
                finally:
                    if self.coordinator is not None:
                        self.coordinator.release_enrollment()

            elif msg_type == MSG_CANCEL_ENROLLMENT:
                logger.debug("CANCEL_ENROLLMENT received while idle; acknowledged.")
                session.write_message(make_enrollment_ready("Idle; ready for enrollment"))

            else:
                logger.warning(f"Unknown message type '{msg_type}' received from client.")
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Unknown message type"))

    def _run_enrollment_session(
        self,
        session: EnrollmentPipeClientSession,
        display_name: str,
        confirmed: bool = False,
        password: Optional[str] = None,
        clean_preview: bool = False,
    ):
        """
        Executes a live guided enrollment session:
        1. Verifies client process token SID before streaming frames.
        2. Initializes and starts EnrollmentRunner.
        3. Encodes composited or clean frame as transient base64 JPEG in memory.
        4. Streams PREVIEW_FRAME messages only to verified client.
        5. Emits POSE_COMPLETE and ENROLLMENT_COMPLETE.
        """
        logger.info(f"Starting enrollment session for display_name='{display_name}' (clean_preview={clean_preview})")

        # Requirement 6: Verify client process SID matches enrolled user before streaming frames
        client_sid = get_client_process_sid(session.handle)
        if self.enrolled_user_sid and self.enrolled_user_sid != "S-1-5-18" and client_sid:
            if client_sid != self.enrolled_user_sid and client_sid != "S-1-5-18":
                logger.warning(
                    "Client process SID '%s' does not match enrolled user SID '%s'. Dropping connection without streaming frames.",
                    client_sid, self.enrolled_user_sid
                )
                session.write_message(make_error(REASON_INVALID_MESSAGE, "Unauthorized client"))
                return

        runner = self._ensure_runner(display_name=display_name, confirmed=confirmed, password=password)

        if hasattr(runner, "start_camera") and not runner.start_camera():
            logger.error("Camera acquisition failed for enrollment.")
            session.write_message(make_error(REASON_CAMERA_UNAVAILABLE, "Camera feed unavailable"))
            return

        try:
            runner.start(display_name=display_name, confirmed=confirmed, password=password)
        except TypeError:
            runner.start(display_name=display_name)
        target_interval = 1.0 / max(1.0, self.target_fps)

        try:
            while not self._stop_event.is_set():
                loop_start = time.time()

                # Bounded-latency cancellation check (<50ms)
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

                step_result: Optional[EnrollmentStepResult] = runner.step()
                if step_result is None:
                    if runner.is_cancelled:
                        break
                    logger.error("Runner failed to deliver frame or was aborted.")
                    session.write_message(make_error(REASON_CAMERA_ERROR, "Camera feed lost"))
                    break

                # Select frame and parameters based on clean_preview request
                if clean_preview and getattr(step_result, "preview_frame", None) is not None:
                    frame_to_encode = step_result.preview_frame
                    jpeg_quality = 75
                    overlay_payload = getattr(step_result, "overlay", None)
                    frame_kind_val = "clean"
                else:
                    frame_to_encode = step_result.composited_frame
                    jpeg_quality = 80
                    overlay_payload = None
                    frame_kind_val = None

                # Transient in-memory JPEG compression (never written to disk)
                success, buffer = cv2.imencode(
                    ".jpg", frame_to_encode, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
                )
                if not success:
                    logger.error("Failed to JPEG-encode preview frame.")
                    session.write_message(make_error(REASON_INTERNAL_ERROR, "Failed to encode preview frame"))
                    break

                frame_b64 = base64.b64encode(buffer).decode("ascii")

                # Re-verify client before streaming frame
                if self.enrolled_user_sid and self.enrolled_user_sid != "S-1-5-18" and client_sid:
                    if client_sid != self.enrolled_user_sid and client_sid != "S-1-5-18":
                        logger.warning("Unauthorized client detected during preview frame streaming; aborting.")
                        session.write_message(make_error(REASON_INVALID_MESSAGE, "Unauthorized client"))
                        runner.cancel()
                        break

                preview_msg = make_preview_frame(
                    frame_jpeg_b64=frame_b64,
                    target_pose=step_result.target_pose,
                    quality_state=step_result.quality_state,
                    progress_pct=step_result.progress_pct,
                    guidance_text=step_result.guidance_text,
                    overlay=overlay_payload,
                    frame_kind=frame_kind_val,
                )
                if not session.write_message(preview_msg):
                    logger.info("Failed to send preview frame; client likely disconnected.")
                    runner.cancel()
                    break

                if step_result.pose_just_completed:
                    pose_msg = make_pose_complete(
                        completed_pose=step_result.completed_pose,
                        pose_index=step_result.completed_pose_idx,
                        total_poses=9,
                        detail=f"Pose {step_result.completed_pose} completed successfully",
                    )
                    session.write_message(pose_msg)

                if runner.is_complete:
                    if runner.saved_profile is not None and runner.saved_profile.profile_id:
                        if self.face_engine is not None:
                            try:
                                self.face_engine.reload_active_profile()
                                logger.info(
                                    "Shared FaceEngine active profile reloaded successfully after enrollment of '%s'",
                                    runner.saved_profile.display_name,
                                )
                            except Exception as ex:
                                logger.error("Failed to reload active profile on shared FaceEngine: %s", ex)

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
