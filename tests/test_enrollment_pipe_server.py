"""
Tests for Peek Enrollment Named Pipe Server (engine/ipc/enrollment_pipe_server.py)
Validates:
1. Client connection, ENROLLMENT_READY handshake, and PING/PONG.
2. ENROLLMENT_COMPLETE only fires after a real finalize_and_save_profile success.
3. ENROLLMENT_COMPLETE does not fire if profile saving fails (emits ENROLLMENT_FAILED).
4. CANCEL_ENROLLMENT mid-session is honored promptly and does NOT persist a profile.
5. Concurrent auth and enrollment sessions are rejected cleanly via SessionCoordinator.
6. Camera unavailability emits ERROR without hanging.
7. Malformed client input emits ERROR without crashing the server.
8. Sequential client connect / disconnect cycling.
"""

import unittest
import time
import uuid
from typing import Optional, Dict, Any

import cv2
import numpy as np

from engine.ipc.enrollment_pipe_server import EnrollmentPipeServer
from engine.ipc.enrollment_pipe_client import EnrollmentPipeClient
from engine.ipc.enrollment_protocol import (
    PROTOCOL_VERSION,
    MSG_START_ENROLLMENT,
    MSG_CANCEL_ENROLLMENT,
    MSG_PING,
    MSG_PONG,
    MSG_ENROLLMENT_READY,
    MSG_PREVIEW_FRAME,
    MSG_POSE_COMPLETE,
    MSG_ENROLLMENT_COMPLETE,
    MSG_ENROLLMENT_FAILED,
    MSG_ERROR,
    REASON_CANCELLED,
    REASON_CAMERA_UNAVAILABLE,
    REASON_CAMERA_ERROR,
    REASON_SESSION_BUSY,
    REASON_INVALID_MESSAGE,
    REASON_INTERNAL_ERROR,
    serialize_message,
)
from engine.enrollment.enrollment_runner import EnrollmentStepResult
from engine.enrollment.enrollment_session import EnrollmentState
from storage.template_store import PeekProfile
from engine.ipc.session_coordinator import SessionCoordinator

# Also import auth pipe components for concurrency tests
from engine.ipc.pipe_server import PipeServer
from engine.ipc.pipe_client import PipeClient
from engine.ipc.protocol import (
    MSG_START_AUTH,
    MSG_CAMERA_STARTING,
    MSG_SEARCHING,
    MSG_AUTHENTICATED,
    MSG_AUTH_FAILED as AUTH_MSG_AUTH_FAILED,
    MSG_ERROR as AUTH_MSG_ERROR,
)
from engine.pipeline.face_engine import EngineFrameResult


class MockEnrollmentRunner:
    """Mock EnrollmentRunner simulating multi-pose guided enrollment."""

    def __init__(
        self,
        frames_to_complete: int = 3,
        fail_profile_save: bool = False,
        camera_available: bool = True,
        display_name: str = "Test User",
    ):
        self.frames_to_complete = frames_to_complete
        self.fail_profile_save = fail_profile_save
        self.camera_available = camera_available
        self.display_name = display_name

        self.started = False
        self.stopped = False
        self._cancelled = False
        self.step_count = 0
        self.is_complete = False
        self.saved_profile: Optional[PeekProfile] = None

    def start_camera(self) -> bool:
        return self.camera_available

    def stop_camera(self):
        self.stopped = True

    def start(self, display_name: Optional[str] = None):
        if display_name:
            self.display_name = display_name
        self.started = True
        self._cancelled = False
        self.step_count = 0
        self.is_complete = False
        self.saved_profile = None

    def cancel(self):
        self._cancelled = True
        self.stopped = True

    def stop(self):
        self.stopped = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def step(self) -> Optional[EnrollmentStepResult]:
        if self._cancelled or not self.camera_available:
            return None

        self.step_count += 1
        dummy_canvas = np.zeros((520, 760, 3), dtype=np.uint8)

        if self.step_count >= self.frames_to_complete:
            self.is_complete = True
            if not self.fail_profile_save and not self._cancelled:
                self.saved_profile = PeekProfile(
                    profile_id=f"profile_mock_{self.step_count}",
                    display_name=self.display_name,
                    enabled=True,
                    templates=[]
                )
            state = EnrollmentState.COMPLETED
            guidance = "Enrollment Complete!"
            pose = "LOWER_RIGHT"
            progress_pct = 100.0
            pose_just_completed = True
        else:
            state = EnrollmentState.GUIDING_TO_POSE
            guidance = f"Look at target pose {self.step_count}"
            pose = "CENTER" if self.step_count == 1 else "LEFT"
            progress_pct = (self.step_count / float(self.frames_to_complete)) * 100.0
            pose_just_completed = (self.step_count == 1)

        return EnrollmentStepResult(
            composited_frame=dummy_canvas,
            state=state,
            guidance_text=guidance,
            target_pose=pose,
            quality_state="PASSED",
            progress_pct=progress_pct,
            pose_just_completed=pose_just_completed,
            completed_pose=pose if pose_just_completed else "",
            completed_pose_idx=self.step_count - 1 if pose_just_completed else -1,
            saved_profile=self.saved_profile,
        )


class MockAuthCamera:
    """Mock camera for PipeServer in concurrency tests."""

    def __init__(self):
        self.started = False

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self):
        self.started = False

    def read_frame(self):
        return True, np.zeros((480, 640, 3), dtype=np.uint8)


class MockAuthFaceEngine:
    """Mock FaceEngine for PipeServer in concurrency tests."""

    def __init__(self):
        self.active_profile = PeekProfile(profile_id="mock_auth_p", display_name="Auth User", enabled=True)

    def reload_active_profile(self):
        pass

    def process_frame(self, frame):
        # Keep searching so session remains active
        return EngineFrameResult(
            has_face=False,
            state_label="SEARCHING",
            is_authorized_to_unlock=False,
            is_live=False,
            details="Searching for face"
        )


class TestEnrollmentPipeServer(unittest.TestCase):

    def setUp(self):
        self.pipe_name = rf"\\.\pipe\PeekTestEnroll_{uuid.uuid4().hex[:8]}"
        self.server: Optional[EnrollmentPipeServer] = None
        self.client: Optional[EnrollmentPipeClient] = None

    def tearDown(self):
        if self.client:
            self.client.close()
        if self.server:
            self.server.stop()

    def test_connect_and_ping(self):
        """Validates that a client connects, receives ENROLLMENT_READY, and exchanges PING/PONG."""
        runner = MockEnrollmentRunner()
        self.server = EnrollmentPipeServer(pipe_name=self.pipe_name, runner=runner)
        self.server.start()

        self.client = EnrollmentPipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))

        ready_msg = self.client.read_message(timeout_seconds=2.0)
        self.assertIsNotNone(ready_msg)
        self.assertEqual(ready_msg.get("type"), MSG_ENROLLMENT_READY)

        # Ping
        self.assertTrue(self.client.ping())
        pong_msg = self.client.read_message(timeout_seconds=2.0)
        self.assertIsNotNone(pong_msg)
        self.assertEqual(pong_msg.get("type"), MSG_PONG)

    def test_enrollment_complete_fires_only_after_successful_profile_save(self):
        """
        Confirms that ENROLLMENT_COMPLETE fires only when all poses are captured
        and finalize_and_save_profile succeeds (mock_runner.saved_profile is not None).
        Also validates preview frames and pose completion messages.
        """
        runner = MockEnrollmentRunner(frames_to_complete=3, display_name="Alice Smith")
        self.server = EnrollmentPipeServer(pipe_name=self.pipe_name, runner=runner, target_fps=60.0)
        self.server.start()

        self.client = EnrollmentPipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)  # ENROLLMENT_READY

        messages = list(self.client.stream_enrollment(display_name="Alice Smith", timeout_seconds=5.0))
        msg_types = [m.get("type") for m in messages]

        # PREVIEW_FRAME messages received
        self.assertIn(MSG_PREVIEW_FRAME, msg_types)
        preview = next(m for m in messages if m.get("type") == MSG_PREVIEW_FRAME)
        self.assertTrue(len(preview.get("frame_jpeg_b64", "")) > 0)
        self.assertIn("target_pose", preview)
        self.assertIn("quality_state", preview)
        self.assertIn("progress_pct", preview)

        # POSE_COMPLETE message received
        self.assertIn(MSG_POSE_COMPLETE, msg_types)

        # ENROLLMENT_COMPLETE message received with real profile_id and display_name
        self.assertIn(MSG_ENROLLMENT_COMPLETE, msg_types)
        complete_msg = next(m for m in messages if m.get("type") == MSG_ENROLLMENT_COMPLETE)
        self.assertEqual(complete_msg.get("display_name"), "Alice Smith")
        self.assertTrue(complete_msg.get("profile_id", "").startswith("profile_mock_"))

        # Verify runner saved_profile was genuinely set
        self.assertIsNotNone(runner.saved_profile)
        self.assertEqual(runner.saved_profile.profile_id, complete_msg.get("profile_id"))

    def test_enrollment_complete_does_not_fire_when_profile_save_fails(self):
        """
        Confirms that if profile saving fails (saved_profile is None),
        ENROLLMENT_COMPLETE is NEVER emitted; ENROLLMENT_FAILED is emitted instead.
        """
        runner = MockEnrollmentRunner(frames_to_complete=3, fail_profile_save=True)
        self.server = EnrollmentPipeServer(pipe_name=self.pipe_name, runner=runner, target_fps=60.0)
        self.server.start()

        self.client = EnrollmentPipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)

        messages = list(self.client.stream_enrollment(display_name="Fail Test", timeout_seconds=4.0))
        msg_types = [m.get("type") for m in messages]

        self.assertNotIn(MSG_ENROLLMENT_COMPLETE, msg_types)
        self.assertIn(MSG_ENROLLMENT_FAILED, msg_types)

        failed_msg = next(m for m in messages if m.get("type") == MSG_ENROLLMENT_FAILED)
        self.assertEqual(failed_msg.get("reason_code"), REASON_INTERNAL_ERROR)

    def test_cancel_enrollment_mid_session_does_not_persist_profile(self):
        """
        Confirms that CANCEL_ENROLLMENT mid-session terminates immediately,
        emits ENROLLMENT_FAILED with REASON_CANCELLED, and never saves a profile.
        """
        # Runner configured with 100 frames so it won't complete on its own
        runner = MockEnrollmentRunner(frames_to_complete=100)
        self.server = EnrollmentPipeServer(pipe_name=self.pipe_name, runner=runner, target_fps=30.0)
        self.server.start()

        self.client = EnrollmentPipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)

        # Start enrollment
        self.client.start_enrollment(display_name="Cancel Test")

        # Wait for at least one PREVIEW_FRAME
        first_frame = None
        for _ in range(20):
            msg = self.client.read_message(timeout_seconds=0.5)
            if msg and msg.get("type") == MSG_PREVIEW_FRAME:
                first_frame = msg
                break
        self.assertIsNotNone(first_frame, "Did not receive initial PREVIEW_FRAME")

        # Send cancel
        t0 = time.time()
        self.client.cancel_enrollment()

        cancel_reply = None
        while time.time() - t0 < 1.0:
            msg = self.client.read_message(timeout_seconds=0.2)
            if msg and msg.get("type") == MSG_ENROLLMENT_FAILED:
                cancel_reply = msg
                break

        latency = time.time() - t0
        self.assertIsNotNone(cancel_reply, "Did not receive ENROLLMENT_FAILED after CANCEL_ENROLLMENT")
        self.assertEqual(cancel_reply.get("reason_code"), REASON_CANCELLED)
        self.assertLess(latency, 0.35, f"Cancel latency too high: {latency*1000:.1f}ms")

        # Invariant: No profile persisted
        self.assertTrue(runner.is_cancelled)
        self.assertIsNone(runner.saved_profile)

    def test_concurrent_auth_and_enrollment_sessions_rejected(self):
        """
        Validates mutual exclusivity between Auth and Enrollment pipes:
        1. When Auth session is active, START_ENROLLMENT is rejected with MSG_ERROR (REASON_SESSION_BUSY).
        2. When Enrollment session is active, START_AUTH is rejected with MSG_ERROR.
        """
        coordinator = SessionCoordinator()

        auth_pipe = rf"\\.\pipe\PeekTestAuth_{uuid.uuid4().hex[:8]}"
        enroll_pipe = rf"\\.\pipe\PeekTestEnroll_{uuid.uuid4().hex[:8]}"

        auth_server = PipeServer(
            pipe_name=auth_pipe,
            face_engine=MockAuthFaceEngine(),
            camera=MockAuthCamera(),
            default_auth_timeout=5.0,
            coordinator=coordinator
        )
        enroll_runner = MockEnrollmentRunner(frames_to_complete=100)
        enroll_server = EnrollmentPipeServer(
            pipe_name=enroll_pipe,
            runner=enroll_runner,
            target_fps=30.0,
            coordinator=coordinator
        )

        auth_server.start()
        enroll_server.start()

        auth_client = PipeClient(pipe_name=auth_pipe)
        enroll_client = EnrollmentPipeClient(pipe_name=enroll_pipe)

        try:
            self.assertTrue(auth_client.connect(timeout_seconds=3.0))
            self.assertTrue(enroll_client.connect(timeout_seconds=3.0))

            auth_client.read_message(timeout_seconds=2.0)   # ENGINE_READY
            enroll_client.read_message(timeout_seconds=2.0) # ENROLLMENT_READY

            # Case A: Start Auth -> Attempt Enrollment -> Should be rejected
            auth_client.send_message({"type": MSG_START_AUTH, "protocol_version": "1.0", "timeout_seconds": 4.0})
            auth_start = auth_client.read_message(timeout_seconds=2.0)
            self.assertEqual(auth_start.get("type"), MSG_CAMERA_STARTING)

            # Enrollment attempt while auth is running
            enroll_client.start_enrollment(display_name="Blocked User")
            enroll_reply = enroll_client.read_message(timeout_seconds=2.0)

            self.assertIsNotNone(enroll_reply)
            self.assertEqual(enroll_reply.get("type"), MSG_ERROR)
            self.assertEqual(enroll_reply.get("reason_code"), REASON_SESSION_BUSY)
            self.assertIn("authentication", enroll_reply.get("detail", "").lower())

            # Cancel Auth session and drain remaining messages
            auth_client.cancel_auth()
            while True:
                m = auth_client.read_message(timeout_seconds=0.3)
                if m is None or m.get("type") == AUTH_MSG_AUTH_FAILED:
                    break
            time.sleep(0.1)  # Allow coordinator lock release

            # Case B: Start Enrollment -> Attempt Auth -> Should be rejected
            enroll_client.start_enrollment(display_name="Active Enrollment")
            enroll_frame = enroll_client.read_message(timeout_seconds=2.0)
            self.assertEqual(enroll_frame.get("type"), MSG_PREVIEW_FRAME)

            # Auth attempt while enrollment is running
            auth_client.send_message({"type": MSG_START_AUTH, "protocol_version": "1.0", "timeout_seconds": 4.0})
            auth_reply = auth_client.read_message(timeout_seconds=2.0)

            self.assertIsNotNone(auth_reply)
            self.assertEqual(auth_reply.get("type"), AUTH_MSG_ERROR)
            self.assertIn("enrollment", auth_reply.get("detail", "").lower())

            # Cancel Enrollment session
            enroll_client.cancel_enrollment()

        finally:
            auth_client.close()
            enroll_client.close()
            auth_server.stop()
            enroll_server.stop()

    def test_camera_unavailable_emits_error(self):
        """Validates that camera failure emits ERROR immediately without hanging."""
        runner = MockEnrollmentRunner(camera_available=False)
        self.server = EnrollmentPipeServer(pipe_name=self.pipe_name, runner=runner)
        self.server.start()

        self.client = EnrollmentPipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)

        self.client.start_enrollment(display_name="No Camera")
        err_msg = self.client.read_message(timeout_seconds=2.0)

        self.assertIsNotNone(err_msg)
        self.assertEqual(err_msg.get("type"), MSG_ERROR)
        self.assertEqual(err_msg.get("reason_code"), REASON_CAMERA_UNAVAILABLE)

    def test_malformed_client_message_does_not_crash_server(self):
        """Validates that malformed JSON from client returns ERROR and keeps server alive."""
        runner = MockEnrollmentRunner()
        self.server = EnrollmentPipeServer(pipe_name=self.pipe_name, runner=runner)
        self.server.start()

        self.client = EnrollmentPipeClient(pipe_name=self.pipe_name)
        self.assertTrue(self.client.connect(timeout_seconds=3.0))
        self.client.read_message(timeout_seconds=2.0)

        # Send invalid JSON
        import win32file
        win32file.WriteFile(self.client.handle, b"{malformed json input\n")

        err_msg = self.client.read_message(timeout_seconds=2.0)
        self.assertIsNotNone(err_msg)
        self.assertEqual(err_msg.get("type"), MSG_ERROR)
        self.assertEqual(err_msg.get("reason_code"), REASON_INVALID_MESSAGE)

        # Server should still respond to PING
        self.assertTrue(self.client.ping())
        pong_msg = self.client.read_message(timeout_seconds=2.0)
        self.assertEqual(pong_msg.get("type"), MSG_PONG)

    def test_sequential_clients_connect_disconnect(self):
        """Validates that multiple clients can connect, operate, and disconnect sequentially."""
        runner = MockEnrollmentRunner()
        self.server = EnrollmentPipeServer(pipe_name=self.pipe_name, runner=runner)
        self.server.start()

        for i in range(3):
            client = EnrollmentPipeClient(pipe_name=self.pipe_name)
            self.assertTrue(client.connect(timeout_seconds=3.0), f"Cycle {i} failed to connect")

            ready = client.read_message(timeout_seconds=2.0)
            self.assertEqual(ready.get("type"), MSG_ENROLLMENT_READY)

            self.assertTrue(client.ping())
            pong = client.read_message(timeout_seconds=2.0)
            self.assertEqual(pong.get("type"), MSG_PONG)

            client.close()


if __name__ == "__main__":
    unittest.main()
