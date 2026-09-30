"""
Tests for Peek Enrollment IPC Protocol (engine/ipc/enrollment_protocol.py)
Validates:
1. Message serialization & deserialization round-trip.
2. Presence of version field ("protocol_version") on every message.
3. Zero-biometric leakage enforcement (prohibited keys raise ProtocolError).
4. Malformed message handling (garbage JSON, non-JSON bytes, empty payloads).
5. Invariant enforcement on make_enrollment_complete (requires valid profile_id and display_name).
6. Failure and error messages reason codes and detail formatting.
"""

import unittest
import json
import time

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
    REASON_QUALITY_FAILED,
    REASON_TIMEOUT,
    ProtocolError,
    serialize_message,
    parse_message,
    make_start_enrollment,
    make_cancel_enrollment,
    make_ping,
    make_enrollment_ready,
    make_preview_frame,
    make_pose_complete,
    make_enrollment_complete,
    make_enrollment_failed,
    make_error,
    make_pong,
)


class TestEnrollmentProtocol(unittest.TestCase):

    def test_version_field_presence_on_all_constructors(self):
        """Validates that every constructor includes 'protocol_version' == PROTOCOL_VERSION."""
        messages = [
            make_start_enrollment(display_name="Test User"),
            make_cancel_enrollment(reason="USER_CANCELLED"),
            make_ping(),
            make_enrollment_ready(),
            make_preview_frame(
                frame_jpeg_b64="dummy_b64",
                target_pose="CENTER",
                quality_state="PASSED",
                progress_pct=11.1,
                guidance_text="Look Straight"
            ),
            make_pose_complete(completed_pose="CENTER", pose_index=0, total_poses=9),
            make_enrollment_complete(profile_id="profile_123", display_name="Test User"),
            make_enrollment_failed(reason_code=REASON_CANCELLED, detail="User aborted"),
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
        original = make_preview_frame(
            frame_jpeg_b64="base64_encoded_jpeg_data",
            target_pose="UPPER_LEFT",
            quality_state="PASSED",
            progress_pct=55.5,
            guidance_text="Hold steady..."
        )
        payload = serialize_message(original)

        self.assertTrue(payload.endswith(b"\n"))
        parsed = parse_message(payload)

        self.assertEqual(parsed["type"], MSG_PREVIEW_FRAME)
        self.assertEqual(parsed["target_pose"], "UPPER_LEFT")
        self.assertEqual(parsed["quality_state"], "PASSED")
        self.assertEqual(parsed["progress_pct"], 55.5)
        self.assertEqual(parsed["guidance_text"], "Hold steady...")
        self.assertEqual(parsed["frame_jpeg_b64"], "base64_encoded_jpeg_data")
        self.assertEqual(parsed["protocol_version"], PROTOCOL_VERSION)

    def test_zero_biometric_leakage_enforcement(self):
        """
        SECURITY REQUIREMENT:
        Enforces that raw facial embeddings or template vectors cannot cross the IPC boundary.
        (Note: JPEG-encoded preview frames for UI preview are explicitly permitted per requirements).
        """
        bad_messages = [
            {"type": MSG_PREVIEW_FRAME, "embedding": [0.1, 0.2, 0.3]},
            {"type": MSG_PREVIEW_FRAME, "embeddings": [[0.1] * 512]},
            {"type": MSG_PREVIEW_FRAME, "template": {"id": 1, "vector": [0.1] * 512}},
            {"type": MSG_PREVIEW_FRAME, "templates": [{"id": 1}]},
        ]

        for bad_msg in bad_messages:
            with self.assertRaises(ProtocolError, msg=f"Should reject forbidden key in {bad_msg}"):
                serialize_message(bad_msg)

    def test_enrollment_complete_invariant(self):
        """
        SECURITY INVARIANT:
        make_enrollment_complete must fail if profile_id or display_name is empty or invalid.
        """
        with self.assertRaises(ValueError):
            make_enrollment_complete(profile_id="", display_name="Test User")

        with self.assertRaises(ValueError):
            make_enrollment_complete(profile_id=None, display_name="Test User")

        with self.assertRaises(ValueError):
            make_enrollment_complete(profile_id="p123", display_name="")

        with self.assertRaises(ValueError):
            make_enrollment_complete(profile_id="p123", display_name=None)

        # Valid constructor succeeds
        msg = make_enrollment_complete(profile_id="p_valid_99", display_name="Jane Doe")
        self.assertEqual(msg["type"], MSG_ENROLLMENT_COMPLETE)
        self.assertEqual(msg["profile_id"], "p_valid_99")
        self.assertEqual(msg["display_name"], "Jane Doe")

    def test_malformed_message_handling(self):
        """Validates that malformed JSON or invalid bytes do not crash the parser."""
        malformed_inputs = [
            b"",
            b"   \n",
            b"not a valid json payload\n",
            b"{\nincomplete json",
            b'{"type": "START_ENROLLMENT"}',             # missing protocol_version
            b'{"protocol_version": "1.0"}',              # missing type
            b'{"type": 1234, "protocol_version": "1.0"}', # invalid type type
            b"[]\n",                                      # not a JSON object
            b"999\n",
            b"\xff\xfe\xfd",                              # invalid utf-8
        ]

        for bad_input in malformed_inputs:
            with self.assertRaises(ProtocolError, msg=f"Should raise ProtocolError for: {bad_input}"):
                parse_message(bad_input)

    def test_error_and_failure_messages_have_reason_code_and_detail(self):
        """Validates that failure messages always include machine-readable code and human detail."""
        fail_msg = make_enrollment_failed(reason_code=REASON_CANCELLED, detail="Enrollment cancelled by user")
        self.assertEqual(fail_msg["type"], MSG_ENROLLMENT_FAILED)
        self.assertEqual(fail_msg["reason_code"], REASON_CANCELLED)
        self.assertIn("detail", fail_msg)

        err_msg = make_error(reason_code=REASON_CAMERA_UNAVAILABLE, detail="Webcam busy")
        self.assertEqual(err_msg["type"], MSG_ERROR)
        self.assertEqual(err_msg["reason_code"], REASON_CAMERA_UNAVAILABLE)
        self.assertIn("detail", err_msg)


if __name__ == "__main__":
    unittest.main()
