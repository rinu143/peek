"""
Peek Face Engine - Enrollment IPC Protocol Definition
Inter-Process Communication Protocol for Windows Guided Enrollment Named Pipe (\\\\.\\pipe\\PeekEnrollment).

SECURITY & DESIGN PRINCIPLES:
1. Versioned JSON-line message schema: every message contains a "protocol_version" field.
2. In-memory transient preview streaming: preview frames are JPEG-encoded in memory,
   streamed via base64 JSON, and never written to disk by the IPC server.
3. Fail open, bounded latency: error and failure responses are machine-readable
   without leaking raw internal stack traces across the pipe.
"""

import json
import time
from typing import Dict, Any, Optional

PROTOCOL_VERSION = "1.0"

# Client -> Server Message Types
MSG_START_ENROLLMENT = "START_ENROLLMENT"
MSG_CANCEL_ENROLLMENT = "CANCEL_ENROLLMENT"
MSG_PING = "PING"

# Server -> Client Message Types
MSG_ENROLLMENT_READY = "ENROLLMENT_READY"
MSG_PREVIEW_FRAME = "PREVIEW_FRAME"
MSG_POSE_COMPLETE = "POSE_COMPLETE"
MSG_ENROLLMENT_COMPLETE = "ENROLLMENT_COMPLETE"
MSG_ENROLLMENT_FAILED = "ENROLLMENT_FAILED"
MSG_ERROR = "ERROR"
MSG_PONG = "PONG"

# Valid Message Sets
VALID_CLIENT_MESSAGES = {
    MSG_START_ENROLLMENT,
    MSG_CANCEL_ENROLLMENT,
    MSG_PING,
}

VALID_SERVER_MESSAGES = {
    MSG_ENROLLMENT_READY,
    MSG_PREVIEW_FRAME,
    MSG_POSE_COMPLETE,
    MSG_ENROLLMENT_COMPLETE,
    MSG_ENROLLMENT_FAILED,
    MSG_ERROR,
    MSG_PONG,
}

# Machine-Readable Reason Codes
REASON_CANCELLED = "CANCELLED"
REASON_CAMERA_ERROR = "CAMERA_ERROR"
REASON_CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"
REASON_SESSION_BUSY = "SESSION_BUSY"
REASON_INVALID_MESSAGE = "INVALID_MESSAGE"
REASON_INTERNAL_ERROR = "INTERNAL_ERROR"
REASON_QUALITY_FAILED = "QUALITY_FAILED"
REASON_TIMEOUT = "TIMEOUT"


class ProtocolError(Exception):
    """Raised when an IPC message violates the enrollment wire protocol."""
    pass


def serialize_message(msg: Dict[str, Any]) -> bytes:
    """
    Serializes a message dictionary to a newline-terminated UTF-8 JSON byte stream.
    Validates protocol version.
    """
    if not isinstance(msg, dict):
        raise ProtocolError(f"Message must be a dictionary, got {type(msg).__name__}")

    # Enforce protocol version
    if "protocol_version" not in msg:
        msg["protocol_version"] = PROTOCOL_VERSION

    # Zero-biometric leakage check: raw embeddings or template data must never cross the pipe
    FORBIDDEN_KEYS = {"embedding", "embeddings", "template", "templates"}
    for k in FORBIDDEN_KEYS:
        if k in msg:
            raise ProtocolError(f"Zero-biometric leakage violation: key '{k}' prohibited in IPC message")

    try:
        raw_json = json.dumps(msg, ensure_ascii=False)
    except Exception as ex:
        raise ProtocolError(f"Failed to serialize message to JSON: {ex}")

    return (raw_json + "\n").encode("utf-8")


def parse_message(raw_data: Any) -> Dict[str, Any]:
    """
    Parses a JSON line (str or bytes) into a validated message dictionary.
    Handles malformed JSON safely without crashing.
    """
    if isinstance(raw_data, bytes):
        try:
            line = raw_data.decode("utf-8").strip()
        except UnicodeDecodeError as ex:
            raise ProtocolError(f"Invalid UTF-8 payload: {ex}")
    elif isinstance(raw_data, str):
        line = raw_data.strip()
    else:
        raise ProtocolError(f"Expected str or bytes, got {type(raw_data).__name__}")

    if not line:
        raise ProtocolError("Empty message received")

    try:
        data = json.loads(line)
    except json.JSONDecodeError as ex:
        raise ProtocolError(f"Malformed JSON: {ex}")

    if not isinstance(data, dict):
        raise ProtocolError(f"Expected JSON object, got {type(data).__name__}")

    # Check required fields
    if "type" not in data or not isinstance(data["type"], str):
        raise ProtocolError("Missing or invalid 'type' field in message")

    if "protocol_version" not in data:
        raise ProtocolError("Missing 'protocol_version' field in message")

    return data


# --- Helper Constructors for Client Messages ---

def make_start_enrollment(display_name: str = "Primary User") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_START_ENROLLMENT,
        "display_name": str(display_name),
        "timestamp": time.time(),
    }


def make_cancel_enrollment(reason: str = "USER_CANCELLED") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_CANCEL_ENROLLMENT,
        "reason": str(reason),
        "timestamp": time.time(),
    }


def make_ping() -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_PING,
        "timestamp": time.time(),
    }


# --- Helper Constructors for Server Messages ---

def make_enrollment_ready(message: str = "Enrollment engine ready") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_ENROLLMENT_READY,
        "message": str(message),
        "timestamp": time.time(),
    }


def make_preview_frame(
    frame_jpeg_b64: str,
    target_pose: str,
    quality_state: str,
    progress_pct: float,
    guidance_text: str = "",
) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_PREVIEW_FRAME,
        "frame": frame_jpeg_b64,
        "image": frame_jpeg_b64,
        "frame_jpeg_b64": frame_jpeg_b64,
        "target_pose": str(target_pose),
        "quality_state": str(quality_state),
        "progress_pct": float(progress_pct),
        "guidance_text": str(guidance_text),
        "timestamp": time.time(),
    }


def make_pose_complete(
    completed_pose: str,
    pose_index: int,
    total_poses: int = 9,
    detail: str = "Pose completed successfully"
) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_POSE_COMPLETE,
        "completed_pose": str(completed_pose),
        "pose_index": int(pose_index),
        "total_poses": int(total_poses),
        "detail": str(detail),
        "timestamp": time.time(),
    }


def make_enrollment_complete(
    profile_id: str,
    display_name: str,
    detail: str = "Biometric profile enrolled and encrypted successfully via DPAPI"
) -> Dict[str, Any]:
    if not profile_id or not isinstance(profile_id, str):
        raise ValueError("ENROLLMENT_COMPLETE requires a valid profile_id")
    if not display_name or not isinstance(display_name, str):
        raise ValueError("ENROLLMENT_COMPLETE requires a valid display_name")

    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_ENROLLMENT_COMPLETE,
        "profile_id": str(profile_id),
        "display_name": str(display_name),
        "detail": str(detail),
        "timestamp": time.time(),
    }


def make_enrollment_failed(reason_code: str, detail: str) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_ENROLLMENT_FAILED,
        "reason_code": str(reason_code),
        "detail": str(detail),
        "timestamp": time.time(),
    }


def make_error(reason_code: str, detail: str) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_ERROR,
        "reason_code": str(reason_code),
        "detail": str(detail),
        "timestamp": time.time(),
    }


def make_pong() -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_PONG,
        "timestamp": time.time(),
    }
