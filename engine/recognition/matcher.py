"""
Peek Face Engine - Similarity Matcher
Layer B: Multi-template cosine similarity comparison and scoring calibration.

NON-NEGOTIABLE SECURITY CONSTRAINT:
Face match alone can NEVER unlock the system.
Match decisions produced here are purely biometric similarity scores.
Liveness must be independently evaluated and satisfied before an unlock can be authorized.
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional

@dataclass
class MatchResult:
    is_match: bool
    confidence: float
    best_template_idx: Optional[int]
    best_template_pose: Optional[str]
    top3_mean: float
    all_scores: List[float]
    # SECURITY FLAG: Explicitly denotes that biometric similarity alone cannot authorize unlock.
    # Liveness must be independently confirmed.
    is_authorized_to_unlock: bool = False
    details: str = ""


class FaceMatcher:
    """
    Compares 512-dimensional L2-normalized embeddings using cosine similarity (dot product).
    Supports multi-template scoring, top-K averaging, and pose-affinity calibration.
    """

    def __init__(self, default_threshold: float = 0.48):
        self.default_threshold = default_threshold

    @staticmethod
    def compute_similarity(embedding_a: np.ndarray, embedding_b: np.ndarray) -> float:
        """
        Computes cosine similarity between two unit vectors: dot(a, b).
        Clamped to [-1.0, 1.0].
        """
        dot = float(np.dot(embedding_a, embedding_b))
        return max(-1.0, min(1.0, dot))

    def match(
        self,
        live_embedding: np.ndarray,
        enrolled_templates: List[np.ndarray],
        template_poses: Optional[List[str]] = None,
        estimated_pose: Optional[str] = None,
        threshold: Optional[float] = None
    ) -> MatchResult:
        """
        Matches a live embedding against enrolled multi-angle template embeddings.
        
        Args:
            live_embedding: (512,) float32 vector.
            enrolled_templates: List of (512,) float32 template vectors.
            template_poses: Optional list of pose labels corresponding to templates.
            estimated_pose: Optional estimated pose of live face (e.g. "LEFT", "CENTER").
            threshold: Optional override for matching threshold.
            
        Returns:
            MatchResult containing match decision, confidence score, and security metadata.
        """
        if not enrolled_templates:
            return MatchResult(
                is_match=False,
                confidence=0.0,
                best_template_idx=None,
                best_template_pose=None,
                top3_mean=0.0,
                all_scores=[],
                is_authorized_to_unlock=False,
                details="No enrolled templates available."
            )

        active_threshold = threshold if threshold is not None else self.default_threshold
        
        # Calculate raw cosine similarity against all enrolled templates
        scores = [self.compute_similarity(live_embedding, tmpl) for tmpl in enrolled_templates]
        
        # Top-K mean
        sorted_scores = sorted(scores, reverse=True)
        top_k = min(3, len(sorted_scores))
        top3_mean = float(np.mean(sorted_scores[:top_k]))

        best_idx = int(np.argmax(scores))
        best_score = scores[best_idx]
        best_pose = template_poses[best_idx] if template_poses and best_idx < len(template_poses) else None

        # Pose affinity bonus: if live face orientation matches enrolled pose template
        effective_score = best_score
        if estimated_pose and best_pose and estimated_pose == best_pose:
            # Small bonus for consistent 3D angle alignment
            effective_score = min(1.0, best_score * 1.02)

        is_match = effective_score >= active_threshold

        # NOTE (SECURITY CONSTRAINT #1):
        # Even if is_match is True, is_authorized_to_unlock is ALWAYS False here.
        # Face matching is only one gate. Liveness evaluation is a mandatory,
        # independent gate that must be verified separately in the authentication pipeline.
        pose_info = f" [{best_pose}]" if best_pose else ""
        return MatchResult(
            is_match=is_match,
            confidence=effective_score,
            best_template_idx=best_idx,
            best_template_pose=best_pose,
            top3_mean=top3_mean,
            all_scores=scores,
            is_authorized_to_unlock=False,  # Enforce security constraint #1
            details=f"Best score {effective_score:.4f}{pose_info} (top-3: {top3_mean:.4f}, thr: {active_threshold:.2f})"
        )
