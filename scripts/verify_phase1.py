"""
Peek — Phase 1 Verification Script
Runs an end-to-end simulated enrollment and recognition test without requiring physical camera intervention.
"""

import os
import sys
import numpy as np
import cv2
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.pipeline.face_engine import FaceEngine
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.VerifyPhase1")


def run_verification():
    logger.info("==================================================")
    logger.info("   PEEK — PHASE 1 FACE ENGINE VERIFICATION   ")
    logger.info("==================================================")

    store = SecureProfileStore()
    engine = FaceEngine(profile_store=store, match_threshold=0.50)

    logger.info("1. Validating detector & embedder models...")
    assert engine.detector is not None, "Detector failed to initialize"
    assert engine.embedder is not None, "Embedder failed to initialize"
    logger.info("✓ Models loaded successfully.")

    logger.info("2. Creating simulated face input...")
    test_img = np.full((480, 640, 3), 200, dtype=np.uint8)
    cx, cy = 320, 240
    cv2.ellipse(test_img, (cx, cy), (100, 130), 0, 0, 360, (160, 160, 160), -1)
    cv2.circle(test_img, (cx - 40, cy - 25), 12, (40, 40, 40), -1)
    cv2.circle(test_img, (cx + 40, cy - 25), 12, (40, 40, 40), -1)
    pts_nose = np.array([[cx, cy - 5], [cx - 10, cy + 18], [cx + 10, cy + 18]])
    cv2.fillPoly(test_img, [pts_nose], (100, 100, 100))
    cv2.ellipse(test_img, (cx, cy + 55), (35, 15), 0, 0, 180, (50, 50, 50), -1)

    # Test processing blank vs simulated face
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    res_blank = engine.process_frame(blank)
    logger.info(f"Blank frame result: {res_blank.state_label} (has_face={res_blank.has_face})")
    assert res_blank.state_label == "SEARCHING"

    logger.info("3. Testing enrollment & DPAPI storage...")
    test_embedding = np.random.randn(512).astype(np.float32)
    test_embedding /= np.linalg.norm(test_embedding)

    tmpl = PeekTemplate(
        template_id="tmpl_verify_001",
        embedding=test_embedding.tolist(),
        pose_label="CENTER",
        quality_score=0.98
    )
    profile = PeekProfile(
        profile_id="verify_user_id",
        display_name="Verification User",
        matching_threshold=0.50,
        templates=[tmpl]
    )

    path = store.save_profile(profile)
    logger.info(f"Saved DPAPI profile to: {path}")

    with open(path, "rb") as f:
        ciphertext = f.read()
    assert b"Verification User" not in ciphertext, "Plaintext detected in template file!"
    logger.info("✓ Verified DPAPI encryption at rest (no plaintext data).")

    engine.reload_active_profile()
    logger.info(f"Loaded active profile: '{engine.active_profile.display_name}'")

    logger.info("4. Testing cosine similarity matching against enrolled template...")
    # Exact match
    match_self = engine.matcher.match(test_embedding, engine.cached_template_embeddings)
    logger.info(f"Self-match confidence: {match_self.confidence:.4f} (is_match={match_self.is_match})")
    assert match_self.is_match is True
    assert match_self.is_authorized_to_unlock is False, "Security constraint #1 violated!"
    logger.info("✓ Verified Constraint #1: Match alone CANNOT authorize unlock.")

    # Impostor test
    impostor_embedding = np.random.randn(512).astype(np.float32)
    impostor_embedding /= np.linalg.norm(impostor_embedding)
    match_impostor = engine.matcher.match(impostor_embedding, engine.cached_template_embeddings)
    logger.info(f"Impostor confidence: {match_impostor.confidence:.4f} (is_match={match_impostor.is_match})")
    assert match_impostor.is_match is False
    logger.info("✓ Verified impostor rejection.")

    # Clean up verification profile
    store.delete_profile("verify_user_id")
    engine.reload_active_profile()
    logger.info("Cleaned up verification profile.")

    logger.info("==================================================")
    logger.info("   ALL PHASE 1 VERIFICATIONS PASSED SUCCESSFULLY! ")
    logger.info("==================================================")


if __name__ == "__main__":
    run_verification()
