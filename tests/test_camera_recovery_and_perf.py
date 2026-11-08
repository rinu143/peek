"""
Unit tests for Camera Auto-Selection, Recovery/Backoff, Power State Handling,
Least-Privilege Resiliency, and Latency Performance Reporting.

Covers:
1. Camera auto-selection: color vs IR/grayscale rejection, busy/missing detection, config override.
2. Auto-exposure warmup frame discarding.
3. Camera reopen with exponential backoff on consecutive read failures.
4. Per-session exception isolation (pipe server keeps running).
5. Sleep/resume power watcher detection and camera recreation.
6. Performance report parser (p50/p95 stage/e2e latency, faces breakdown, CPU/RAM CSV).
"""

import os
import sys
import time
import json
import uuid
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import numpy as np

from engine.camera.camera_capture import (
    CameraCapture,
    is_color_frame,
    probe_camera_index,
    find_best_camera_index,
    get_config_override_index,
)
from engine.camera.power_watcher import PowerStateWatcher
from engine.ipc.pipe_server import PipeServer
from engine.ipc.pipe_client import PipeClient
from engine.ipc.protocol import (
    MSG_START_AUTH,
    make_start_auth,
    make_ping,
    MSG_PING,
    MSG_PONG,
    MSG_AUTHENTICATED,
    MSG_ERROR,
    REASON_CAMERA_ERROR,
)
from engine.pipeline.face_engine import EngineFrameResult
from storage.template_store import PeekProfile
from scripts.perf_report import (
    SessionData,
    calculate_percentile,
    analyze_sessions,
    parse_perf_samples_csv,
    export_report_csv,
    parse_latency_logs,
)
from scripts.run_engine_service import unhandled_exception_handler


class TestCameraAutoSelection(unittest.TestCase):
    """Validates camera auto-selection, IR/grayscale rejection, and override."""

    def test_is_color_frame_rgb(self):
        # Create a synthetic color frame (e.g., strong red and green, low blue)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :, 0] = 20   # B
        frame[:, :, 1] = 180  # G
        frame[:, :, 2] = 240  # R
        is_color, mean_diff, sat = is_color_frame(frame)
        self.assertTrue(is_color)
        self.assertGreater(mean_diff, 50.0)
        self.assertGreater(sat, 50.0)

    def test_is_color_frame_pure_grayscale_ir(self):
        # Pure monochrome / IR stream where R == G == B
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)
        is_color, mean_diff, sat = is_color_frame(frame)
        self.assertFalse(is_color)
        self.assertAlmostEqual(mean_diff, 0.0, places=2)
        self.assertAlmostEqual(sat, 0.0, places=2)

    def test_is_color_frame_near_grayscale_ir_noise(self):
        # IR camera with sensor quantization noise (small difference between channels)
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)
        frame[:, :, 0] = 127
        frame[:, :, 1] = 128
        frame[:, :, 2] = 129
        is_color, mean_diff, sat = is_color_frame(frame)
        self.assertFalse(is_color)
        self.assertLess(mean_diff, 3.0)

    @patch("cv2.VideoCapture")
    def test_probe_camera_index_busy_or_missing(self, mock_vc_cls):
        # Camera fails to open
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_vc_cls.return_value = mock_cap

        res = probe_camera_index(0)
        self.assertFalse(res["is_color"])
        self.assertEqual(res["status"], "unavailable")
        self.assertIn("unavailable", res["reason"])

    @patch("cv2.VideoCapture")
    def test_probe_camera_index_ir_rejected(self, mock_vc_cls):
        # Camera opens, but frames are grayscale/IR
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        ir_frame = np.full((240, 320, 3), 100, dtype=np.uint8)
        mock_cap.read.return_value = (True, ir_frame)
        mock_vc_cls.return_value = mock_cap

        res = probe_camera_index(1)
        self.assertFalse(res["is_color"])
        self.assertEqual(res["status"], "rejected_ir")
        self.assertIn("Rejected infrared/grayscale", res["reason"])

    @patch("cv2.VideoCapture")
    def test_probe_camera_index_color_accepted(self, mock_vc_cls):
        # Camera opens and returns color frames
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        color_frame = np.zeros((240, 320, 3), dtype=np.uint8)
        color_frame[:, :, 0] = 30
        color_frame[:, :, 1] = 150
        color_frame[:, :, 2] = 220
        mock_cap.read.return_value = (True, color_frame)
        mock_vc_cls.return_value = mock_cap

        res = probe_camera_index(2)
        self.assertTrue(res["is_color"])
        self.assertEqual(res["status"], "color")
        self.assertIn("Accepted color stream", res["reason"])

    @patch("engine.camera.camera_capture.probe_camera_index")
    def test_find_best_camera_index_selection(self, mock_probe):
        # Index 0 is busy, index 1 is IR, index 2 is color
        def probe_side_effect(idx, **kwargs):
            if idx == 0:
                return {"index": 0, "status": "unavailable", "reason": "Index 0 busy", "is_color": False}
            elif idx == 1:
                return {"index": 1, "status": "rejected_ir", "reason": "Index 1 IR rejected", "is_color": False}
            elif idx == 2:
                return {"index": 2, "status": "color", "reason": "Index 2 Color accepted", "is_color": True}
            return {"index": idx, "status": "unavailable", "reason": f"Index {idx} missing", "is_color": False}

        mock_probe.side_effect = probe_side_effect
        best_idx, reason, probe_results = find_best_camera_index(max_indices=4)
        self.assertEqual(best_idx, 2)
        self.assertEqual(len(probe_results), 4)

    def test_config_override_reading(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "engine.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"camera_index": 3}, f)

            override = get_config_override_index(cfg_path)
            self.assertEqual(override, 3)

            best, reason, probes = find_best_camera_index(config_path=cfg_path)
            self.assertEqual(best, 3)
            self.assertIn("Override from engine.json", reason)


class TestCameraReopenAndBackoff(unittest.TestCase):
    """Validates reopen, exponential backoff, and warmup frame discarding."""

    @patch("cv2.VideoCapture")
    def test_warmup_frames_discarded(self, mock_vc_cls):
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        frame = np.full((100, 100, 3), 120, dtype=np.uint8)
        mock_cap.read.return_value = (True, frame)
        mock_vc_cls.return_value = mock_cap

        cam = CameraCapture(camera_index=0, warm_up_frames=5)
        res = cam.start()
        self.assertTrue(res)
        # Should have read at least 5 warmup frames
        self.assertGreaterEqual(mock_cap.read.call_count, 5)
        cam.stop()

    def test_camera_reopen_success_on_retry(self):
        cam = CameraCapture(camera_index=0)
        cam.stop = MagicMock()
        
        # Start fails on attempt 1, succeeds on attempt 2
        cam.start = MagicMock(side_effect=[False, True])

        success = cam.reopen(tries=3, backoff_base=0.01)
        self.assertTrue(success)
        self.assertEqual(cam.start.call_count, 2)
        self.assertEqual(cam.stop.call_count, 1)

    def test_camera_reopen_exhausted(self):
        cam = CameraCapture(camera_index=0)
        cam.stop = MagicMock()
        cam.start = MagicMock(return_value=False)

        success = cam.reopen(tries=3, backoff_base=0.01)
        self.assertFalse(success)
        self.assertEqual(cam.start.call_count, 3)


class TestPowerWatcher(unittest.TestCase):
    """Validates power state watcher sleep/resume detection."""

    def test_simulate_resume_flags_cleared(self):
        watcher = PowerStateWatcher(check_interval=1.0, sleep_threshold=3.5)
        self.assertFalse(watcher.check_and_clear_resume())

        watcher.simulate_resume()
        self.assertTrue(watcher.check_and_clear_resume())
        # Second call must be False (cleared flag)
        self.assertFalse(watcher.check_and_clear_resume())


class MockFailingCamera:
    """Mock camera that fails reads after N frames and supports reopen."""

    def __init__(self, fail_after: int = 1, reopen_succeeds: bool = True):
        self.fail_after = fail_after
        self.reopen_succeeds = reopen_succeeds
        self.read_count = 0
        self.reopen_count = 0
        self.is_opened = True

    def start(self):
        self.is_opened = True
        return True

    def stop(self):
        self.is_opened = False

    def read_frame(self):
        self.read_count += 1
        if self.read_count > self.fail_after:
            return False, None
        return True, np.zeros((480, 640, 3), dtype=np.uint8)

    def reopen(self, tries: int = 3, backoff_base: float = 0.01):
        self.reopen_count += 1
        if self.reopen_succeeds:
            self.read_count = 0
            self.is_opened = True
            return True
        self.is_opened = False
        return False


class TestPipeServerRecoveryAndIsolation(unittest.TestCase):
    """
    Validates:
    - 3 consecutive read failures trigger reopen with backoff.
    - Reopen exhaustion emits ERROR (Camera feed lost).
    - Per-session exception does not kill the pipe server.
    """

    def setUp(self):
        self.pipe_name = f"\\\\.\\pipe\\PeekTestRecovery_{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        if hasattr(self, "server") and self.server:
            self.server.stop()

    def test_camera_consecutive_failure_reopen_exhaustion(self):
        # Camera fails immediately, reopen fails -> emits REASON_CAMERA_ERROR
        failing_camera = MockFailingCamera(fail_after=0, reopen_succeeds=False)
        self.server = PipeServer(pipe_name=self.pipe_name, camera=failing_camera)
        self.server.start()
        time.sleep(0.15)

        client = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(client.connect(timeout_seconds=2.0))

        # Start auth session
        client.send_message(make_start_auth())

        error_received = False
        start_t = time.time()
        while time.time() - start_t < 4.0:
            msg = client.read_message()
            if not msg:
                continue
            if msg.get("type") == MSG_ERROR:
                error_received = True
                self.assertEqual(msg.get("reason_code"), REASON_CAMERA_ERROR)
                break

        client.close()
        self.assertTrue(error_received, "Server should emit MSG_ERROR with REASON_CAMERA_ERROR on camera loss")

    def test_per_session_exception_does_not_stop_server(self):
        """
        An unhandled exception during session execution must NOT terminate
        the server. Subsequent clients must connect and communicate successfully.
        """
        mock_cam = MagicMock()
        mock_cam.start.return_value = True
        mock_cam.is_opened = True
        mock_cam.read_frame.side_effect = RuntimeError("Catastrophic hardware fault simulation")

        self.server = PipeServer(pipe_name=self.pipe_name, camera=mock_cam)
        self.server.start()
        time.sleep(0.15)

        # Client 1 triggers exception
        client1 = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(client1.connect(timeout_seconds=2.0))
        client1.send_message(make_start_auth())
        time.sleep(0.3)
        client1.close()

        # Verify server is still alive!
        self.assertTrue(self.server.is_running())

        # Client 2 connects fresh and pings
        client2 = PipeClient(pipe_name=self.pipe_name)
        self.assertTrue(client2.connect(timeout_seconds=2.0), "Server must accept new connections after previous session crashed")
        # Read greeting ENGINE_READY message first
        greeting = client2.read_message()
        self.assertIsNotNone(greeting)

        client2.send_message(make_ping())
        resp = client2.read_message()
        self.assertIsNotNone(resp)
        self.assertEqual(resp.get("type"), MSG_PONG)
        client2.close()


class TestPerfReportAnalysis(unittest.TestCase):
    """Validates latency parsing, p50/p95 calculations, and CSV reports."""

    def test_percentile_calculations(self):
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(calculate_percentile(data, 50.0), 30.0)
        self.assertEqual(calculate_percentile(data, 0.0), 10.0)
        self.assertEqual(calculate_percentile(data, 100.0), 50.0)

    def test_parse_latency_logs_and_analyze(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            eng_log = os.path.join(tmpdir, "engine.log")
            cp_log = os.path.join(tmpdir, "cp.log")

            # Write simulated engine latency lines
            with open(eng_log, "w", encoding="utf-8") as f:
                f.write(
                    "[2026-11-08 12:00:00,000] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=session start timestamp_ms=1000\n"
                    "[2026-11-08 12:00:00,050] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=camera open timestamp_ms=1050\n"
                    "[2026-11-08 12:00:00,100] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=first frame timestamp_ms=1100\n"
                    "[2026-11-08 12:00:00,120] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=per-stage inference time timestamp_ms=1120 detect_ms=15.00 track_pose_ms=1.00 align_ms=2.00 embed_ms=12.00 match_ms=0.50 temporal_ms=0.20 liveness_ms=3.00 total_inference_ms=33.70 faces_seen=1\n"
                    "[2026-11-08 12:00:00,122] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=first face timestamp_ms=1122\n"
                    "[2026-11-08 12:00:00,300] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=temporal confirmed timestamp_ms=1300\n"
                    "[2026-11-08 12:00:00,350] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=liveness passed timestamp_ms=1350\n"
                    "[2026-11-08 12:00:00,360] [INFO] [Peek.PipeServer]: [LATENCY] session_id=sess_1 stage=AUTHENTICATED timestamp_ms=1360\n"
                )

            # Write simulated CP latency lines
            with open(cp_log, "w", encoding="utf-8") as f:
                f.write(
                    "[2026-11-08 11:59:59.950] [INFO] [LATENCY] session_id=sess_1 stage=SetSelected timestamp_ms=950\n"
                    "[2026-11-08 11:59:59.980] [INFO] [LATENCY] session_id=sess_1 stage=pipe connected timestamp_ms=980\n"
                    "[2026-11-08 12:00:00.060] [INFO] [LATENCY] session_id=sess_1 stage=first state timestamp_ms=1060\n"
                    "[2026-11-08 12:00:00.365] [INFO] [LATENCY] session_id=sess_1 stage=AUTHENTICATED timestamp_ms=1365\n"
                    "[2026-11-08 12:00:00.380] [INFO] [LATENCY] session_id=sess_1 stage=GetSerialization done timestamp_ms=1380\n"
                )

            sessions = parse_latency_logs(eng_log, cp_log)
            self.assertEqual(len(sessions), 1)
            self.assertIn("sess_1", sessions)
            sess = sessions["sess_1"]
            self.assertEqual(sess.face_category, "1 face")
            self.assertTrue(sess.authenticated)

            analysis = analyze_sessions(sessions)
            self.assertEqual(analysis["counts"]["Overall"], 1)
            self.assertEqual(analysis["counts"]["1 face"], 1)

            # Check deltas
            stages = analysis["stages"]["1 face"]
            self.assertEqual(stages["CP: SetSelected -> pipe connected"]["p50"], 30.0) # 980 - 950
            self.assertEqual(stages["Engine: session start -> camera open"]["p50"], 50.0) # 1050 - 1000
            self.assertEqual(stages["Engine End-to-End (session start -> AUTH)"]["p50"], 360.0) # 1360 - 1000
            self.assertEqual(stages["Full End-to-End (SetSelected -> Serialization)"]["p50"], 430.0) # 1380 - 950

            # Check inference
            inf = analysis["inference"]["1 face"]
            self.assertEqual(inf["detect_ms"]["p50"], 15.0)
            self.assertEqual(inf["total_inference_ms"]["p50"], 33.7)

            # Test export to CSV
            out_csv = os.path.join(tmpdir, "report.csv")
            export_report_csv(analysis, None, out_csv)
            self.assertTrue(os.path.isfile(out_csv))
            with open(out_csv, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("Full End-to-End (SetSelected -> Serialization)", content)


class TestFatalErrorExitHandling(unittest.TestCase):
    """Validates that fatal engine errors exit non-zero and log critical traceback."""

    def test_unhandled_exception_handler_exits_code_1(self):
        with self.assertRaises(SystemExit) as ctx:
            try:
                raise RuntimeError("Simulated fatal engine startup failure")
            except RuntimeError:
                exc_type, exc_val, exc_tb = sys.exc_info()
                with patch("logging.Logger.critical") as mock_crit:
                    unhandled_exception_handler(exc_type, exc_val, exc_tb)
                    mock_crit.assert_called()
        self.assertEqual(ctx.exception.code, 1)

    def test_unhandled_exception_handler_ignores_keyboard_interrupt(self):
        # KeyboardInterrupt should delegate without forced sys.exit(1)
        with patch("sys.__excepthook__") as mock_hook:
            unhandled_exception_handler(KeyboardInterrupt, KeyboardInterrupt(), None)
            mock_hook.assert_called_once()


if __name__ == "__main__":
    unittest.main()
