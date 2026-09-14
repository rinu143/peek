"""
Peek Face Engine - Head Pose Estimator
Layer B / Layer D: Landmark-based head pose estimation and 9-direction pose classification.

Analyzes 5-point facial landmarks to calculate yaw, pitch, and roll,
driving the guided enrollment system by measured facial geometry rather than user guesswork.
"""

import numpy as np
from enum import Enum
from dataclasses import dataclass
from typing import Tuple, Optional

class HeadPose(str, Enum):
    CENTER = "CENTER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UP = "UP"
    DOWN = "DOWN"
    UPPER_LEFT = "UPPER_LEFT"
    UPPER_RIGHT = "UPPER_RIGHT"
    LOWER_LEFT = "LOWER_LEFT"
    LOWER_RIGHT = "LOWER_RIGHT"
    UNKNOWN = "UNKNOWN"

# Ordered list of poses for guided enrollment progression
ORDERED_ENROLLMENT_POSES = [
    HeadPose.CENTER,
    HeadPose.LEFT,
    HeadPose.RIGHT,
    HeadPose.UP,
    HeadPose.DOWN,
    HeadPose.UPPER_LEFT,
    HeadPose.UPPER_RIGHT,
    HeadPose.LOWER_LEFT,
    HeadPose.LOWER_RIGHT,
]

@dataclass
class PoseEstimate:
    pose: HeadPose
    yaw: float          # Negative: Looking Left, Positive: Looking Right
    pitch: float        # Negative: Looking Up, Positive: Looking Down
    roll: float         # Degrees rotation around optical axis
    target_match_score: float = 0.0
    guidance: str = ""


class HeadPoseEstimator:
    """
    Estimates 3D head orientation from 5 facial keypoints:
    [left_eye, right_eye, nose_tip, left_mouth_corner, right_mouth_corner].
    """

    def __init__(
        self,
        yaw_threshold: float = 0.14,
        pitch_threshold: float = 0.08,
        roll_limit: float = 35.0
    ):
        self.yaw_threshold = yaw_threshold
        self.pitch_threshold = pitch_threshold
        self.roll_limit = roll_limit

    def estimate(self, landmarks: np.ndarray) -> PoseEstimate:
        """
        Calculates yaw, pitch, roll, and classifies the primary head pose.
        
        Args:
            landmarks: (5, 2) numpy array of facial keypoints.
            
        Returns:
            PoseEstimate containing classified pose and angular ratios.
        """
        le, re, nose, lm, rm = landmarks[:5]

        eye_midpoint = (le + re) / 2.0
        mouth_midpoint = (lm + rm) / 2.0
        
        interpupillary_dist = float(np.linalg.norm(re - le))
        face_height = float(np.linalg.norm(mouth_midpoint - eye_midpoint))
        
        if interpupillary_dist < 1e-4 or face_height < 1e-4:
            return PoseEstimate(pose=HeadPose.UNKNOWN, yaw=0.0, pitch=0.0, roll=0.0, guidance="Face geometry invalid")

        # Roll: angle between eyes in degrees
        dx = re[0] - le[0]
        dy = re[1] - le[1]
        roll = float(np.degrees(np.arctan2(dy, dx)))

        # Yaw ratio: nose x displacement from eye center normalized by half interpupillary distance
        yaw = float((nose[0] - eye_midpoint[0]) / (interpupillary_dist / 2.0))

        # Pitch ratio: nose y position along eye-to-mouth vector. Neutral baseline is ~0.49
        pitch = float((nose[1] - eye_midpoint[1]) / face_height - 0.49)

        # Classify into one of 9 directions
        is_left = yaw < -self.yaw_threshold
        is_right = yaw > self.yaw_threshold
        is_up = pitch < -self.pitch_threshold
        is_down = pitch > self.pitch_threshold

        if is_up and is_left:
            classified = HeadPose.UPPER_LEFT
        elif is_up and is_right:
            classified = HeadPose.UPPER_RIGHT
        elif is_down and is_left:
            classified = HeadPose.LOWER_LEFT
        elif is_down and is_right:
            classified = HeadPose.LOWER_RIGHT
        elif is_left:
            classified = HeadPose.LEFT
        elif is_right:
            classified = HeadPose.RIGHT
        elif is_up:
            classified = HeadPose.UP
        elif is_down:
            classified = HeadPose.DOWN
        else:
            classified = HeadPose.CENTER

        return PoseEstimate(
            pose=classified,
            yaw=yaw,
            pitch=pitch,
            roll=roll,
            target_match_score=0.0,
            guidance=""
        )

    def evaluate_against_target(
        self,
        landmarks: np.ndarray,
        target_pose: HeadPose,
        mirrored: bool = False
    ) -> Tuple[bool, float, str]:
        """
        Evaluates whether current landmarks match the expected target pose.
        
        Args:
            landmarks: (5, 2) facial keypoints.
            target_pose: Desired HeadPose.
            mirrored: True only if feeding un-flipped raw frames where user-left is camera-right.
                      Defaults to False (standard for flipped/mirrored webcam feeds).
            
        Returns:
            (is_match: bool, score: float [0.0 - 1.0], guidance: str)
        """
        estimate = self.estimate(landmarks)
        yaw = -estimate.yaw if mirrored else estimate.yaw
        pitch = estimate.pitch

        if abs(estimate.roll) > self.roll_limit:
            return False, 0.0, "Please keep head upright (excessive tilt)"

        # Target expected directions
        target_yaw_dir = 0
        target_pitch_dir = 0

        if target_pose in (HeadPose.LEFT, HeadPose.UPPER_LEFT, HeadPose.LOWER_LEFT):
            target_yaw_dir = -1
        elif target_pose in (HeadPose.RIGHT, HeadPose.UPPER_RIGHT, HeadPose.LOWER_RIGHT):
            target_yaw_dir = 1

        if target_pose in (HeadPose.UP, HeadPose.UPPER_LEFT, HeadPose.UPPER_RIGHT):
            target_pitch_dir = -1
        elif target_pose in (HeadPose.DOWN, HeadPose.LOWER_LEFT, HeadPose.LOWER_RIGHT):
            target_pitch_dir = 1

        # Check yaw alignment
        yaw_ok = False
        yaw_score = 0.0
        if target_yaw_dir == 0:
            if abs(yaw) <= self.yaw_threshold:
                yaw_ok = True
                yaw_score = 1.0 - (abs(yaw) / self.yaw_threshold)
            else:
                guidance_yaw = "Turn head back to center"
        elif target_yaw_dir < 0:
            if yaw <= -self.yaw_threshold * 0.8:
                yaw_ok = True
                yaw_score = min(1.0, abs(yaw) / self.yaw_threshold)
            else:
                guidance_yaw = "Turn head more to your left"
        else:
            if yaw >= self.yaw_threshold * 0.8:
                yaw_ok = True
                yaw_score = min(1.0, abs(yaw) / self.yaw_threshold)
            else:
                guidance_yaw = "Turn head more to your right"

        # Check pitch alignment
        pitch_ok = False
        pitch_score = 0.0
        if target_pitch_dir == 0:
            if abs(pitch) <= self.pitch_threshold * 1.2:
                pitch_ok = True
                pitch_score = 1.0 - (abs(pitch) / (self.pitch_threshold * 1.2))
            else:
                guidance_pitch = "Tilt head level"
        elif target_pitch_dir < 0:
            if pitch <= -self.pitch_threshold * 0.8:
                pitch_ok = True
                pitch_score = min(1.0, abs(pitch) / self.pitch_threshold)
            else:
                guidance_pitch = "Tilt head slightly up"
        else:
            if pitch >= self.pitch_threshold * 0.8:
                pitch_ok = True
                pitch_score = min(1.0, abs(pitch) / self.pitch_threshold)
            else:
                guidance_pitch = "Tilt head slightly down"

        is_match = yaw_ok and pitch_ok
        composite_score = float((yaw_score + pitch_score) / 2.0)

        if is_match:
            guidance = "Hold pose steady..."
        elif not yaw_ok:
            guidance = guidance_yaw
        else:
            guidance = guidance_pitch

        return is_match, composite_score, guidance
