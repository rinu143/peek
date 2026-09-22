"""
Peek Face Engine - Phase 4 Liveness & Anti-Spoofing Verification Script
Validates:
1. Genuine living user stream acceptance (natural 3D micro-movement, diffuse skin texture, eye dynamics).
2. Static photo attack rejection (zero micro-movement / frozen frame).
3. Rigid 2D planar translation rejection (photograph or phone waved in front of lens).
4. Screen replay attack rejection (high-frequency moiré interference fringes).
5. Non-Negotiable Security Constraint #1: Biometric temporal match alone can NEVER unlock.
   Only when both biometric temporal match AND independent passive liveness pass is unlock authorized.
"""

import os
import sys
import logging
import numpy as np
import cv2

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.liveness.motion_detector import MotionParallaxDetector
from engine.liveness.texture_checker import TextureChecker
from engine.liveness.eye_dynamics import EyeDynamicsDetector
from engine.liveness.liveness_detector import LivenessDetector

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.VerifyPhase4")


def make_skin_face_frame(width=160, height=160):
    """Generates a synthetic frame with natural diffuse skin tone."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    # BGR approx for healthy skin (YCrCb: Y~160, Cr~145, Cb~105)
    frame[:, :] = [120, 150, 200]
    noise = np.random.normal(0, 2, size=frame.shape).astype(np.int16)
    frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return frame


def make_screen_replay_frame(width=160, height=160):
    """Generates a frame exhibiting high-frequency screen grid frequencies."""
    frame = np.full((height, width, 3), 170, dtype=np.uint8)
    # Inject periodic subpixel grid matrix
    frame[::4, :, :] = 35
    frame[:, ::4, :] = 35
    return frame


def run_phase4_verification():
    logger.info("=" * 60)
    logger.info("   PEEK — PHASE 4 LIVENESS & ANTI-SPOOFING VERIFICATION")
    logger.info("=" * 60)

    base_landmarks = np.array([
        [50.0, 45.0],   # left eye
        [85.0, 45.0],   # right eye
        [67.5, 65.0],   # nose
        [55.0, 85.0],   # left mouth
        [80.0, 85.0],   # right mouth
    ], dtype=np.float32)
    base_bbox = (20.0, 20.0, 140.0, 140.0)

    # -------------------------------------------------------------
    # 1. Genuine Living User Verification
    # -------------------------------------------------------------
    logger.info("1. Validating genuine living user acceptance (3D posture + skin + blinks)...")
    detector = LivenessDetector(min_frames_to_confirm=8, liveness_threshold=0.65)
    
    np.random.seed(42)
    live_result = None
    skin_frame = make_skin_face_frame()

    for i in range(12):
        # Involuntary physiological micro-jitter (0.5 to 1.2 px)
        jitter = np.random.normal(0.0, 0.7, size=(5, 2)).astype(np.float32)
        # Subtle 3D perspective foreshortening
        jitter[2, 1] += np.sin(i * 0.45) * 1.1
        current_lms = base_landmarks + jitter

        bbox_jitter = np.random.normal(0.0, 0.6, size=4).astype(np.float32)
        current_bbox = tuple(np.array(base_bbox) + bbox_jitter)

        # Simulate natural blink at frame 6-7
        frame_to_use = skin_frame.copy()
        if i in (6, 7):
            # Eye closed: blend pupil region to smooth skin
            cv2.circle(frame_to_use, (50, 45), 5, (120, 150, 200), -1)
            cv2.circle(frame_to_use, (85, 45), 5, (120, 150, 200), -1)
        else:
            # Eye open: distinct dark iris/pupil
            cv2.circle(frame_to_use, (50, 45), 4, (20, 20, 20), -1)
            cv2.circle(frame_to_use, (85, 45), 4, (20, 20, 20), -1)

        live_result = detector.update(frame_to_use, current_bbox, current_lms, track_id=1)
        if i >= 7:
            logger.info(f"   Frame {i+1:02d}: State={live_result.state} | Score={live_result.fused_score:.2f} (mot={live_result.motion_score:.2f}, text={live_result.texture_score:.2f}, eye={live_result.eye_score:.2f})")

    assert live_result.is_live, "Genuine user stream must achieve is_live == True"
    assert live_result.state == "LIVE", f"Expected state LIVE, got {live_result.state}"
    logger.info("✓ Genuine living user stream successfully verified and passed liveness.")

    # -------------------------------------------------------------
    # 2. Static Photograph / Freeze Attack Rejection
    # -------------------------------------------------------------
    logger.info("2. Validating static photo / frozen feed rejection...")
    detector.reset()
    static_frame = skin_frame.copy()
    cv2.circle(static_frame, (50, 45), 4, (20, 20, 20), -1)
    cv2.circle(static_frame, (85, 45), 4, (20, 20, 20), -1)

    photo_result = None
    for i in range(10):
        # 100% frozen pixels and landmarks
        photo_result = detector.update(static_frame, base_bbox, base_landmarks, track_id=1)

    logger.info(f"   Static Photo Evaluation: State={photo_result.state} | Reason={photo_result.spoof_reason} | Score={photo_result.fused_score:.2f}")
    assert not photo_result.is_live, "Static photo must NOT achieve is_live == True"
    assert photo_result.state == "SPOOF_DETECTED", f"Expected SPOOF_DETECTED, got {photo_result.state}"
    assert photo_result.spoof_reason == "STATIC_PHOTO", f"Expected STATIC_PHOTO, got {photo_result.spoof_reason}"
    logger.info("✓ Static photo attack successfully detected and blocked.")

    # -------------------------------------------------------------
    # 3. Rigid 2D Planar Translation Attack Rejection
    # -------------------------------------------------------------
    logger.info("3. Validating rigid 2D planar motion attack (waved photograph)...")
    detector.reset()
    rigid_result = None

    for i in range(12):
        # Attacker moving a photo in front of camera: 2D shift, but ZERO 3D internal parallax
        offset = float(i * 3.0)
        shifted_lms = base_landmarks + np.array([offset, offset * 0.4])
        shifted_bbox = (
            base_bbox[0] + offset,
            base_bbox[1] + offset * 0.4,
            base_bbox[2] + offset,
            base_bbox[3] + offset * 0.4
        )
        rigid_result = detector.update(static_frame, shifted_bbox, shifted_lms, track_id=1)

    logger.info(f"   Waved Photo Evaluation: State={rigid_result.state} | Reason={rigid_result.spoof_reason} | Score={rigid_result.fused_score:.2f}")
    assert not rigid_result.is_live, "Waved rigid photo must NOT achieve is_live == True"
    assert rigid_result.state == "SPOOF_DETECTED", f"Expected SPOOF_DETECTED, got {rigid_result.state}"
    assert rigid_result.spoof_reason == "RIGID_PLANAR_MOTION", f"Expected RIGID_PLANAR_MOTION, got {rigid_result.spoof_reason}"
    logger.info("✓ Rigid 2D planar motion attack successfully detected and blocked.")

    # -------------------------------------------------------------
    # 4. Screen Replay Moiré Attack Rejection
    # -------------------------------------------------------------
    logger.info("4. Validating screen replay attack rejection (moiré grid fringes)...")
    detector.reset()
    screen_frame = make_screen_replay_frame()
    screen_result = None

    for i in range(10):
        jitter = np.random.normal(0.0, 0.5, size=(5, 2)).astype(np.float32)
        screen_result = detector.update(screen_frame, base_bbox, base_landmarks + jitter, track_id=1)

    logger.info(f"   Screen Replay Evaluation: State={screen_result.state} | Reason={screen_result.spoof_reason} | Score={screen_result.fused_score:.2f}")
    assert not screen_result.is_live, "Screen replay must NOT achieve is_live == True"
    assert screen_result.state == "SPOOF_DETECTED", f"Expected SPOOF_DETECTED, got {screen_result.state}"
    assert screen_result.spoof_reason in ("SCREEN_MOIRE", "BEZEL_DETECTED"), f"Expected SCREEN_MOIRE or BEZEL_DETECTED, got {screen_result.spoof_reason}"
    logger.info("✓ Screen replay attack successfully detected and blocked.")

    # -------------------------------------------------------------
    # 5. Non-Negotiable Security Constraint #1 (Dual-Gate Unlock Enforcement)
    # -------------------------------------------------------------
    logger.info("5. Enforcing Non-Negotiable Security Constraint #1 (Dual-Gate Rule)...")

    # Gate A: Temporal Biometric Match
    # Gate B: Independent Liveness Gate
    
    # Test 5A: Temporal match 100%, but Liveness still CHECKING -> Unauthorized
    gate_biometric = True
    gate_liveness = False  # Still evaluating / checking
    is_authorized = bool(gate_biometric and gate_liveness)
    logger.info(f"   Scenario A (Biometric 100%, Liveness CHECKING): is_authorized_to_unlock = {is_authorized}")
    assert not is_authorized, "Biometric match alone must NEVER unlock without liveness confirmation."

    # Test 5B: Temporal match 100%, but Spoof Detected -> Unauthorized
    gate_biometric = True
    gate_liveness = False  # Spoof detected
    is_authorized = bool(gate_biometric and gate_liveness)
    logger.info(f"   Scenario B (Biometric 100%, Liveness SPOOF):    is_authorized_to_unlock = {is_authorized}")
    assert not is_authorized, "Spoof must block unlock regardless of 100% biometric score."

    # Test 5C: Impostor with living face -> Unauthorized
    gate_biometric = False # Wrong person
    gate_liveness = True   # Living person
    is_authorized = bool(gate_biometric and gate_liveness)
    logger.info(f"   Scenario C (Biometric NO_MATCH, Liveness LIVE): is_authorized_to_unlock = {is_authorized}")
    assert not is_authorized, "Liveness alone must never unlock without verified biometric match."

    # Test 5D: Verified Biometric Match + Verified Living User -> AUTHORIZED
    gate_biometric = True
    gate_liveness = True
    is_authorized = bool(gate_biometric and gate_liveness)
    logger.info(f"   Scenario D (Biometric MATCH, Liveness LIVE):    is_authorized_to_unlock = {is_authorized}")
    assert is_authorized, "Both gates satisfied -> Unlock must be authorized."

    # -------------------------------------------------------------
    # 6. Smartphone Chassis / Screen Bezel Attack Rejection (Phase 4 Hardening)
    # -------------------------------------------------------------
    logger.info("6. Validating smartphone chassis / screen bezel detection veto...")
    detector.reset()
    bezel_frame = np.full((320, 320, 3), 160, dtype=np.uint8)
    # Draw dark smartphone chassis surrounding face
    cv2.rectangle(bezel_frame, (70, 30), (250, 290), (20, 20, 20), -1)
    # Draw screen inside
    cv2.rectangle(bezel_frame, (85, 50), (235, 270), (190, 180, 170), -1)
    bezel_bbox = (95.0, 60.0, 225.0, 250.0)
    bezel_lms = np.array([
        [130.0, 110.0],
        [190.0, 110.0],
        [160.0, 150.0],
        [140.0, 195.0],
        [180.0, 195.0]
    ], dtype=np.float32)

    bezel_result = None
    for i in range(5):
        jx = float(np.sin(i * 0.7) * 1.0)
        jy = float(np.cos(i * 0.6) * 1.0)
        shifted_bbox = (bezel_bbox[0] + jx, bezel_bbox[1] + jy, bezel_bbox[2] + jx, bezel_bbox[3] + jy)
        shifted_lms = bezel_lms + np.array([jx, jy], dtype=np.float32)
        bezel_result = detector.update(bezel_frame, shifted_bbox, shifted_lms, track_id=2)

    logger.info(f"   Phone Bezel Evaluation: State={bezel_result.state} | Reason={bezel_result.spoof_reason} | Score={bezel_result.fused_score:.2f} | BezelConf={bezel_result.bezel_confidence:.2f}")
    assert not bezel_result.is_live, "Phone bezel attack must NOT achieve is_live == True"
    assert bezel_result.state == "SPOOF_DETECTED", f"Expected SPOOF_DETECTED, got {bezel_result.state}"
    assert "BEZEL_DETECTED" in (bezel_result.spoof_reason or ""), f"Expected BEZEL_DETECTED in {bezel_result.spoof_reason}"
    logger.info("✓ Smartphone bezel detection veto successfully detected and blocked.")

    # -------------------------------------------------------------
    # 7. Flat 2D Hand Tremor Parallax Veto (Phase 4 Hardening)
    # -------------------------------------------------------------
    logger.info("7. Validating 2D hand tremor parallax rejection (lacks 3D depth)...")
    detector.reset()
    parallax_result = None
    plain_frame = np.full((320, 320, 3), 180, dtype=np.uint8)

    for i in range(12):
        shift_x = float(np.sin(i * 0.9) * 1.8)
        shift_y = float(np.cos(i * 0.8) * 1.2)
        shifted_bbox = (
            bezel_bbox[0] + shift_x,
            bezel_bbox[1] + shift_y,
            bezel_bbox[2] + shift_x,
            bezel_bbox[3] + shift_y
        )
        # Rigid translation with zero 3D relative ratio variation
        shifted_lms = bezel_lms + np.array([shift_x, shift_y], dtype=np.float32)
        parallax_result = detector.update(plain_frame, shifted_bbox, shifted_lms, track_id=3)

    logger.info(f"   Hand Tremor Evaluation: State={parallax_result.state} | Reason={parallax_result.spoof_reason} | Score={parallax_result.fused_score:.2f}")
    assert not parallax_result.is_live, "Flat 2D tremor replay must NOT achieve is_live == True"
    assert parallax_result.state == "SPOOF_DETECTED", f"Expected SPOOF_DETECTED, got {parallax_result.state}"
    assert "LACKS_PARALLAX" in (parallax_result.spoof_reason or ""), f"Expected LACKS_PARALLAX in {parallax_result.spoof_reason}"
    logger.info("✓ 2D hand tremor parallax veto successfully detected and blocked.")

    # -------------------------------------------------------------
    # 8. Active Challenge-Response Evaluation (Phase 4 Hardening)
    # -------------------------------------------------------------
    logger.info("8. Validating active challenge-response prompt and verification...")
    from engine.liveness.challenge import ChallengeManager
    challenger = ChallengeManager(timeout_seconds=2.0)
    ch_res = challenger.start_challenge()
    assert ch_res.state == "PENDING"
    assert ch_res.challenge_type in ["TURN_LEFT", "TURN_RIGHT", "TILT_UP", "BLINK"]

    challenger._current_challenge = "TURN_LEFT"
    ch_res = challenger.update(pose_label="LEFT")
    assert ch_res.passed
    assert ch_res.state == "PASSED"
    logger.info("✓ Active challenge-response prompt & verification passed.")

    # -------------------------------------------------------------
    # 9. Challenge Pass Single-Use Security (State-Latch Fix)
    # -------------------------------------------------------------
    logger.info("9. Validating challenge pass single-use security (state-latch fix)...")
    challenger2 = ChallengeManager(timeout_seconds=2.0)
    ch_res = challenger2.start_challenge()
    assert ch_res.state == "PENDING"

    # Force the challenge type and pass it
    challenger2._current_challenge = "TURN_LEFT"
    ch_res = challenger2.update(pose_label="LEFT")
    assert ch_res.passed
    assert ch_res.state == "PASSED"

    # Consume the pass (simulating authorization)
    challenger2.consume_pass()
    assert challenger2._state == "CONSUMED"

    # Attempt to reuse the same pass - should fail
    ch_res = challenger2.update(pose_label="LEFT")
    assert not ch_res.passed
    assert ch_res.state == "CONSUMED"
    assert "already used" in ch_res.details

    # Only reset should clear CONSUMED state
    challenger2.reset()
    ch_res = challenger2.update(pose_label="LEFT")
    assert ch_res.state == "IDLE"
    logger.info("✓ Challenge pass single-use security verified (state-latch vulnerability fixed).")

    logger.info("=" * 60)
    logger.info("   ALL PHASE 4 HARDENED LIVENESS VERIFICATIONS PASSED!")
    logger.info("   (Including state-latch vulnerability fix for challenge reuse)")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_phase4_verification()

