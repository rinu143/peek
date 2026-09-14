"""
Peek Face Engine - Camera Capture
Layer A: Windows camera capture via Media Foundation with graceful degradation.
"""

import cv2
import logging
from typing import Optional, Tuple

logger = logging.getLogger("Peek.Camera")

class CameraCapture:
    """
    Manages camera acquisition using Windows Media Foundation (CAP_MSMF).
    Provides resilient error handling when camera is busy, disconnected, or denied.
    """

    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480):
        self.camera_index = camera_index
        self.target_width = width
        self.target_height = height
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_opened = False
        self.error_message: Optional[str] = None

    def start(self) -> bool:
        """
        Attempts to start the camera using MSMF, falling back to default backend.
        Returns True if successful, False if unavailable.
        """
        logger.info(f"Opening camera index {self.camera_index} via Media Foundation (MSMF)...")
        try:
            # Prefer Windows Media Foundation backend
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_MSMF)
            if not self.cap or not self.cap.isOpened():
                logger.warning("MSMF backend failed to open camera; falling back to CAP_ANY...")
                if self.cap:
                    self.cap.release()
                self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_ANY)

            if not self.cap or not self.cap.isOpened():
                self.error_message = f"Camera {self.camera_index} could not be opened (in use or not found)."
                logger.error(self.error_message)
                self.is_opened = False
                return False

            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
            self.is_opened = True
            self.error_message = None
            logger.info("Camera successfully initialized.")
            return True

        except Exception as ex:
            self.error_message = f"Unexpected error opening camera: {ex}"
            logger.error(self.error_message)
            self.is_opened = False
            return False

    def read_frame(self) -> Tuple[bool, Optional[any]]:
        """
        Reads a single frame from the camera.
        Returns (success: bool, frame: np.ndarray or None).
        """
        if not self.is_opened or self.cap is None:
            return False, None

        ret, frame = self.cap.read()
        if not ret or frame is None:
            logger.warning("Camera failed to deliver a frame (disconnected or in use).")
            return False, None

        return True, frame

    def stop(self):
        """Releases the camera hardware."""
        if self.cap is not None:
            logger.info("Releasing camera capture.")
            self.cap.release()
            self.cap = None
        self.is_opened = False
