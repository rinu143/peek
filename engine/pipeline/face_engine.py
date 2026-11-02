"""
Peek Face Engine - Core Pipeline
Coordinates Camera, Detector, Tracker, Pose Estimator, Aligner, Embedder, Matcher,
Temporal Verifier, and Secure Profile Storage.

NON-NEGOTIABLE SECURITY CONSTRAINTS:
1. Face match alone can never unlock. Even when temporal verification passes 100%,
   is_authorized_to_unlock remains strictly False. Liveness is an independent gate.
2. Raw camera frames are processed in-memory and discarded immediately; only embeddings are stored.
3. Fail open, never fail closed.
"""

import os
import cv2
import time
import numpy as np
import logging
import platform
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from engine.detection.scrfd_detector import SCRFDDetector, FaceDetection
from engine.tracking.face_tracker import FaceTracker, TrackedFace
from engine.pose.pose_estimator import HeadPoseEstimator
from engine.alignment.aligner import FaceAligner
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.recognition.matcher import FaceMatcher, MatchResult
from engine.temporal.temporal_verifier import TemporalVerifier, TemporalVerificationResult
from engine.liveness.liveness_detector import LivenessDetector, LivenessResult
from engine.liveness.challenge import ChallengeManager, ChallengeResult
from engine.quality.quality_checker import FaceQualityChecker, QualityCheckResult
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate

logger = logging.getLogger("Peek.FaceEngine")

def get_current_process_user_sid() -> Optional[str]:
    """Retrieves current Windows user SID or checks PEEK_USER_SID env."""
    sid_env = os.environ.get("PEEK_USER_SID")
    if sid_env:
        return sid_env
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes
            advapi32 = ctypes.windll.advapi32
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
            advapi32.OpenProcessToken.restype = wintypes.BOOL
            advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            advapi32.GetTokenInformation.restype = wintypes.BOOL
            advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
            advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

            token = wintypes.HANDLE()
            if advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
                try:
                    cb = wintypes.DWORD(0)
                    advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(cb))
                    buf = ctypes.create_string_buffer(cb.value)
                    if advapi32.GetTokenInformation(token, 1, buf, cb.value, ctypes.byref(cb)):
                        pSid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                        pStr = wintypes.LPWSTR()
                        if advapi32.ConvertSidToStringSidW(pSid, ctypes.byref(pStr)):
                            sid_val = pStr.value
                            kernel32.LocalFree(pStr)
                            return sid_val
                finally:
                    kernel32.CloseHandle(token)
        except Exception as ex:
            logger.debug(f"Process SID resolution error: {ex}")
    return None

def get_authorized_identities_from_registry(user_sid: str) -> Optional[List[str]]:
    """
    Reads HKLM\\SOFTWARE\\Peek\\Identities\\<user_sid> value "Authorized".
    Returns List[str] (capped at 5) if key exists; None if key does not exist (legacy v1 install).
    """
    if not user_sid or platform.system() != "Windows":
        return None
    try:
        import winreg
        key_path = rf"SOFTWARE\Peek\Identities\{user_sid}"
        access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
            try:
                val, _ = winreg.QueryValueEx(key, "Authorized")
                if isinstance(val, list):
                    return [x for x in val if x][:5]
                elif isinstance(val, str) and val:
                    return [val][:5]
                return []
            except FileNotFoundError:
                return []
    except FileNotFoundError:
        # Key does not exist -> legacy v1 install!
        return None
    except Exception as ex:
        logger.debug(f"Failed to read authorized identities registry key: {ex}")
        return None

@dataclass
class EngineFrameResult:
    has_face: bool
    detection: Optional[FaceDetection] = None
    track_id: Optional[int] = None
    all_tracks: List[TrackedFace] = field(default_factory=list)
    embedding: Optional[np.ndarray] = None
    match_result: Optional[MatchResult] = None
    temporal_result: Optional[TemporalVerificationResult] = None
    is_temporally_confirmed: bool = False
    liveness_result: Optional[LivenessResult] = None
    is_live: bool = False
    estimated_pose: Optional[str] = None
    challenge_result: Optional[ChallengeResult] = None
    state_label: str = "SEARCHING"
    # SECURITY FLAG: True ONLY when both biometric match AND independent liveness pass
    is_authorized_to_unlock: bool = False
    matched_profile_id: Optional[str] = None
    matched_profile_name: Optional[str] = None
    details: str = ""


class FaceEngine:
    """
    Unified Face Recognition Engine for Peek with persistent tracking,
    temporal verification, and independent multi-cue liveness gating.

    require_active_challenge:
        When False (default), unlock is granted immediately once biometric matching
        and passive liveness pass, without issuing interactive head-turn/blink challenges.
        Set True only if explicit interactive challenge-response is required.
    """

    def __init__(
        self,
        detector_model_path: Optional[str] = None,
        embedder_model_path: Optional[str] = None,
        profile_store: Optional[SecureProfileStore] = None,
        match_threshold: float = 0.48,
        temporal_window_size: int = 7,
        temporal_min_matches: int = 5,
        liveness_min_frames: int = 8,
        liveness_threshold: float = 0.58,
        require_active_challenge: bool = False,
        quality_checker: Optional[FaceQualityChecker] = None,
        challenge_escalation_score_band: Tuple[float, float] = (0.45, 0.75),
        challenge_escalation_bezel_threshold: float = 0.25,
        challenge_escalation_face_fill_ratio: float = 0.70,
        challenge_grace_seconds: float = 45.0
    ):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, "models")
        
        det_path = detector_model_path or os.path.join(models_dir, "scrfd_500m_bnkps.onnx")
        emb_path = embedder_model_path or os.path.join(models_dir, "w600k_mbf.onnx")

        if not os.path.exists(det_path) or not os.path.exists(emb_path):
            from engine.models.download_models import ensure_models_exist
            logger.info("ONNX models missing; triggering automated download...")
            det_path, emb_path = ensure_models_exist()

        logger.info(f"Loading SCRFD Detector from: {det_path}")
        self.detector = SCRFDDetector(det_path, conf_threshold=0.5, nms_threshold=0.4)

        logger.info(f"Loading ArcFace Embedder from: {emb_path}")
        self.embedder = ArcFaceEmbedder(emb_path)

        self.tracker = FaceTracker(min_hits_to_confirm=2, max_missing_frames=2)
        self.pose_estimator = HeadPoseEstimator()
        self.aligner = FaceAligner(target_size=(112, 112))
        self.matcher = FaceMatcher(default_threshold=match_threshold)
        self.temporal_verifier = TemporalVerifier(
            window_size=temporal_window_size,
            min_matches_required=temporal_min_matches
        )
        self.liveness_detector = LivenessDetector(
            min_frames_to_confirm=liveness_min_frames,
            liveness_threshold=liveness_threshold
        )
        self.quality_checker = quality_checker or FaceQualityChecker()
        self._last_liveness_result: Optional[LivenessResult] = None
        self.require_active_challenge = require_active_challenge
        self._session_force_active_challenge = False
        self.challenge_manager = ChallengeManager(timeout_seconds=3.5, max_frames=70)
        self._challenge_passed_track_id: Optional[int] = None
        self.profile_store = profile_store or SecureProfileStore()

        # Risk-based challenge escalation parameters
        self.challenge_escalation_score_band = challenge_escalation_score_band
        self.challenge_escalation_bezel_threshold = challenge_escalation_bezel_threshold
        self.challenge_escalation_face_fill_ratio = challenge_escalation_face_fill_ratio
        self.challenge_grace_seconds = challenge_grace_seconds

        # Grace window tracking for low-friction repeat unlocks
        self._last_full_verification_time: Optional[float] = None
        self._last_full_verification_track_id: Optional[int] = None
        
        # In-memory enrolled templates cache for fast real-time matching
        self.active_profile: Optional[PeekProfile] = None
        self.enabled_profiles: List[PeekProfile] = []
        self._template_profile_map: List[PeekProfile] = []
        self.cached_template_embeddings: List[np.ndarray] = []
        self.cached_template_poses: List[str] = []
        self.reload_active_profile()

    def reload_active_profile(self, user_sid: Optional[str] = None):
        """
        Loads enabled profiles from DPAPI storage and caches template embeddings.
        REQUIREMENT 5a:
        Load only profiles that are enabled AND on the Authorized list for the current user SID.
        If the Identities key does not exist (legacy v1 install) keep old behavior (load enabled profiles).
        """
        target_sid = user_sid or get_current_process_user_sid()
        authorized_ids = get_authorized_identities_from_registry(target_sid) if target_sid else None

        profiles = self.profile_store.list_profiles()
        if authorized_ids is not None:
            auth_set = set(authorized_ids[:5])
            self.enabled_profiles = [p for p in profiles if p.enabled and p.profile_id in auth_set]
            logger.info(f"Loaded {len(self.enabled_profiles)} authorized profile(s) for SID {target_sid}")
        else:
            self.enabled_profiles = [p for p in profiles if p.enabled]
            logger.info(f"Legacy v1 install: Loaded {len(self.enabled_profiles)} enabled profile(s)")
        
        if self.enabled_profiles:
            self.active_profile = self.enabled_profiles[0]
            self.cached_template_embeddings = []
            self.cached_template_poses = []
            self._template_profile_map = []
            for prof in self.enabled_profiles:
                for t in prof.templates:
                    self.cached_template_embeddings.append(np.array(t.embedding, dtype=np.float32))
                    self.cached_template_poses.append(t.pose_label)
                    self._template_profile_map.append(prof)
            logger.info(f"Loaded {len(self.enabled_profiles)} active profile(s) with {len(self.cached_template_embeddings)} total templates.")
        else:
            self.active_profile = None
            self.enabled_profiles = []
            self.cached_template_embeddings = []
            self.cached_template_poses = []
            self._template_profile_map = []
            logger.info("No active profile enrolled.")

        self.temporal_verifier.reset()
        self.liveness_detector.reset()
        self.challenge_manager.reset()
        self._challenge_passed_track_id = None
        self._last_full_verification_time = None
        self._last_full_verification_track_id = None
        self._last_liveness_result = None

    def set_active_profile(self, profile: PeekProfile):
        """Sets active profile in memory."""
        self.active_profile = profile
        self.enabled_profiles = [profile]
        self.cached_template_embeddings = [
            np.array(t.embedding, dtype=np.float32) for t in profile.templates
        ]
        self.cached_template_poses = [t.pose_label for t in profile.templates]
        self._template_profile_map = [profile for _ in profile.templates]
        self.temporal_verifier.reset()
        self.liveness_detector.reset()
        self.challenge_manager.reset()
        self._challenge_passed_track_id = None
        self._last_full_verification_time = None
        self._last_full_verification_track_id = None
        self._last_liveness_result = None

    def _is_challenge_required(self) -> bool:
        """
        Returns True if active challenge-response mode is enabled.
        Implements grace window logic for low-friction repeat unlocks.
        """
        # Check if we're in a grace window (same track, within time limit, no risk escalation)
        if (self._last_full_verification_time is not None and
            self._last_full_verification_track_id is not None and
            (time.time() - self._last_full_verification_time) < self.challenge_grace_seconds):
            # Grace window is valid - check if conditions still hold
            # We'll verify track_id match and no risk escalation in process_frame
            return False

        return self.require_active_challenge or self._session_force_active_challenge

    def _compute_risk_escalation_flag(
        self,
        liveness_res,
        dominant_target,
        frame_shape: Tuple[int, int]
    ) -> bool:
        """
        Computes whether risk escalation is required based on liveness metrics.
        Returns True if any risk indicator is triggered.
        """
        # Check 1: Liveness score in ambiguous mid-range band
        if (liveness_res and
            hasattr(liveness_res, 'fused_score') and
            self.challenge_escalation_score_band[0] <= liveness_res.fused_score <= self.challenge_escalation_score_band[1]):
            return True

        # Check 2: Bezel confidence exceeds low threshold but below veto threshold
        if (liveness_res and
            hasattr(liveness_res, 'bezel_confidence') and
            liveness_res.bezel_confidence is not None and
            liveness_res.bezel_confidence >= self.challenge_escalation_bezel_threshold):
            return True

        # Check 3: Face fill ratio exceeds threshold (too close to camera)
        if dominant_target:
            face_area = dominant_target.area
            frame_area = frame_shape[0] * frame_shape[1]
            if face_area / frame_area > self.challenge_escalation_face_fill_ratio:
                return True

        return False



    def process_frame(self, frame: np.ndarray) -> EngineFrameResult:
        """
        Executes the detection -> tracking -> pose -> alignment -> embedding -> matching -> temporal -> liveness pipeline.
        
        SECURITY GUARANTEE:
        Aligned crops are processed in memory and discarded. No raw camera frames persist.
        Match alone NEVER sets is_authorized_to_unlock to True. Liveness must pass independently.
        """
        # Step 1: Detect all faces in frame
        detections = self.detector.detect(frame)

        # Step 2: Track faces and resolve dominant target face
        dominant_target, all_tracks = self.tracker.update(detections)
        
        if dominant_target is None:
            temporal_res = self.temporal_verifier.update(None, False, 0.0)
            self.liveness_detector.reset()
            self.challenge_manager.reset()
            self._challenge_passed_track_id = None
            self._last_full_verification_time = None
            self._last_full_verification_track_id = None
            self._last_liveness_result = None
            return EngineFrameResult(
                has_face=False,
                all_tracks=all_tracks,
                temporal_result=temporal_res,
                liveness_result=None,
                is_live=False,
                state_label="SEARCHING",
                is_authorized_to_unlock=False,
                details="Looking for you..."
            )

        # Re-pack dominant target as FaceDetection for downstream consumers
        dominant_detection = FaceDetection(
            bbox=dominant_target.bbox,
            confidence=dominant_target.confidence,
            landmarks=dominant_target.landmarks
        )

        # SECURITY: Reset challenge if track_id changes (prevents pass carryover to different face)
        if self._challenge_passed_track_id is not None and dominant_target.track_id != self._challenge_passed_track_id:
            self.challenge_manager.reset()
            self._challenge_passed_track_id = None
            self._last_full_verification_time = None
            self._last_full_verification_track_id = None

        # Face size check
        if dominant_target.width < 45 or dominant_target.height < 45:
            temporal_res = self.temporal_verifier.update(dominant_target.track_id, False, 0.0)
            self.liveness_detector.reset()
            self.challenge_manager.reset()
            self._challenge_passed_track_id = None
            self._last_full_verification_time = None
            self._last_full_verification_track_id = None
            self._last_liveness_result = None
            return EngineFrameResult(
                has_face=True,
                detection=dominant_detection,
                track_id=dominant_target.track_id,
                all_tracks=all_tracks,
                temporal_result=temporal_res,
                liveness_result=None,
                is_live=False,
                state_label="FACE_TOO_SMALL",
                is_authorized_to_unlock=False,
                details="Move closer to camera"
            )

        # Step 3: Estimate head pose
        pose_est = self.pose_estimator.estimate(dominant_target.landmarks)
        pose_label = pose_est.pose.value

        # Step 4: 5-point alignment to 112x112 canonical ArcFace geometry
        aligned_face = self.aligner.align(frame, dominant_target.landmarks)

        # Step 5: Extract 512-D L2-normalized ArcFace embedding
        embedding = self.embedder.extract_embedding(aligned_face)

        # SECURITY CONSTRAINT #2: Aligned frame buffer is immediately deleted from memory
        del aligned_face

        # Step 6: Biometric template matching (if active profile exists)
        if not self.cached_template_embeddings:
            temporal_res = self.temporal_verifier.update(dominant_target.track_id, False, 0.0)
            quality_res = self.quality_checker.evaluate(frame, [dominant_detection])
            if not quality_res.passed:
                liveness_res = self._last_liveness_result or LivenessResult(
                    is_live=False,
                    state="CHECKING",
                    fused_score=0.0,
                    motion_score=0.0,
                    texture_score=0.0,
                    eye_score=0.0,
                    details=f"Waiting for a clearer frame: {quality_res.rejection_reason}"
                )
                return EngineFrameResult(
                    has_face=True,
                    detection=dominant_detection,
                    track_id=dominant_target.track_id,
                    all_tracks=all_tracks,
                    embedding=embedding,
                    temporal_result=temporal_res,
                    liveness_result=liveness_res,
                    is_live=liveness_res.is_live,
                    estimated_pose=pose_label,
                    state_label="WAITING_FOR_CLEAR_FRAME",
                    is_authorized_to_unlock=False,
                    details=f"Waiting for a clearer frame: {quality_res.rejection_reason}"
                )
            liveness_res = self.liveness_detector.update(
                frame=frame,
                bbox=dominant_target.bbox,
                landmarks=dominant_target.landmarks,
                track_id=dominant_target.track_id
            )
            self._last_liveness_result = liveness_res
            return EngineFrameResult(
                has_face=True,
                detection=dominant_detection,
                track_id=dominant_target.track_id,
                all_tracks=all_tracks,
                embedding=embedding,
                temporal_result=temporal_res,
                liveness_result=liveness_res,
                is_live=liveness_res.is_live,
                estimated_pose=pose_label,
                state_label="FACE_FOUND",
                is_authorized_to_unlock=False,
                details="Face detected (No enrolled profile)"
            )

        match_result = self.matcher.match(
            live_embedding=embedding,
            enrolled_templates=self.cached_template_embeddings,
            template_poses=self.cached_template_poses,
            estimated_pose=pose_label,
            threshold=self.active_profile.matching_threshold if self.active_profile else None
        )
        matched_profile = None
        if match_result.is_match and match_result.best_template_idx is not None:
            if hasattr(self, '_template_profile_map') and match_result.best_template_idx < len(self._template_profile_map):
                matched_profile = self._template_profile_map[match_result.best_template_idx]

        # Step 7: Rolling temporal verification confirmation (M-of-N sliding window per-identity)
        temporal_res = self.temporal_verifier.update(
            track_id=dominant_target.track_id,
            is_frame_match=match_result.is_match,
            frame_confidence=match_result.confidence,
            profile_id=matched_profile.profile_id if matched_profile else None
        )

        # Step 8: Quality Gate & Independent Liveness Detection
        confirmed_profile = None
        quality_res = self.quality_checker.evaluate(frame, [dominant_detection])
        if not quality_res.passed:
            # FIQA quality check failed: do NOT call liveness_detector.update(),
            # do NOT reset rolling buffers, and do NOT count as a spoof veto.
            # Reuse last known LivenessResult and display waiting feedback.
            liveness_res = self._last_liveness_result or LivenessResult(
                is_live=False,
                state="CHECKING",
                fused_score=0.0,
                motion_score=0.0,
                texture_score=0.0,
                eye_score=0.0,
                details=f"Waiting for a clearer frame: {quality_res.rejection_reason}"
            )
            challenge_res = None
            is_authorized = False
            state_label = "WAITING_FOR_CLEAR_FRAME"
            details = f"Waiting for a clearer frame: {quality_res.rejection_reason}"
        else:
            liveness_res = self.liveness_detector.update(
                frame=frame,
                bbox=dominant_target.bbox,
                landmarks=dominant_target.landmarks,
                track_id=dominant_target.track_id
            )
            self._last_liveness_result = liveness_res

            # Compute risk escalation flag for challenge selection
            risk_escalation = self._compute_risk_escalation_flag(
                liveness_res, dominant_target, frame.shape[:2]
            )

            # Optional Active Challenge-Response Mode
            challenge_res: Optional[ChallengeResult] = None
            challenge_mode_enabled = self._is_challenge_required()

            # Check grace window conditions: same track, within time, no risk escalation
            in_grace_window = (
                self._last_full_verification_time is not None and
                self._last_full_verification_track_id is not None and
                (time.time() - self._last_full_verification_time) < self.challenge_grace_seconds and
                dominant_target.track_id == self._last_full_verification_track_id and
                not risk_escalation
            )

            if challenge_mode_enabled and not in_grace_window:
                curr_blinks = liveness_res.blink_count
                blink_this = liveness_res.blink_detected
                if not self.challenge_manager.is_active and not (self.challenge_manager.update(pose_label).passed):
                    # Use blink bias for low-friction, directional for high-risk
                    bias_blink = not risk_escalation
                    challenge_res = self.challenge_manager.start_challenge(
                        initial_blink_count=curr_blinks,
                        bias_blink=bias_blink
                    )
                else:
                    challenge_res = self.challenge_manager.update(
                        pose_label=pose_label,
                        current_blink_count=curr_blinks,
                        blink_detected_this_frame=blink_this
                    )

            # Step 9: Dual-Gate Authorization Logic
            # Identify temporally confirmed profile
            confirmed_profile = None
            if temporal_res.is_temporally_confirmed and temporal_res.confirmed_profile_id:
                for prof in self.enabled_profiles:
                    if prof.profile_id == temporal_res.confirmed_profile_id:
                        confirmed_profile = prof
                        break
            elif temporal_res.is_temporally_confirmed and self.enabled_profiles:
                confirmed_profile = self.active_profile or self.enabled_profiles[0]

            is_biometric_pass = bool(temporal_res.is_temporally_confirmed and confirmed_profile is not None)
            is_live_pass = liveness_res.is_live
            if challenge_mode_enabled and not in_grace_window:
                is_challenge_pass = bool(challenge_res is not None and challenge_res.passed)
            else:
                is_challenge_pass = True  # Grace window or challenge mode disabled
            is_authorized = bool(is_biometric_pass and is_live_pass and is_challenge_pass)

            # SECURITY: Consume challenge pass immediately after use (single-use)
            if is_authorized and challenge_mode_enabled and not in_grace_window and challenge_res and challenge_res.passed:
                self.challenge_manager.consume_pass()
                self._challenge_passed_track_id = dominant_target.track_id

            # Track grace window: record full verification time when authorized
            if is_authorized and is_biometric_pass and is_live_pass:
                self._last_full_verification_time = time.time()
                self._last_full_verification_track_id = dominant_target.track_id

            # Determine state label based on progression
            if liveness_res.state == "SPOOF_DETECTED":
                state_label = "SPOOF_DETECTED"
                details = f"Spoof Blocked: {liveness_res.spoof_reason or 'Presentation Attack'}"
            elif challenge_mode_enabled and not in_grace_window and challenge_res and challenge_res.state in ("FAILED", "TIMEOUT"):
                state_label = "CHALLENGE_FAILED"
                details = f"Challenge Failed: {challenge_res.details}"
            elif challenge_mode_enabled and not in_grace_window and challenge_res and not challenge_res.passed:
                state_label = "CHALLENGE"
                details = f"Action Required: {challenge_res.prompt_text}"
            elif is_authorized:
                state_label = "SUCCESS"
                user_greet = confirmed_profile.display_name if confirmed_profile else "User"
                details = f"✓ Welcome, {user_greet}! Unlocked ({temporal_res.details})"
            elif is_biometric_pass:
                state_label = "LIVENESS_CHECK"
                details = f"Face Matched — Checking Liveness ({liveness_res.details})"
            elif temporal_res.is_temporally_confirmed:
                state_label = "MATCH"
                details = f"Verified: {temporal_res.details}"
            elif match_result.is_match:
                state_label = "VERIFYING"
                details = f"Verifying ({temporal_res.positive_matches_in_window}/{temporal_res.required_matches})..."
            else:
                state_label = "NO_MATCH"
                details = match_result.details

        # If multiple faces present in frame, append bystander notice
        if len(all_tracks) > 1:
            details += f" ({len(all_tracks)} faces tracked)"

        matched_prof_id = confirmed_profile.profile_id if confirmed_profile else None
        matched_prof_name = confirmed_profile.display_name if confirmed_profile else None

        return EngineFrameResult(
            has_face=True,
            detection=dominant_detection,
            track_id=dominant_target.track_id,
            all_tracks=all_tracks,
            embedding=embedding,
            match_result=match_result,
            temporal_result=temporal_res,
            is_temporally_confirmed=temporal_res.is_temporally_confirmed,
            liveness_result=liveness_res,
            is_live=liveness_res.is_live,
            estimated_pose=pose_label,
            challenge_result=challenge_res,
            state_label=state_label,
            is_authorized_to_unlock=is_authorized,
            matched_profile_id=matched_prof_id,
            matched_profile_name=matched_prof_name,
            details=details
        )

    def enroll_from_frame(
        self,
        frame: np.ndarray,
        pose_label: str = "CENTER"
    ) -> Tuple[bool, Optional[PeekTemplate], str]:
        """
        Enrolls a single template from the given camera frame.
        Rejects frames with multiple faces, small faces, or poor confidence.
        """
        detections = self.detector.detect(frame)
        if not detections:
            return False, None, "No face detected in frame."
        if len(detections) > 1:
            return False, None, "Multiple faces detected. Single face required for enrollment."

        det = detections[0]
        if det.confidence < 0.65:
            return False, None, f"Detection confidence too low ({det.confidence:.2f})."
        if det.width < 50 or det.height < 50:
            return False, None, "Face too small in frame. Move closer."

        aligned_face = self.aligner.align(frame, det.landmarks)
        embedding = self.embedder.extract_embedding(aligned_face)
        del aligned_face  # Discard immediately

        template = PeekTemplate(
            template_id=f"tmpl_{int(time.time()*1000)}",
            embedding=embedding.tolist(),
            pose_label=pose_label,
            quality_score=float(det.confidence)
        )
        return True, template, "Enrollment sample captured successfully."
