"""
Peek Face Engine - IPC Protocol Definition
Layer F: Inter-Process Communication Protocol for Windows Credential Provider.

SECURITY & PRIVACY CONSTRAINTS:
1. Versioned JSON-line message schema: every message contains a "protocol_version" field.
2. Zero biometric leakage: no raw camera frames, embeddings, or template data
   are EVER permitted to cross the pipe. Only state labels, booleans, display_name,
   and sanitized status text are transmitted.
3. Fail open, bounded latency: error and failure responses are machine-readable
   without leaking raw exception tracebacks to client processes.
4. Nonce binding: START_AUTH carries a per-attempt random 128-bit challenge nonce
   that must be echoed identically in AUTHENTICATED.
5. Profile ID protection: profile_id is NEVER emitted across the authentication pipe.
"""

import json
import time
import secrets
from typing import Dict, Any, Optional

PROTOCOL_VERSION = "2.0"

# Client -> Server Message Types
MSG_START_AUTH = "START_AUTH"
MSG_CANCEL_AUTH = "CANCEL_AUTH"
MSG_PING = "PING"

# Server -> Client Message Types
MSG_ENGINE_READY = "ENGINE_READY"
MSG_CAMERA_STARTING = "CAMERA_STARTING"
MSG_SEARCHING = "SEARCHING"
MSG_FACE_FOUND = "FACE_FOUND"
MSG_VERIFYING = "VERIFYING"
MSG_LIVENESS_CHECK = "LIVENESS_CHECK"
MSG_AUTHENTICATED = "AUTHENTICATED"
MSG_AUTH_FAILED = "AUTH_FAILED"
MSG_TIMEOUT = "TIMEOUT"
MSG_ERROR = "ERROR"
MSG_PONG = "PONG"

# Valid Message Sets
VALID_CLIENT_MESSAGES = {
    MSG_START_AUTH,
    MSG_CANCEL_AUTH,
    MSG_PING,
}

VALID_SERVER_MESSAGES = {
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
}

# Machine-Readable Reason Codes
REASON_SPOOF_DETECTED = "SPOOF_DETECTED"
REASON_NO_MATCH = "NO_MATCH"
REASON_CHALLENGE_FAILED = "CHALLENGE_FAILED"
REASON_CANCELLED = "CANCELLED"
REASON_NO_PROFILE = "NO_PROFILE"
REASON_TIMEOUT = "TIMEOUT"
REASON_CAMERA_ERROR = "CAMERA_ERROR"
REASON_CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"
REASON_ENGINE_ERROR = "ENGINE_ERROR"
REASON_INVALID_MESSAGE = "INVALID_MESSAGE"
REASON_INTERNAL_ERROR = "INTERNAL_ERROR"
REASON_CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"

# Security guard: prohibited payload keys that could leak raw biometric/template data or private IDs
PROHIBITED_PAYLOAD_KEYS = {
    "embedding",
    "embeddings",
    "raw_frame",
    "frame",
    "template",
    "templates",
    "pixel_data",
    "landmarks",
}


class ProtocolError(Exception):
    """Raised when an IPC message violates the versioned wire protocol."""
    pass


def serialize_message(msg: Dict[str, Any]) -> bytes:
    """
    Serializes a message dictionary to a newline-terminated UTF-8 JSON byte stream.
    Validates protocol version and enforces zero-biometric leakage constraint.
    """
    if not isinstance(msg, dict):
        raise ProtocolError(f"Message must be a dictionary, got {type(msg).__name__}")

    # Enforce protocol version
    if "protocol_version" not in msg:
        msg["protocol_version"] = PROTOCOL_VERSION

    # Security check: forbid any biometric or pixel payload
    for key in msg:
        if key.lower() in PROHIBITED_PAYLOAD_KEYS:
            raise ProtocolError(
                f"Security violation: Prohibited key '{key}' cannot cross the IPC boundary."
            )

    try:
        raw_json = json.dumps(msg, ensure_ascii=False)
    except Exception as ex:
        raise ProtocolError(f"Failed to serialize message to JSON: {ex}")

    return (raw_json + "\n").encode("utf-8")


def parse_message(raw_data: Any) -> Dict[str, Any]:
    """
    Parses a JSON line (str or bytes) into a validated message dictionary.
    Handles malformed JSON or corrupted data safely without crashing.
    Enforces strict protocol schema and version.
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

    if data["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError(
            f"Unsupported protocol version '{data['protocol_version']}'; expected '{PROTOCOL_VERSION}'"
        )

    msg_type = data["type"]
    if msg_type not in VALID_CLIENT_MESSAGES and msg_type not in VALID_SERVER_MESSAGES:
        raise ProtocolError(f"Unknown message type '{msg_type}'")

    # Prohibited keys check on receive
    for key in data:
        if key.lower() in PROHIBITED_PAYLOAD_KEYS:
            raise ProtocolError(
                f"Security violation: Prohibited key '{key}' received in IPC message."
            )

    return data


# --- Helper Constructors for Client Messages ---

def make_start_auth(timeout_seconds: float = 12.0, nonce: Optional[str] = None) -> Dict[str, Any]:
    if nonce is None:
        nonce = secrets.token_hex(16)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_START_AUTH,
        "nonce": str(nonce),
        "timeout_seconds": float(timeout_seconds),
        "timestamp": time.time(),
    }


def make_cancel_auth(reason: str = "USER_CANCELLED") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_CANCEL_AUTH,
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

def make_engine_ready(message: str = "Engine ready for authentication") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_ENGINE_READY,
        "message": message,
        "timestamp": time.time(),
    }


def make_camera_starting(message: str = "Acquiring camera device...") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_CAMERA_STARTING,
        "message": message,
        "timestamp": time.time(),
    }


def make_searching(detail: str = "Looking for user face...") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_SEARCHING,
        "detail": detail,
        "timestamp": time.time(),
    }


def make_face_found(
    track_id: Optional[int] = None,
    estimated_pose: Optional[str] = None,
    detail: str = "Face detected, tracking active"
) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_FACE_FOUND,
        "track_id": track_id,
        "estimated_pose": estimated_pose,
        "detail": detail,
        "timestamp": time.time(),
    }


def make_verifying(
    track_id: Optional[int] = None,
    progress: Optional[str] = None,
    detail: str = "Verifying identity..."
) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_VERIFYING,
        "track_id": track_id,
        "progress": progress,
        "detail": detail,
        "timestamp": time.time(),
    }


def make_liveness_check(
    track_id: Optional[int] = None,
    prompt_text: Optional[str] = None,
    detail: str = "Evaluating passive/active liveness..."
) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_LIVENESS_CHECK,
        "track_id": track_id,
        "prompt_text": prompt_text,
        "detail": detail,
        "timestamp": time.time(),
    }


def make_authenticated(
    display_name: str,
    nonce: Optional[str] = None,
    is_authorized_to_unlock: bool = True,
    detail: str = "Authentication successful"
) -> Dict[str, Any]:
    """
    CRITICAL SECURITY INVARIANTS:
    1. is_authorized_to_unlock must strictly be True.
    2. display_name must be non-empty.
    3. nonce must match the per-attempt 128-bit challenge from START_AUTH.
    4. profile_id is NEVER emitted across the pipe.
    """
    if not is_authorized_to_unlock:
        raise ValueError("Cannot construct AUTHENTICATED message with is_authorized_to_unlock=False")

    if not display_name or not isinstance(display_name, str):
        raise ValueError("AUTHENTICATED message requires a valid display_name")

    if nonce is None:
        nonce = secrets.token_hex(16)
    elif not nonce or not isinstance(nonce, str):
        raise ValueError("AUTHENTICATED message requires a valid non-empty nonce")

    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_AUTHENTICATED,
        "is_authorized_to_unlock": True,
        "display_name": display_name,
        "nonce": str(nonce),
        "detail": detail,
        "timestamp": time.time(),
    }


def make_auth_failed(reason_code: str, detail: str = "Authentication failed") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_AUTH_FAILED,
        "reason_code": str(reason_code),
        "detail": str(detail),
        "timestamp": time.time(),
    }


def make_timeout(detail: str = "Authentication timed out") -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": MSG_TIMEOUT,
        "reason_code": REASON_TIMEOUT,
        "detail": str(detail),
        "timestamp": time.time(),
    }


def make_error(reason_code: str, detail: str = "Internal engine error") -> Dict[str, Any]:
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
