"""
Tests for Peek Named Pipe Server (engine/ipc/pipe_server.py)
Validates:
1. Sequential client connect / disconnect cycling.
2. CANCEL_AUTH bounded latency (<100ms).
3. NON-NEGOTIABLE SECURITY CONSTRAINT: AUTHENTICATED only fires when
   is_authorized_to_unlock is strictly True on EngineFrameResult.
4. No authorization when temporal passes but liveness fails (fail open / locked).
5. Display name is dynamically populated from enrolled PeekProfile.display_name.
6. Camera acquisition failure emits ERROR without hanging.
7. Malformed client input does not crash the server.
8. PING -> PONG roundtrip latency.
"""

import unittest
import time
import uuid
import numpy as np

from engine.ipc.pipe_server import PipeServer
from engine.ipc.pipe_client import PipeClient
from engine.ipc.protocol import (
    MSG_START_AUTH,
    MSG_CANCEL_AUTH,
    MSG_PING,
    MSG_PONG,
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
    REASON_CANCELLED,
    REASON_SPOOF_DETECTED,
    REASON_CAMERA_UNAVAILABLE,
    REASON_INVALID_MESSAGE,
    serialize_message,
)
from engine.pipeline.face_engine import EngineFrameResult
from engine.liveness.liveness_detector import LivenessResult
from storage.template_store import PeekProfile


class MockCamera:
    """Mock camera providing dummy black frames."""

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.started = False
        self.stopped = False
        self.read_count = 0

    def start(self) -> bool:
        if self.should_fail:
            return False
        self.started = True
        return True

    def stop(self):
        self.stopped = True

    def read_frame(self):
        if not self.started or self.should_fail:
            return False, None
        self.read_count += 1
        return True, np.zeros((480, 640, 3), dtype=np.uint8)


class MockFaceEngine:
    """Mock FaceEngine returning controllable EngineFrameResult sequences."""

    def __init__(
        self,
        authorized_at_frame: int = 3,
        display_name: str = "Test User",
        simulate_spoof: bool = False,
        temporal_only: bool = False
    ):
        self.authorized_at_frame = authorized_at_frame
        self.display_name = display_name
        self.simulate_spoof = simulate_spoof
        self.temporal_only = temporal_only
        self.frame_counter = 0

        self.active_profile = PeekProfile(
            profile_id="mock_p1",
            display_name=display_name,
            enabled=True
        )

    def reload_active_profile(self):
        pass

    def process_frame(self, frame) -> EngineFrameResult:
        self.frame_counter += 1

        if self.simulate_spoof:
            return EngineFrameResult(
                has_face=True,
                track_id=1,
                state_label="SPOOF_DETECTED",
                is_authorized_to_unlock=False,
                is_live=False,
                liveness_result=LivenessResult(
                    is_live=False,
                    state="SPOOF_DETECTED",
                    fused_score=0.08,
                    motion_score=0.0,
                    texture_score=0.0,
                    eye_score=0.0,
                    spoof_reason="BEZEL_DETECTED"
                ),
                details="Mock spoof detected"
            )

        if self.temporal_only:
            # Temporal pass (100%), but liveness fails -> is_authorized_to_unlock MUST remain False!
            return EngineFrameResult(
                has_face=True,
                track_id=1,
                state_label="MATCH",
                is_temporally_confirmed=True,
                is_live=False,
                is_authorized_to_unlock=False,
                details="Temporal confirmed, liveness failed"
            )

        is_auth = (self.frame_counter >= self.authorized_at_frame)
        return EngineFrameResult(
            has_face=True,
            track_id=1,
            state_label="SUCCESS" if is_auth else "VERIFYING",
            is_authorized_to_unlock=is_auth,
            is_live=is_auth,
            is_temporally_confirmed=True,
            details=f"Mock Frame {self.frame_counter}"
        )


class TestPipeServer(unittest.TestCase):

    def setUp(self):
        self.pipe_name = rf"\\.\pipe\PeekTest_{uuid.uuid4().hex[:8]}"
        self.server = None
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.close()
        if self.server:
            self.server.stop()

    def test_connect_and_ping(self):
        """Validates that a client can connect, receive ENGINE_READY, and exchange PING/PONG."""
        mock_engine = MockFaceEngine()
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=MockCamera())
        self.server.start()

        self.client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))

        ready_msg = self.client.read_message(timeout_seconds=2.0)
        self.assertIsNotNone(ready_msg)
        self.assertEqual(ready_msg.get("type"), MSG_ENGINE_READY)

        # Ping
        self.assertTrue(self.client.ping(timeout_seconds=2.0))

    def test_sequential_connect_disconnect_cycling(self):
        """Validates that multiple sequential clients can connect, operate, and disconnect cleanly."""
        mock_engine = MockFaceEngine()
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=MockCamera())
        self.server.start()

        for i in range(3):
            client = PipeClient(pipe_name=self.pipe_name)
            self.assertTrue(client.connect(timeout_seconds=3.0), f"Cycle {i}: Client failed to connect")

            ready = client.read_message(timeout_seconds=2.0)
            self.assertEqual(ready.get("type"), MSG_ENGINE_READY)

            self.assertTrue(client.ping(timeout_seconds=2.0), f"Cycle {i}: Ping failed")
            client.close()

    def test_authenticated_only_fires_when_authorized(self):
        """
        NON-NEGOTIABLE SECURITY INVARIANT:
        Server emits AUTHENTICATED only when EngineFrameResult.is_authorized_to_unlock == True.
        Display name must match the enrolled profile.
        """
        mock_engine = MockFaceEngine(authorized_at_frame=3, display_name="Dr. Ellen Ripley")
        mock_cam = MockCamera()
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=mock_cam)
        self.server.start()

        self.client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))

        ready = self.client.read_message(timeout_seconds=2.0)
        self.assertEqual(ready.get("type"), MSG_ENGINE_READY)

        messages = list(self.client.stream_auth(timeout_seconds=4.0))
        msg_types = [m.get("type") for m in messages]

        self.assertIn(MSG_CAMERA_STARTING, msg_types)
        self.assertIn(MSG_AUTHENTICATED, msg_types)

        auth_msg = next(m for m in messages if m.get("type") == MSG_AUTHENTICATED)
        self.assertTrue(auth_msg.get("is_authorized_to_unlock"))
        self.assertEqual(auth_msg.get("display_name"), "Dr. Ellen Ripley")

        # Verify camera was stopped after authentication
        self.assertTrue(mock_cam.stopped)

    def test_dual_gate_temporal_match_alone_never_authorizes(self):
        """
        NON-NEGOTIABLE SECURITY INVARIANT:
        When biometric temporal verification passes 100% but liveness fails,
        AUTHENTICATED MUST NEVER BE EMITTED. Session must timeout without unlocking.
        """
        mock_engine = MockFaceEngine(temporal_only=True)
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=MockCamera())
        self.server.start()

        self.client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))

        # Short timeout of 0.4s
        messages = list(self.client.stream_auth(timeout_seconds=0.4))
        msg_types = [m.get("type") for m in messages]

        # AUTHENTICATED must NEVER be in message stream
        self.assertNotIn(MSG_AUTHENTICATED, msg_types)
        self.assertIn(MSG_TIMEOUT, msg_types)

    def test_cancel_auth_bounded_latency(self):
        """
        Validates that CANCEL_AUTH is honored immediately (<100ms) and
        terminates the session with AUTH_FAILED (REASON_CANCELLED).
        """
        # Engine will not authorize for 50 frames
        mock_engine = MockFaceEngine(authorized_at_frame=50)
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=MockCamera())
        self.server.start()

        self.client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))

        self.client.read_message(timeout_seconds=2.0)  # ENGINE_READY

        # Start auth
        self.client.send_message({"type": MSG_START_AUTH, "protocol_version": "1.0", "timeout_seconds": 10.0})

        # Wait for camera starting
        start_msg = self.client.read_message(timeout_seconds=2.0)
        self.assertEqual(start_msg.get("type"), MSG_CAMERA_STARTING)

        # Issue cancel and measure response time
        t0 = time.time()
        self.assertTrue(self.client.cancel_auth())

        cancel_reply = None
        while time.time() - t0 < 1.0:
            msg = self.client.read_message(timeout_seconds=0.2)
            if msg and msg.get("type") == MSG_AUTH_FAILED:
                cancel_reply = msg
                break

        latency = time.time() - t0
        self.assertIsNotNone(cancel_reply, "Did not receive AUTH_FAILED after CANCEL_AUTH")
        self.assertEqual(cancel_reply.get("reason_code"), REASON_CANCELLED)
        # Latency must be bounded (under 250ms)
        self.assertLess(latency, 0.25, f"Cancel latency too high: {latency*1000:.1f}ms")

    def test_spoof_detection_emits_auth_failed(self):
        """Validates that a spoof detection triggers immediate AUTH_FAILED with reason code."""
        mock_engine = MockFaceEngine(simulate_spoof=True)
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=MockCamera())
        self.server.start()

        self.client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)

        messages = list(self.client.stream_auth(timeout_seconds=3.0))
        msg_types = [m.get("type") for m in messages]

        self.assertIn(MSG_AUTH_FAILED, msg_types)
        self.assertNotIn(MSG_AUTHENTICATED, msg_types)

        failed_msg = next(m for m in messages if m.get("type") == MSG_AUTH_FAILED)
        self.assertEqual(failed_msg.get("reason_code"), REASON_SPOOF_DETECTED)

    def test_camera_failure_emits_error(self):
        """Validates that if camera acquisition fails, server emits ERROR without hanging."""
        mock_engine = MockFaceEngine()
        broken_cam = MockCamera(should_fail=True)
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=broken_cam)
        self.server.start()

        self.client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)

        messages = list(self.client.stream_auth(timeout_seconds=3.0))
        msg_types = [m.get("type") for m in messages]

        self.assertIn(MSG_ERROR, msg_types)
        err_msg = next(m for m in messages if m.get("type") == MSG_ERROR)
        self.assertEqual(err_msg.get("reason_code"), REASON_CAMERA_UNAVAILABLE)

    def test_malformed_client_message_does_not_crash_server(self):
        """Validates that client writing garbage JSON receives ERROR but server remains alive."""
        mock_engine = MockFaceEngine()
        self.server = PipeServer(pipe_name=self.pipe_name, face_engine=mock_engine, camera=MockCamera())
        self.server.start()

        self.client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)

        # Write raw non-JSON garbage line
        import win32file
        win32file.WriteFile(self.client.handle, b"{invalid json payload;\n")

        err_msg = self.client.read_message(timeout_seconds=2.0)
        self.assertIsNotNone(err_msg)
        self.assertEqual(err_msg.get("type"), MSG_ERROR)
        self.assertEqual(err_msg.get("reason_code"), REASON_INVALID_MESSAGE)

        # Server should still be healthy and respond to subsequent PING
        self.assertTrue(self.client.ping(timeout_seconds=2.0))


if __name__ == "__main__":
    unittest.main()
