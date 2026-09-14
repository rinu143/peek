"""
Peek Face Engine - Face Alignment
Layer B: Landmark-based similarity transform (Umeyama algorithm)
to align faces to canonical 112x112 ArcFace geometry.
"""

import cv2
import numpy as np
from typing import Tuple

# Canonical 5 facial landmarks for 112x112 ArcFace model:
# 1. Left eye
# 2. Right eye
# 3. Nose tip
# 4. Left mouth corner
# 5. Right mouth corner
ARCFACE_CANONICAL_5PTS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041]
], dtype=np.float32)


def estimate_similarity_transform(src_pts: np.ndarray, dst_pts: np.ndarray) -> np.ndarray:
    """
    Computes optimal similarity transform (scale, rotation, translation) from src_pts to dst_pts
    using Umeyama's algorithm.
    
    Args:
        src_pts: (N, 2) array of detected facial landmarks.
        dst_pts: (N, 2) array of canonical reference landmarks.
        
    Returns:
        M: 2x3 affine transformation matrix suitable for cv2.warpAffine.
    """
    src = np.asarray(src_pts, dtype=np.float64)
    dst = np.asarray(dst_pts, dtype=np.float64)
    
    num_pts, dim = src.shape
    if num_pts < 3:
        raise ValueError("At least 3 points are required to compute similarity transform.")

    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)

    src_centered = src - src_mean
    dst_centered = dst - dst_mean

    src_var = np.mean(np.sum(src_centered ** 2, axis=1))
    if src_var < 1e-8:
        # Avoid division by zero for degenerate points
        return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)

    cov = (dst_centered.T @ src_centered) / num_pts

    u, d, vt = np.linalg.svd(cov)
    s = np.eye(dim, dtype=np.float64)
    
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s[dim - 1, dim - 1] = -1.0

    r = u @ s @ vt
    scale = np.sum(d * np.diag(s)) / src_var
    trans = dst_mean - scale * (r @ src_mean)

    # Construct 2x3 affine matrix
    m = np.zeros((2, 3), dtype=np.float32)
    m[:, :2] = scale * r
    m[:, 2] = trans
    return m


class FaceAligner:
    """
    Aligns detected facial landmarks to canonical 112x112 ArcFace crop.
    """

    def __init__(self, target_size: Tuple[int, int] = (112, 112)):
        self.target_size = target_size
        self.reference_pts = ARCFACE_CANONICAL_5PTS

    def align(self, image: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        """
        Aligns the face crop in image according to the 5 landmarks.
        
        Args:
            image: Full original BGR image.
            landmarks: (5, 2) detected landmarks.
            
        Returns:
            aligned_face: (112, 112, 3) BGR aligned image.
        """
        transform_matrix = estimate_similarity_transform(landmarks, self.reference_pts)
        aligned = cv2.warpAffine(
            image,
            transform_matrix,
            self.target_size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0.0
        )
        return aligned
