"""
Peek Face Engine - Session Coordinator
Manages mutual exclusivity between Authentication and Enrollment sessions.
Ensures that auth and enrollment do not fight over camera hardware or concurrent access.
"""

import threading
import logging

logger = logging.getLogger("Peek.SessionCoordinator")


class SessionCoordinator:
    """
    Coordinates access to camera and biometric operations across multiple IPC pipes.
    Ensures that Authentication and Enrollment sessions are strictly mutually exclusive.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._auth_active = False
        self._enrollment_active = False

    def is_auth_active(self) -> bool:
        """Returns True if an authentication session is currently running."""
        with self._lock:
            return self._auth_active

    def is_enrollment_active(self) -> bool:
        """Returns True if an enrollment session is currently running."""
        with self._lock:
            return self._enrollment_active

    def acquire_auth(self) -> bool:
        """
        Attempts to acquire the lock for an authentication session.
        Fails if an enrollment session or another auth session is active.
        """
        with self._lock:
            if self._enrollment_active or self._auth_active:
                logger.warning(
                    "Cannot start authentication: enrollment_active=%s, auth_active=%s",
                    self._enrollment_active,
                    self._auth_active,
                )
                return False
            self._auth_active = True
            logger.info("Authentication session lock acquired.")
            return True

    def release_auth(self):
        """Releases the authentication session lock."""
        with self._lock:
            self._auth_active = False
            logger.info("Authentication session lock released.")

    def acquire_enrollment(self) -> bool:
        """
        Attempts to acquire the lock for an enrollment session.
        Fails if an authentication session or another enrollment session is active.
        """
        with self._lock:
            if self._auth_active or self._enrollment_active:
                logger.warning(
                    "Cannot start enrollment: auth_active=%s, enrollment_active=%s",
                    self._auth_active,
                    self._enrollment_active,
                )
                return False
            self._enrollment_active = True
            logger.info("Enrollment session lock acquired.")
            return True

    def release_enrollment(self):
        """Releases the enrollment session lock."""
        with self._lock:
            self._enrollment_active = False
            logger.info("Enrollment session lock released.")
