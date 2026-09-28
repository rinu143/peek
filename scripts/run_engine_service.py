"""
Peek Face Engine - Background Service Entrypoint
Starts FaceEngine and Named Pipe IPC server as a long-running standalone process.
This entrypoint can be executed directly from the console or wrapped as a Windows Service.

Usage:
    python scripts/run_engine_service.py [--pipe-name \\\\.\\pipe\\PeekEngine] [--debug]
"""

import os
import sys
import time
import signal
import logging
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.pipeline.face_engine import FaceEngine
from storage.template_store import SecureProfileStore
from engine.ipc.pipe_server import PipeServer, DEFAULT_PIPE_NAME, DEFAULT_SDDL

logger = logging.getLogger("Peek.EngineService")


def setup_logging(debug: bool = False):
    """Configures structured service logging."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    logging.basicConfig(level=log_level, format=log_format)


def main():
    parser = argparse.ArgumentParser(description="Peek Face Engine - Windows Named Pipe Service")
    parser.add_argument(
        "--pipe-name",
        type=str,
        default=DEFAULT_PIPE_NAME,
        help=f"Named pipe URI (default: {DEFAULT_PIPE_NAME})"
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
    args = parser.parse_args()

    setup_logging(args.debug)

    print("=" * 65)
    print("   PEEK — FACE RECOGNITION ENGINE SERVICE (Named Pipe IPC)")
    print("=" * 65)
    logger.info(f"Initializing FaceEngine and DPAPI SecureProfileStore...")

    profile_store = SecureProfileStore()
    profiles = profile_store.list_profiles()
    active_profile = next((p for p in profiles if p.enabled), None)

    if active_profile:
        logger.info(f"Loaded active profile: '{active_profile.display_name}' ({len(active_profile.templates)} templates)")
    else:
        logger.warning("No enabled biometric profile found in DPAPI storage. Enrolled user required to unlock.")

    engine = FaceEngine(
        profile_store=profile_store,
        match_threshold=0.48,
        require_active_challenge=True
    )

    server = PipeServer(
        pipe_name=args.pipe_name,
        face_engine=engine,
        default_auth_timeout=args.timeout,
        sddl=DEFAULT_SDDL
    )

    # Register OS signal handlers for graceful shutdown
    def handle_signal(sig, frame):
        logger.info(f"Signal {sig} received; shutting down engine service...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(f"Listening on Named Pipe: {args.pipe_name}")
    logger.info(f"Security Descriptor (SDDL): {DEFAULT_SDDL}")
    logger.info(f"Service ready. Press Ctrl+C to terminate.")

    # Start blocking server loop
    server.serve_forever()


if __name__ == "__main__":
    main()
