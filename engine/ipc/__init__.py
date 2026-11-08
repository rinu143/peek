"""
Peek Face Engine - IPC (Inter-Process Communication) Layer
Enables secure, decoupled Windows Named Pipe communication between the Face Engine
and external callers such as the Windows Credential Provider (LogonUI).
"""

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
    ProtocolError,
    serialize_message,
    parse_message,
)

__all__ = [
    "PROTOCOL_VERSION",
    "MSG_START_AUTH",
    "MSG_CANCEL_AUTH",
    "MSG_PING",
    "MSG_ENGINE_READY",
    "MSG_CAMERA_STARTING",
    "MSG_SEARCHING",
    "MSG_FACE_FOUND",
    "MSG_VERIFYING",
    "MSG_LIVENESS_CHECK",
    "MSG_AUTHENTICATED",
    "MSG_AUTH_FAILED",
    "MSG_TIMEOUT",
    "MSG_ERROR",
    "MSG_PONG",
    "ProtocolError",
    "serialize_message",
    "parse_message",
    "EnrollmentPipeServer",
    "EnrollmentPipeClient",
    "SessionCoordinator",
]

from engine.ipc.enrollment_pipe_server import EnrollmentPipeServer
from engine.ipc.enrollment_pipe_client import EnrollmentPipeClient
from engine.ipc.session_coordinator import SessionCoordinator
