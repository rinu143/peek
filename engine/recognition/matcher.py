"""
Peek Face Engine - Similarity Matcher
Layer B: Cosine similarity comparison between live face embedding and stored templates.

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
    all_scores: List[float]
    # SECURITY FLAG: Explicitly denotes that biometric similarity alone cannot authorize unlock.
    # Liveness must be independently confirmed.
    is_authorized_to_unlock: bool = False
    details: str = ""


class FaceMatcher:
    """
    Compares 512-dimensional L2-normalized embeddings using cosine similarity (dot product).
    """

    def __init__(self, default_threshold: float = 0.50):
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
        threshold: Optional[float] = None
    ) -> MatchResult:
        """
        Matches a live embedding against a list of enrolled template embeddings.
        
        Args:
            live_embedding: (512,) float32 vector.
            enrolled_templates: List of (512,) float32 template vectors.
            threshold: Optional override for matching threshold.
            
        Returns:
            MatchResult containing match decision, confidence score, and security metadata.
        """
        if not enrolled_templates:
            return MatchResult(
                is_match=False,
                confidence=0.0,
                best_template_idx=None,
                all_scores=[],
                is_authorized_to_unlock=False,
                details="No enrolled templates available."
            )

        active_threshold = threshold if threshold is not None else self.default_threshold
        
        # Calculate cosine similarity against all enrolled templates
        scores = [self.compute_similarity(live_embedding, tmpl) for tmpl in enrolled_templates]
        best_idx = int(np.argmax(scores))
        best_score = scores[best_idx]

        is_match = best_score >= active_threshold

        # NOTE (SECURITY CONSTRAINT #1):
        # Even if is_match is True, is_authorized_to_unlock is ALWAYS False here.
        # Face matching is only one gate. Liveness evaluation is a mandatory,
        # independent gate that must be verified separately in the authentication pipeline.
        return MatchResult(
            is_match=is_match,
            confidence=best_score,
            best_template_idx=best_idx,
            all_scores=scores,
            is_authorized_to_unlock=False,  # Enforce security constraint #1
            details=f"Best score {best_score:.4f} against template {best_idx} (threshold: {active_threshold:.2f})"
        )
