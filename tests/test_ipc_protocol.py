"""
Tests for Peek IPC Protocol (engine/ipc/protocol.py)
Validates:
1. Message serialization & deserialization round-trip.
2. Presence of version field ("protocol_version") on every message.
3. Zero-biometric leakage enforcement (prohibited keys raise ProtocolError).
4. Malformed message handling (garbage JSON, non-JSON bytes, empty payloads).
5. Invariant enforcement on make_authenticated (cannot set is_authorized_to_unlock=False).
"""

import unittest
import json
import time

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
    REASON_CAMERA_ERROR,
    REASON_TIMEOUT,
    ProtocolError,
    serialize_message,
    parse_message,
    make_start_auth,
    make_cancel_auth,
    make_ping,
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


class TestIPCProtocol(unittest.TestCase):

    def test_version_field_presence_on_all_constructors(self):
        """Validates that every constructor includes 'protocol_version' == PROTOCOL_VERSION."""
        messages = [
            make_start_auth(),
            make_cancel_auth(),
            make_ping(),
            make_engine_ready(),
            make_camera_starting(),
            make_searching(),
            make_face_found(track_id=1, estimated_pose="CENTER"),
            make_verifying(track_id=1, progress="4/5"),
            make_liveness_check(track_id=1, prompt_text="Blink eyes"),
            make_authenticated(display_name="Alice Smith", is_authorized_to_unlock=True),
            make_auth_failed(reason_code=REASON_NO_MATCH, detail="Face not recognized"),
            make_timeout(),
            make_error(reason_code=REASON_CAMERA_ERROR, detail="Device busy"),
            make_pong(),
        ]

        for msg in messages:
            self.assertIn("protocol_version", msg)
            self.assertEqual(msg["protocol_version"], PROTOCOL_VERSION)
            self.assertIn("type", msg)
            self.assertIsInstance(msg["type"], str)

    def test_serialization_round_trip(self):
        """Validates that messages serialize to UTF-8 JSON lines and parse back identically."""
        original = make_face_found(track_id=42, estimated_pose="TILT_UP", detail="Tracking target 42")
        payload = serialize_message(original)

        self.assertTrue(payload.endswith(b"\n"))
        parsed = parse_message(payload)

        self.assertEqual(parsed["type"], MSG_FACE_FOUND)
        self.assertEqual(parsed["track_id"], 42)
        self.assertEqual(parsed["estimated_pose"], "TILT_UP")
        self.assertEqual(parsed["protocol_version"], PROTOCOL_VERSION)

    def test_zero_biometric_leakage_enforcement(self):
        """
        SECURITY REQUIREMENT:
        Enforces that raw camera frames, embeddings, or template data cannot cross the IPC boundary.
        """
        bad_messages = [
            {"type": "TEST", "embedding": [0.1, 0.2, 0.3]},
            {"type": "TEST", "embeddings": [[0.1] * 512]},
            {"type": "TEST", "raw_frame": b"\x00\x01\x02"},
            {"type": "TEST", "frame": "base64pixeldata"},
            {"type": "TEST", "template": {"id": 1}},
            {"type": "TEST", "landmarks": [[10, 20], [30, 40]]},
        ]

        for bad_msg in bad_messages:
            with self.assertRaises(ProtocolError, msg=f"Should reject forbidden key in {bad_msg}"):
                serialize_message(bad_msg)

    def test_authenticated_message_invariant(self):
        """
        NON-NEGOTIABLE SECURITY INVARIANT:
        make_authenticated must fail if is_authorized_to_unlock is False or display_name is empty.
        """
        # Cannot authorize with False
        with self.assertRaises(ValueError):
            make_authenticated(display_name="Test User", is_authorized_to_unlock=False)

        # Cannot authorize without display_name
        with self.assertRaises(ValueError):
            make_authenticated(display_name="", is_authorized_to_unlock=True)

        with self.assertRaises(ValueError):
            make_authenticated(display_name=None, is_authorized_to_unlock=True)

        # Valid authorization succeeds
        msg = make_authenticated(display_name="Verified User", is_authorized_to_unlock=True)
        self.assertEqual(msg["type"], MSG_AUTHENTICATED)
        self.assertTrue(msg["is_authorized_to_unlock"])
        self.assertEqual(msg["display_name"], "Verified User")

    def test_malformed_message_handling(self):
        """Validates that malformed JSON or invalid bytes do not crash the parser."""
        malformed_inputs = [
            b"",
            b"   \n",
            b"not a json line\n",
            b"{\nincomplete json",
            b'{"type": "TEST"}',                  # missing protocol_version
            b'{"protocol_version": "1.0"}',        # missing type
            b'{"type": 123, "protocol_version": "1.0"}',  # invalid type type
            b"[]\n",                               # not an object
            b"12345\n",
            b"\xff\xfe\xfd",                       # invalid utf-8
        ]

        for bad_input in malformed_inputs:
            with self.assertRaises(ProtocolError, msg=f"Should raise ProtocolError for: {bad_input}"):
                parse_message(bad_input)

    def test_error_and_failure_messages_have_reason_code_and_detail(self):
        """Validates that failure messages always include machine-readable code and human detail."""
        fail_msg = make_auth_failed(reason_code=REASON_SPOOF_DETECTED, detail="Photo replay attack blocked")
        self.assertEqual(fail_msg["type"], MSG_AUTH_FAILED)
        self.assertEqual(fail_msg["reason_code"], REASON_SPOOF_DETECTED)
        self.assertIn("detail", fail_msg)

        err_msg = make_error(reason_code=REASON_CAMERA_ERROR, detail="Device in use")
        self.assertEqual(err_msg["type"], MSG_ERROR)
        self.assertEqual(err_msg["reason_code"], REASON_CAMERA_ERROR)
        self.assertIn("detail", err_msg)


if __name__ == "__main__":
    unittest.main()
