"""
Peek — Phase 3 Verification Script (Recognition Hardening)
Validates dominant-face tracking, bystander hysteresis, rolling temporal confirmation,
and multi-template scoring.
"""

import os
import sys
import numpy as np
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.tracking.face_tracker import FaceTracker, compute_iou
from engine.temporal.temporal_verifier import TemporalVerifier
from engine.recognition.matcher import FaceMatcher
from engine.recognition.calibrator import ThresholdCalibrator
from engine.detection.scrfd_detector import FaceDetection

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.VerifyPhase3")


def run_verification():
    logger.info("==================================================")
    logger.info("   PEEK — PHASE 3 RECOGNITION HARDENING VERIFICATION")
    logger.info("==================================================")

    # 1. Multi-Face Tracking & Hysteresis Test
    logger.info("1. Validating dominant-face tracking & bystander hysteresis...")
    tracker = FaceTracker(min_hits_to_confirm=2, max_missing_frames=4, hysteresis_area_margin=1.25)
    dummy_kps = np.zeros((5, 2))

    # User sits in front of laptop (center, box [150, 80, 310, 260], area = 160 x 180 = 28,800)
    user_bbox = np.array([150.0, 80.0, 310.0, 260.0])
    dom, all_t = tracker.update([FaceDetection(bbox=user_bbox, confidence=0.96, landmarks=dummy_kps)])
    assert dom is not None
    user_id = dom.track_id
    logger.info(f"   Primary user detected: Track ID #{user_id} (area: {dom.area:.0f})")

    # Frame 2: Primary user tracked again
    user_bbox_f2 = user_bbox + np.array([3.0, 2.0, 3.0, 2.0])
    dom, _ = tracker.update([FaceDetection(bbox=user_bbox_f2, confidence=0.96, landmarks=dummy_kps)])
    assert dom.track_id == user_id and dom.is_confirmed
    logger.info(f"   Primary user confirmed: Track ID #{dom.track_id} (hits: {dom.hits})")

    # Frame 3-5: Bystander walks into background (box [400, 70, 565, 255], area = 165 x 185 = 30,525, ~6% larger)
    bystander_bbox = np.array([400.0, 70.0, 565.0, 255.0])
    for frame_no in range(3, 6):
        user_bbox_fn = user_bbox + np.array([frame_no * 2.0, 0, frame_no * 2.0, 0])
        dom, all_t = tracker.update([
            FaceDetection(bbox=user_bbox_fn, confidence=0.95, landmarks=dummy_kps),
            FaceDetection(bbox=bystander_bbox, confidence=0.92, landmarks=dummy_kps)
        ])
        assert len(all_t) == 2, f"Expected 2 tracked faces, got {len(all_t)}"
        assert dom.track_id == user_id, f"Hysteresis failed: switched from #{user_id} to #{dom.track_id}"
        logger.info(f"   Frame {frame_no}: 2 faces visible -> Active dominant target remains #{dom.track_id} (bystander #{all_t[1].track_id} ignored)")

    logger.info("✓ Target stickiness verified: Tracker did NOT oscillate or jump to bystander.")

    # 2. Rolling Temporal Verification Window Test
    logger.info("2. Validating rolling temporal verification window (M-of-N confirmation)...")
    verifier = TemporalVerifier(window_size=7, min_matches_required=5, ema_alpha=0.35)

    # Simulate intermittent frames
    logger.info("   Feeding frame stream to temporal verifier:")
    scores = [0.88, 0.91, 0.85, 0.89, 0.92]
    for idx, sc in enumerate(scores, 1):
        res = verifier.update(track_id=user_id, is_frame_match=True, frame_confidence=sc)
        logger.info(f"   Sample {idx}: confidence={sc:.2f} -> Temporal Window: {res.positive_matches_in_window}/{res.required_matches} (confirmed={res.is_temporally_confirmed})")
        if idx < 5:
            assert not res.is_temporally_confirmed, "Triggered confirmation prematurely!"

    assert res.is_temporally_confirmed is True, "Expected temporal confirmation after 5 matching frames!"
    logger.info(f"   ✓ Temporal confirmation achieved at 5th frame. Smoothed Score: {res.smoothed_confidence:.3f}")

    # Security Constraint #1
    assert res.is_authorized_to_unlock is False, "Security constraint #1 violated!"
    logger.info("✓ Security constraint #1 verified: Even confirmed temporal match CANNOT authorize unlock.")

    # Simulate target lost -> window resets
    logger.info("   Simulating target loss / person leaving frame...")
    res_lost = verifier.update(track_id=None, is_frame_match=False, frame_confidence=0.0)
    assert res_lost.is_temporally_confirmed is False
    assert res_lost.positive_matches_in_window == 0
    logger.info("✓ Temporal window resets immediately on target loss.")

    # 3. Multi-Template & Pose-Affinity Scoring Test
    logger.info("3. Validating multi-template scoring with pose affinity...")
    matcher = FaceMatcher(default_threshold=0.48)

    # 9 multi-angle templates
    templates = []
    poses = ["CENTER", "LEFT", "RIGHT", "UP", "DOWN", "UPPER_LEFT", "UPPER_RIGHT", "LOWER_LEFT", "LOWER_RIGHT"]
    for i in range(9):
        vec = np.random.randn(512).astype(np.float32)
        vec /= np.linalg.norm(vec)
        templates.append(vec)

    # Query with noise matching LEFT template
    left_vec = templates[1]
    noise = np.random.randn(512).astype(np.float32)
    noise /= np.linalg.norm(noise)
    query_left = left_vec + noise * 0.12
    query_left /= np.linalg.norm(query_left)

    match_res = matcher.match(
        live_embedding=query_left,
        enrolled_templates=templates,
        template_poses=poses,
        estimated_pose="LEFT"
    )

    logger.info(f"   Best Template Match: idx={match_res.best_template_idx} ({match_res.best_template_pose})")
    logger.info(f"   Similarity: {match_res.confidence:.4f} (Top-3 Mean: {match_res.top3_mean:.4f})")
    assert match_res.is_match is True
    assert match_res.best_template_pose == "LEFT"
    logger.info("✓ Multi-template matching accurately identified matching pose template.")

    # 4. Threshold Calibration
    logger.info("4. Running threshold calibration metrics...")
    calibrator = ThresholdCalibrator()
    genuine_pairs = []
    impostor_pairs = []

    for _ in range(50):
        base = np.random.randn(512).astype(np.float32)
        base /= np.linalg.norm(base)
        
        n = np.random.randn(512).astype(np.float32)
        n /= np.linalg.norm(n)
        gen = base + n * 0.20
        gen /= np.linalg.norm(gen)
        genuine_pairs.append((base, gen))

        imp = np.random.randn(512).astype(np.float32)
        imp /= np.linalg.norm(imp)
        impostor_pairs.append((base, imp))

    report = calibrator.evaluate(genuine_pairs, impostor_pairs)
    logger.info(f"   Calibrated Genuine Mean:  {report.genuine_mean:.4f} (±{report.genuine_std:.3f})")
    logger.info(f"   Calibrated Impostor Mean: {report.impostor_mean:.4f} (±{report.impostor_std:.3f})")
    logger.info(f"   Recommended Operating Threshold: {report.recommended_threshold:.2f}")
    logger.info(f"   FAR at threshold: {report.far_at_recommended * 100:.2f}% | FRR: {report.frr_at_recommended * 100:.2f}%")
    assert report.genuine_mean > 0.80
    assert report.impostor_mean < 0.15
    logger.info("✓ Threshold calibration establishes clean margin between genuine and impostor scores.")

    logger.info("==================================================")
    logger.info("   ALL PHASE 3 VERIFICATIONS PASSED SUCCESSFULLY! ")
    logger.info("==================================================")


if __name__ == "__main__":
    run_verification()
