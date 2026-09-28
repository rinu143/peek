"""
Peek Face Engine - Windows Named Pipe Reference Client
Layer F: Lightweight test harness client for verifying the Peek IPC protocol.

This module provides a Python reference client to connect to \\\\.\\pipe\\PeekEngine,
initiate authentication, receive streamed state updates, and exercise cancel/ping commands.
(Note: The production Phase 5 Credential Provider will be implemented in C++).
"""

import sys
import os
import time
import logging
from typing import Optional, Dict, Any, Generator

import pywintypes
import win32file
import win32pipe

from engine.ipc.protocol import (
    PROTOCOL_VERSION,
    MSG_START_AUTH,
    MSG_CANCEL_AUTH,
    MSG_PING,
    MSG_ENGINE_READY,
    MSG_AUTHENTICATED,
    MSG_AUTH_FAILED,
    MSG_TIMEOUT,
    MSG_ERROR,
    MSG_PONG,
    serialize_message,
    parse_message,
    make_start_auth,
    make_cancel_auth,
    make_ping,
)

logger = logging.getLogger("Peek.PipeClient")
DEFAULT_PIPE_NAME = r"\\.\pipe\PeekEngine"

TERMINAL_MESSAGE_TYPES = {
    MSG_AUTHENTICATED,
    MSG_AUTH_FAILED,
    MSG_TIMEOUT,
    MSG_ERROR,
}


class PipeClient:
    """
    Reference Named Pipe client for connecting to Peek Face Engine service.
    """

    def __init__(self, pipe_name: str = DEFAULT_PIPE_NAME):
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
                    None
                )
                logger.debug(f"Connected to pipe: {self.pipe_name}")
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

            time.sleep(0.015)

        return None

    def ping(self, timeout_seconds: float = 2.0) -> bool:
        """Sends a PING message and waits for PONG."""
        if not self.send_message(make_ping()):
            return False
        reply = self.read_message(timeout_seconds=timeout_seconds)
        return reply is not None and reply.get("type") == MSG_PONG

    def cancel_auth(self, reason: str = "USER_CANCELLED") -> bool:
        """Sends CANCEL_AUTH message to abort current authentication session."""
        return self.send_message(make_cancel_auth(reason=reason))

    def stream_auth(self, timeout_seconds: float = 12.0) -> Generator[Dict[str, Any], None, None]:
        """
        Sends START_AUTH and yields streamed state updates until a terminal message
        (AUTHENTICATED, AUTH_FAILED, TIMEOUT, ERROR) is received or connection drops.
        """
        if not self.send_message(make_start_auth(timeout_seconds=timeout_seconds)):
            yield {"type": MSG_ERROR, "reason_code": "PIPE_ERROR", "detail": "Failed to send START_AUTH"}
            return

        auth_start_time = time.time()
        max_duration = timeout_seconds + 3.0  # Allow slight server-side grace margin

        while time.time() - auth_start_time < max_duration:
            msg = self.read_message(timeout_seconds=2.0)
            if msg is None:
                if not self.is_connected():
                    yield {
                        "type": MSG_ERROR,
                        "reason_code": "SERVER_DISCONNECTED",
                        "detail": "Pipe server disconnected unexpectedly"
                    }
                    break
                continue

            yield msg

            if msg.get("type") in TERMINAL_MESSAGE_TYPES:
                break


def main():
    """Command-line utility demonstrating client connection and streamed auth."""
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
    print("=" * 60)
    print("   PEEK — NAMED PIPE REFERENCE CLIENT (Phase 5 IPC Test)")
    print("=" * 60)

    client = PipeClient()
    print(f"Connecting to Named Pipe: {DEFAULT_PIPE_NAME}...")
    if not client.connect(timeout_seconds=4.0):
        print(f"ERROR: Could not connect to {DEFAULT_PIPE_NAME}.")
        print("Ensure 'python scripts/run_engine_service.py' is running in another console.")
        sys.exit(1)

    print("Connected successfully! Waiting for ENGINE_READY...")
    ready_msg = client.read_message(timeout_seconds=2.0)
    print(f"Server Initial Greeting: {ready_msg}")

    print("\nSending PING to test roundtrip latency...")
    ping_ok = client.ping(timeout_seconds=2.0)
    print(f"Ping result: {'OK (PONG received)' if ping_ok else 'FAILED'}")

    print("\nStarting authentication session (START_AUTH, timeout=12s)...")
    print("-" * 60)
    for state in client.stream_auth(timeout_seconds=12.0):
        m_type = state.get("type", "UNKNOWN")
        if m_type == MSG_AUTHENTICATED:
            print(f"✓ [{m_type}] Display Name: '{state.get('display_name')}' | Authorized: {state.get('is_authorized_to_unlock')}")
            print(f"   Detail: {state.get('detail')}")
        elif m_type in (MSG_AUTH_FAILED, MSG_TIMEOUT, MSG_ERROR):
            print(f"✗ [{m_type}] Code: {state.get('reason_code')} | Detail: {state.get('detail')}")
        else:
            detail = state.get('detail') or state.get('message') or ""
            print(f"  [{m_type:15s}] {detail}")

    print("-" * 60)
    client.close()
    print("Session finished. Client closed.")


if __name__ == "__main__":
    main()
