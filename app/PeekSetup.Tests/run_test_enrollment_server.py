"""
Transient test enrollment pipe server for C# integration testing.
Runs on \\\\.\\pipe\\PeekEnrollmentTest with a mock runner.
"""
import sys
import os
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.ipc.enrollment_pipe_server import EnrollmentPipeServer
from tests.test_enrollment_pipe_server import MockEnrollmentRunner

def main():
    pipe_name = r"\\.\pipe\PeekEnrollmentTest"
    runner = MockEnrollmentRunner(frames_to_complete=3, camera_available=True)
    server = EnrollmentPipeServer(pipe_name=pipe_name, runner=runner)
    server.start()
    print("READY", flush=True)

    try:
        # Keep alive up to 10 seconds for the test client
        start = time.time()
        while time.time() - start < 10:
            if runner.is_complete or runner._cancelled:
                time.sleep(0.5)
                break
            time.sleep(0.1)
    finally:
        server.stop()
        print("STOPPED", flush=True)

if __name__ == "__main__":
    main()
