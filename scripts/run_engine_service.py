"""
Peek Face Engine - Background Service Entrypoint
Starts FaceEngine and Named Pipe IPC servers (Auth & Guided Enrollment) as a long-running standalone process or Windows Service.
Can be run under low-privilege virtual service account NT SERVICE\\PeekEngine or standalone console.

Usage:
    python scripts/run_engine_service.py [--pipe-name \\\\.\\pipe\\PeekEngine] [--enrollment-pipe-name \\\\.\\pipe\\PeekEnrollment] [--debug] [--service] [--console]
"""

import os
import sys
import time
import signal
import logging
import json
from logging.handlers import RotatingFileHandler
import argparse

# In windowless GUI / background service mode (e.g. PyInstaller console=False),
# sys.stdout and sys.stderr may be None. Protect against NoneType write errors.
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine._version import __version__
from engine.pipeline.face_engine import FaceEngine
from storage.template_store import SecureProfileStore, secure_admin_only_path
from engine.ipc.pipe_server import PipeServer, DEFAULT_PIPE_NAME, DEFAULT_SDDL
from engine.ipc.enrollment_pipe_server import EnrollmentPipeServer, DEFAULT_ENROLLMENT_PIPE_NAME
from engine.ipc.session_coordinator import SessionCoordinator

logger = logging.getLogger("Peek.EngineService")

try:
    import win32service
    import win32serviceutil
    import win32event
    import servicemanager
    import win32process
    import win32api
    import win32security
    HAS_WIN32SERVICE = True
except ImportError:
    HAS_WIN32SERVICE = False


def unhandled_exception_handler(exc_type, exc_value, exc_traceback):
    """Catches any uncaught fatal exception, logs traceback, flushes logs, and exits non-zero."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("FATAL: Unhandled fatal exception in engine process:", exc_info=(exc_type, exc_value, exc_traceback))
    for handler in logging.root.handlers:
        try:
            handler.flush()
        except Exception:
            pass
    sys.exit(1)


sys.excepthook = unhandled_exception_handler


def setup_logging(debug: bool = False):
    """Configures structured service logging to stdout/stderr and file if log directory exists."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    handlers = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))

    # Persistent file logging in %ProgramData%\Peek\logs
    log_dirs = [
        os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "logs"),
        os.path.join(os.environ.get("TEMP", r"C:\Temp"), "Peek", "logs")
    ]
    for ld in log_dirs:
        try:
            if not os.path.exists(ld):
                os.makedirs(ld, exist_ok=True)
                secure_admin_only_path(ld, is_directory=True)
            log_file = os.path.join(ld, "engine.log")
            handlers.append(RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"))
            break
        except Exception:
            continue

    if handlers:
        logging.basicConfig(level=log_level, format=log_format, handlers=handlers)
    else:
        logging.basicConfig(level=log_level, format=log_format)


def log_pipe_owner() -> dict:
    """Logs the single pipe owner identity, SID, PID, and session ID."""
    pid = os.getpid()
    session_id = "unknown"
    user_name = "unknown"
    user_sid = "unknown"

    if HAS_WIN32SERVICE:
        try:
            session_id = win32process.ProcessIdToSessionId(pid)
        except Exception:
            session_id = "unknown"
        try:
            user_name = win32api.GetUserName()
        except Exception:
            user_name = os.environ.get("USERNAME", "unknown")
        try:
            token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
            user_sid_obj, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            user_sid = win32security.ConvertSidToStringSid(user_sid_obj)
            win32api.CloseHandle(token)
        except Exception:
            user_sid = "unknown"
    else:
        user_name = os.environ.get("USERNAME", "unknown")

    logger.info(
        f"[PIPE OWNER] Single owner defined: User='{user_name}', SID='{user_sid}', PID={pid}, SessionID={session_id}"
    )
    return {
        "user_name": user_name,
        "user_sid": user_sid,
        "pid": pid,
        "session_id": session_id,
    }


def probe_and_document_camera(session_id) -> dict:
    """
    Documents the camera result per machine (session 0 or not) to
    %ProgramData%\\Peek\\state\\camera_status.json and logs fallback to password sign-in.
    """
    is_session_0 = (session_id == 0 or session_id == "0")
    camera_available = False
    camera_backend = "unknown"
    camera_error = ""

    try:
        import cv2
        cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
        if cap and cap.isOpened():
            camera_available = True
            camera_backend = "CAP_MSMF"
            cap.release()
        else:
            if cap:
                cap.release()
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if cap and cap.isOpened():
                camera_available = True
                camera_backend = "CAP_DSHOW"
                cap.release()
            else:
                if cap:
                    cap.release()
                camera_error = "Unable to open video capture device index 0"
    except Exception as ex:
        camera_error = str(ex)

    state_dir = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Peek", "state")
    camera_status_path = os.path.join(state_dir, "camera_status.json")

    status_data = {
        "machine": os.environ.get("COMPUTERNAME", "unknown"),
        "session_id": session_id,
        "is_session_0": is_session_0,
        "camera_available": camera_available,
        "camera_backend": camera_backend,
        "error": camera_error,
        "timestamp": time.time(),
    }

    try:
        os.makedirs(state_dir, exist_ok=True)
        secure_admin_only_path(state_dir, is_directory=True)
        with open(camera_status_path, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2)
        secure_admin_only_path(camera_status_path, is_directory=False)
    except Exception as ex:
        logger.warning(f"Failed to record camera status file: {ex}")

    if camera_available:
        logger.info(
            f"[CAMERA RESULT] Camera ready (Backend: {camera_backend}, Session: {session_id}, Session0: {is_session_0})"
        )
    else:
        logger.warning(
            f"[CAMERA RESULT] Camera UNAVAILABLE (Session: {session_id}, Session0: {is_session_0}, Error: {camera_error}). "
            f"Falling back cleanly to standard password sign-in."
        )

    return status_data


def run_engine_service(
    pipe_name: str = DEFAULT_PIPE_NAME,
    enrollment_pipe_name: str = DEFAULT_ENROLLMENT_PIPE_NAME,
    timeout: float = 12.0,
    require_challenge: bool = False,
    stop_event=None,
):
    """Core engine execution loop for both service and standalone runs."""
    owner_info = log_pipe_owner()
    session_id = owner_info.get("session_id", "unknown")
    probe_and_document_camera(session_id)

    profile_store = SecureProfileStore()
    profiles = profile_store.list_profiles()
    active_profile = next((p for p in profiles if p.enabled), None)

    if active_profile:
        logger.info(f"Loaded active profile: '{active_profile.display_name}' ({len(active_profile.templates)} templates)")
    else:
        logger.warning("No enabled biometric profile found in storage. Enrolled user required to unlock.")

    engine = FaceEngine(
        profile_store=profile_store,
        match_threshold=0.48,
        require_active_challenge=require_challenge
    )

    coordinator = SessionCoordinator()

    auth_server = PipeServer(
        pipe_name=pipe_name,
        face_engine=engine,
        default_auth_timeout=timeout,
        sddl=DEFAULT_SDDL,
        coordinator=coordinator,
    )

    enrollment_server = EnrollmentPipeServer(
        pipe_name=enrollment_pipe_name,
        coordinator=coordinator,
        profile_store=profile_store,
        face_engine=engine,
    )

    def handle_signal(sig, frame):
        logger.info(f"Signal {sig} received; shutting down engine services...")
        auth_server.stop()
        enrollment_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(f"Listening on Auth Pipe: {pipe_name} (SDDL: {auth_server.sddl})")
    logger.info(f"Listening on Enrollment Pipe: {enrollment_pipe_name} (SDDL: {enrollment_server.sddl})")
    logger.info(f"Services ready.")

    try:
        auth_server.start()
        enrollment_server.start()
    except Exception as ex:
        logger.critical(f"FATAL: Engine failed to start IPC servers: {ex}", exc_info=True)
        sys.exit(1)

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                logger.info("Service stop event signaled; terminating...")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received; stopping engine services...")
    finally:
        auth_server.stop()
        enrollment_server.stop()


if HAS_WIN32SERVICE:
    class PeekEngineWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = "PeekEngine"
        _svc_display_name_ = "Peek Face Recognition Engine"
        _svc_description_ = "Background face recognition engine and IPC pipe server for Peek biometric unlock."

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._engine_stop_event = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            run_engine_service(stop_event=self.stop_event)


def main():
    parser = argparse.ArgumentParser(description="Peek Face Engine - Windows Named Pipe Service")
    parser.add_argument(
        "--version",
        action="version",
        version=f"PeekEngine {__version__}",
        help="Show engine version and exit"
    )
    parser.add_argument(
        "--pipe-name",
        type=str,
        default=DEFAULT_PIPE_NAME,
        help=f"Auth named pipe URI (default: {DEFAULT_PIPE_NAME})"
    )
    parser.add_argument(
        "--enrollment-pipe-name",
        type=str,
        default=DEFAULT_ENROLLMENT_PIPE_NAME,
        help=f"Enrollment named pipe URI (default: {DEFAULT_ENROLLMENT_PIPE_NAME})"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=12.0,
        help="Default authentication timeout in seconds (default: 12.0)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging"
    )
    parser.add_argument(
        "--require-challenge",
        action="store_true",
        default=False,
        help="Require active interactive challenge gestures before unlocking (default: False)"
    )
    parser.add_argument(
        "--service",
        action="store_true",
        help="Run as Windows Service (SCM dispatched)"
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Force standalone interactive console mode"
    )
    args = parser.parse_args()

    setup_logging(args.debug)

    print("=" * 65)
    print(f"   PEEK — FACE RECOGNITION ENGINE SERVICE v{__version__} (Named Pipe IPC)")
    print("=" * 65)

    if args.service and HAS_WIN32SERVICE:
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(PeekEngineWindowsService)
            servicemanager.StartServiceCtrlDispatcher()
            return
        except win32service.error:
            # Fall back to console if not started by SCM
            pass

    run_engine_service(
        pipe_name=args.pipe_name,
        enrollment_pipe_name=args.enrollment_pipe_name,
        timeout=args.timeout,
        require_challenge=args.require_challenge,
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as ex:
        logger.critical(f"FATAL: Unhandled exception in main: {ex}", exc_info=True)
        for handler in logging.root.handlers:
            try:
                handler.flush()
            except Exception:
                pass
        sys.exit(1)
