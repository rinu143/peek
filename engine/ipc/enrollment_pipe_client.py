"""
Peek Face Engine - Enrollment Named Pipe Client
Client harness for connecting to \\\\.\\pipe\\PeekEnrollment,
initiating guided enrollment, receiving preview frames and guidance, and managing cancellation.
"""

import time
import logging
from typing import Optional, Dict, Any, Generator

import pywintypes
import win32file
import win32pipe

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
    serialize_message,
    parse_message,
    make_start_enrollment,
    make_cancel_enrollment,
    make_ping,
)

logger = logging.getLogger("Peek.EnrollmentPipeClient")
DEFAULT_ENROLLMENT_PIPE_NAME = r"\\.\pipe\PeekEnrollment"

TERMINAL_MESSAGE_TYPES = {
    MSG_ENROLLMENT_COMPLETE,
    MSG_ENROLLMENT_FAILED,
    MSG_ERROR,
}


class EnrollmentPipeClient:
    """
    Reference Named Pipe client for connecting to Peek Enrollment service.
    """

    def __init__(self, pipe_name: str = DEFAULT_ENROLLMENT_PIPE_NAME):
        self.pipe_name = pipe_name
        self.handle = None
        self._read_buffer = b""

    def connect(self, timeout_seconds: float = 5.0) -> bool:
        """
        Attempts to connect to the Named Pipe server within timeout_seconds.
        Returns True if connected, False if timed out or failed.
        """
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                self.handle = win32file.CreateFile(
                    self.pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                logger.debug(f"Connected to enrollment pipe: {self.pipe_name}")
                return True
            except pywintypes.error as ex:
                # 2 = ERROR_FILE_NOT_FOUND, 231 = ERROR_PIPE_BUSY
                if ex.winerror == 231:
                    try:
                        win32pipe.WaitNamedPipe(self.pipe_name, 1000)
                    except Exception:
                        pass
                time.sleep(0.1)

        logger.error(f"Failed to connect to Named Pipe '{self.pipe_name}' within {timeout_seconds}s.")
        return False

    def is_connected(self) -> bool:
        return self.handle is not None

    def close(self):
        """Closes pipe handle and resets read buffer."""
        if self.handle:
            try:
                win32file.CloseHandle(self.handle)
            except Exception:
                pass
            finally:
                self.handle = None
        self._read_buffer = b""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def send_message(self, msg: Dict[str, Any]) -> bool:
        """Sends a serialized JSON message to the server."""
        if not self.handle:
            logger.error("Cannot send message: Pipe handle is not open.")
            return False

        try:
            payload = serialize_message(msg)
            win32file.WriteFile(self.handle, payload)
            return True
        except pywintypes.error as ex:
            logger.error(f"Failed to write message to pipe: {ex}")
            return False

    def read_message(self, timeout_seconds: float = 3.0) -> Optional[Dict[str, Any]]:
        """
        Reads the next complete JSON-line message from the server.
        Blocks up to timeout_seconds. Returns None if timed out or disconnected.
        """
        if not self.handle:
            return None

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            # Check if complete line is already in buffer
            if b"\n" in self._read_buffer:
                line_bytes, self._read_buffer = self._read_buffer.split(b"\n", 1)
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line:
                    return parse_message(line)

            # Peek available bytes
            try:
                _, avail, _ = win32pipe.PeekNamedPipe(self.handle, 1)
                if avail > 0:
                    _, chunk = win32file.ReadFile(self.handle, avail)
                    self._read_buffer += chunk
                    continue
            except pywintypes.error as ex:
                logger.debug(f"Pipe read error (likely server closed): {ex}")
                self.close()
                return None

            time.sleep(0.01)

        return None

    def start_enrollment(self, display_name: str = "Primary User") -> bool:
        """Convenience method to send START_ENROLLMENT."""
        return self.send_message(make_start_enrollment(display_name=display_name))

    def cancel_enrollment(self, reason: str = "USER_CANCELLED") -> bool:
        """Convenience method to send CANCEL_ENROLLMENT."""
        return self.send_message(make_cancel_enrollment(reason=reason))

    def ping(self) -> bool:
        """Convenience method to send PING."""
        return self.send_message(make_ping())

    def stream_enrollment(
        self,
        display_name: str = "Primary User",
        timeout_seconds: float = 30.0
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Sends START_ENROLLMENT and yields messages (PREVIEW_FRAME, POSE_COMPLETE, etc.)
        until a terminal message (ENROLLMENT_COMPLETE, ENROLLMENT_FAILED, ERROR) is received
        or timeout expires.
        """
        if not self.start_enrollment(display_name=display_name):
            return

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            msg = self.read_message(timeout_seconds=2.0)
            if msg is None:
                continue

            yield msg

            msg_type = msg.get("type")
            if msg_type in TERMINAL_MESSAGE_TYPES:
                break
