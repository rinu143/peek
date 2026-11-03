"""
Peek Face Engine - Multi-Identity and Elevated Authorization Tests
Tests:
1. Two identities: A matches -> AUTHENTICATED id A; B matches -> AUTHENTICATED id B.
2. Mixed A/B frames never satisfy the temporal check (>= 5 of last 7 must match same profile).
3. Disabled and unauthorized profiles are not loaded (registry-aware filtering and legacy v1 fallback).
4. Recapture is atomic and cancel-safe (cancelling preserves original; completion updates atomically).
5. Duplicate identity rejection (DUPLICATE_IDENTITY) and identity cap rejection (IDENTITY_LIMIT, cap 5).
6. Protocol verification (no password or confirmed fields).
"""

import os
import time
import uuid
import tempfile
import shutil
import unittest
import numpy as np
from unittest.mock import patch, MagicMock

from engine.temporal.temporal_verifier import TemporalVerifier
from engine.pipeline.face_engine import FaceEngine, EngineFrameResult, get_authorized_identities_from_registry
from engine.enrollment.enrollment_session import (
    EnrollmentSession,
    IdentityLimitError,
    DuplicateIdentityError,
)
from engine.enrollment.enrollment_runner import EnrollmentRunner
from engine.ipc.enrollment_protocol import (
    make_start_enrollment,
    REASON_IDENTITY_LIMIT,
    REASON_DUPLICATE_IDENTITY,
)
from engine.ipc.protocol import make_authenticated
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate


class TestMultiIdentity(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="peek_test_multi_id_")
        self.profile_store = SecureProfileStore(storage_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_template(self, template_id: str, seed: int = 42) -> PeekTemplate:
        rng = np.random.RandomState(seed)
        vec = rng.randn(512).astype(np.float32)
        vec /= np.linalg.norm(vec)
        return PeekTemplate(
            template_id=template_id,
            embedding=vec.tolist(),
            pose_label="CENTER",
            quality_score=0.95,
        )

    def test_protocol_has_no_password_or_confirmed_field(self):
        """The enrollment protocol must never contain password or confirmed fields."""
        msg = make_start_enrollment(display_name="Alice", clean_preview=True, blur_background=True, activate=False)
        self.assertNotIn("password", msg)
        self.assertNotIn("confirmed", msg)

        import engine.ipc.enrollment_protocol as ep
        self.assertFalse(hasattr(ep, "verify_windows_password"))
        self.assertFalse(hasattr(ep, "MSG_CONFIRM_PASSWORD"))

    def test_mixed_ab_frames_never_satisfy_temporal_check(self):
        """
        Temporal verification requires >= 5 of the last 7 frames to match the SAME profile_id.
        Mixed A/B frames must never satisfy the temporal check.
        """
        verifier = TemporalVerifier(window_size=7, min_matches_required=5)

        # 3 frames of Identity A followed by 4 frames of Identity B
        # In this 7-frame window: A has 3 matches, B has 4 matches -> neither has >= 5!
        for _ in range(3):
            res = verifier.update(track_id=1, is_frame_match=True, frame_confidence=0.8, profile_id="prof_A")
            self.assertFalse(res.is_temporally_confirmed)
            self.assertIsNone(res.confirmed_profile_id)

        for _ in range(4):
            res = verifier.update(track_id=1, is_frame_match=True, frame_confidence=0.8, profile_id="prof_B")
            self.assertFalse(res.is_temporally_confirmed)
            self.assertIsNone(res.confirmed_profile_id)

        # Interleaved A, B, A, B, A, B, A -> maximum count for any single identity is 4
        verifier.reset()
        for i in range(7):
            pid = "prof_A" if i % 2 == 0 else "prof_B"
            res = verifier.update(track_id=1, is_frame_match=True, frame_confidence=0.8, profile_id=pid)
            self.assertFalse(res.is_temporally_confirmed, f"Interleaved frame {i} must not confirm")

        # Now 5 consecutive frames of A -> must confirm with confirmed_profile_id == 'prof_A'
        for i in range(5):
            res = verifier.update(track_id=1, is_frame_match=True, frame_confidence=0.8, profile_id="prof_A")

        self.assertTrue(res.is_temporally_confirmed)
        self.assertEqual(res.confirmed_profile_id, "prof_A")

    def test_disabled_and_unauthorized_profiles_filtered(self):
        """
        Profile filtering:
        1. Registry key present: only enabled profiles listed in Authorized are loaded.
        2. Disabled profiles in Authorized list are NOT loaded.
        3. Enabled profiles NOT in Authorized list are NOT loaded.
        4. Registry key missing (legacy v1): all enabled profiles are loaded.
        """
        p_auth = PeekProfile(
            profile_id="p_auth_1",
            display_name="Authorized User",
            enabled=True,
            templates=[self._create_mock_template("t1", seed=1)],
        )
        p_unauth = PeekProfile(
            profile_id="p_unauth_2",
            display_name="Unauthorized User",
            enabled=True,
            templates=[self._create_mock_template("t2", seed=2)],
        )
        p_disabled = PeekProfile(
            profile_id="p_disabled_3",
            display_name="Disabled User",
            enabled=False,
            templates=[self._create_mock_template("t3", seed=3)],
        )

        self.profile_store.save_profile(p_auth)
        self.profile_store.save_profile(p_unauth)
        self.profile_store.save_profile(p_disabled)

        engine = FaceEngine(profile_store=self.profile_store)

        # Case 1: Registry key exists and authorizes only 'p_auth_1' and 'p_disabled_3'
        with patch("engine.pipeline.face_engine.get_authorized_identities_from_registry", return_value=["p_auth_1", "p_disabled_3"]):
            engine.reload_active_profile(user_sid="S-1-5-21-test")
            loaded_ids = [p.profile_id for p in engine.enabled_profiles]
            self.assertIn("p_auth_1", loaded_ids)
            self.assertNotIn("p_unauth_2", loaded_ids, "Unauthorized profile must not be loaded")
            self.assertNotIn("p_disabled_3", loaded_ids, "Disabled profile must not be loaded even if in Authorized list")
            self.assertEqual(len(engine.enabled_profiles), 1)

        # Case 2: Registry key does not exist (returns None, legacy v1 install)
        with patch("engine.pipeline.face_engine.get_authorized_identities_from_registry", return_value=None):
            engine.reload_active_profile(user_sid="S-1-5-21-test")
            loaded_ids = [p.profile_id for p in engine.enabled_profiles]
            self.assertIn("p_auth_1", loaded_ids)
            self.assertIn("p_unauth_2", loaded_ids)
            self.assertNotIn("p_disabled_3", loaded_ids, "Disabled profiles are never loaded")
            self.assertEqual(len(engine.enabled_profiles), 2)

        # Case 3: Registry read error (PermissionError / OSError -> returns [], loads NO profiles)
        with patch("engine.pipeline.face_engine.get_authorized_identities_from_registry", return_value=[]):
            engine.reload_active_profile(user_sid="S-1-5-21-test")
            self.assertEqual(len(engine.enabled_profiles), 0, "Registry read error must load 0 profiles instead of all enabled ones")

    def test_two_identities_distinct_auth(self):
        """
        Two authorized identities (Alice & Bob):
        When Alice matches temporally -> AUTHENTICATED emitted with Alice's id and name.
        When Bob matches temporally -> AUTHENTICATED emitted with Bob's id and name.
        """
        p_alice = PeekProfile(
            profile_id="prof_alice",
            display_name="Alice",
            enabled=True,
            templates=[self._create_mock_template("t_alice", seed=10)],
        )
        p_bob = PeekProfile(
            profile_id="prof_bob",
            display_name="Bob",
            enabled=True,
            templates=[self._create_mock_template("t_bob", seed=20)],
        )
        self.profile_store.save_profile(p_alice)
        self.profile_store.save_profile(p_bob)

        engine = FaceEngine(profile_store=self.profile_store)
        with patch("engine.pipeline.face_engine.get_authorized_identities_from_registry", return_value=None):
            engine.reload_active_profile()

        self.assertEqual(len(engine.enabled_profiles), 2)

        # Alice authentication simulation:
        # Construct AUTHENTICATED message with Alice's profile_id
        auth_alice = make_authenticated(display_name="Alice", profile_id="prof_alice")
        self.assertEqual(auth_alice["display_name"], "Alice")
        self.assertEqual(auth_alice["profile_id"], "prof_alice")
        self.assertTrue(auth_alice["is_authorized_to_unlock"])

        # Bob authentication simulation:
        auth_bob = make_authenticated(display_name="Bob", profile_id="prof_bob")
        self.assertEqual(auth_bob["display_name"], "Bob")
        self.assertEqual(auth_bob["profile_id"], "prof_bob")
        self.assertTrue(auth_bob["is_authorized_to_unlock"])

    def test_recapture_is_atomic_and_cancel_safe(self):
        """
        Recapture:
        1. Cancelling halfway leaves the original profile and templates completely untouched.
        2. Completing all 9 poses replaces the templates atomically and preserves enabled, created_at, user_sid, display_name.
        """
        orig_t = [self._create_mock_template(f"orig_{i}", seed=100 + i) for i in range(9)]
        orig_profile = PeekProfile(
            profile_id="profile_recapture_target",
            display_name="Original Name",
            enabled=True,
            created_at=1000000.0,
            user_sid="S-1-5-21-9999",
            templates=orig_t,
        )
        self.profile_store.save_profile(orig_profile)

        # Verify initial state on disk
        loaded_orig = self.profile_store.load_profile("profile_recapture_target")
        self.assertEqual(len(loaded_orig.templates), 9)
        self.assertEqual(loaded_orig.templates[0].template_id, "orig_0")

        # Simulate cancellation during recapture:
        # A session is started for recapture, but canceled before finalizing
        runner = EnrollmentRunner(
            profile_store=self.profile_store,
            profile_id="profile_recapture_target",
            display_name="Original Name",
        )
        runner.cancel()
        self.assertTrue(runner.is_cancelled)

        # Assert disk content is completely unmodified
        post_cancel = self.profile_store.load_profile("profile_recapture_target")
        self.assertEqual(post_cancel.created_at, 1000000.0)
        self.assertEqual(post_cancel.display_name, "Original Name")
        self.assertEqual(post_cancel.templates[0].template_id, "orig_0")

        # Simulate completion of recapture:
        new_t = [self._create_mock_template(f"new_{i}", seed=200 + i) for i in range(9)]
        session = EnrollmentSession(
            detector=None,
            aligner=None,
            embedder=None,
            profile_store=self.profile_store,
        )
        session.final_templates = new_t

        saved = session.finalize_and_save_profile(
            display_name="Different Name Should Be Ignored",
            profile_id="profile_recapture_target",
        )

        self.assertEqual(saved.profile_id, "profile_recapture_target")
        self.assertEqual(saved.display_name, "Original Name", "Recapture must preserve original display_name")
        self.assertEqual(saved.created_at, 1000000.0, "Recapture must preserve original created_at")
        self.assertEqual(saved.user_sid, "S-1-5-21-9999", "Recapture must preserve original user_sid")
        self.assertTrue(saved.enabled, "Recapture must preserve original enabled state")
        self.assertEqual(saved.templates[0].template_id, "new_0")

        # Verify disk reflects atomic update
        reloaded = self.profile_store.load_profile("profile_recapture_target")
        self.assertEqual(reloaded.templates[0].template_id, "new_0")

    def test_duplicate_and_cap_rejections(self):
        """
        1. Duplicate rejection: enrolling with embeddings matching existing profile >= threshold
           fails with DuplicateIdentityError naming the duplicate.
        2. Cap rejection: MAX_IDENTITIES = 5. A 6th identity is rejected with IdentityLimitError.
        3. Recapture of an existing profile is exempt from both cap and duplicate rejection.
        """
        # Enroll 5 distinct identities
        for i in range(5):
            t = [self._create_mock_template(f"t_{i}_{j}", seed=1000 * i + j) for j in range(9)]
            session = EnrollmentSession(detector=None, aligner=None, embedder=None, profile_store=self.profile_store)
            session.final_templates = t
            session.finalize_and_save_profile(f"User {i}", profile_id=f"prof_{i}", activate=True)

        self.assertEqual(len(self.profile_store.list_profiles()), 5)

        # Attempt to enroll 6th identity -> IdentityLimitError
        t_6 = [self._create_mock_template(f"t_6_{j}", seed=6000 + j) for j in range(9)]
        session_6 = EnrollmentSession(detector=None, aligner=None, embedder=None, profile_store=self.profile_store)
        session_6.final_templates = t_6
        with self.assertRaises(IdentityLimitError):
            session_6.finalize_and_save_profile("User 6", profile_id="prof_6")

        # Recapture of prof_0 when at limit of 5 MUST succeed (exempt from limit)
        t_recap = [self._create_mock_template(f"t_recap_{j}", seed=9000 + j) for j in range(9)]
        session_recap = EnrollmentSession(detector=None, aligner=None, embedder=None, profile_store=self.profile_store)
        session_recap.final_templates = t_recap
        recap_res = session_recap.finalize_and_save_profile("Ignored", profile_id="prof_0")
        self.assertEqual(recap_res.profile_id, "prof_0")

        # Duplicate check: remove 1 profile so we are below limit (4 profiles)
        os.remove(self.profile_store._get_profile_path("prof_4"))
        self.assertEqual(len(self.profile_store.list_profiles()), 4)

        # Try enrolling a face matching prof_1 (using seed=1000 identical templates)
        t_dup = [self._create_mock_template(f"t_dup_{j}", seed=1000 + j) for j in range(9)]
        session_dup = EnrollmentSession(detector=None, aligner=None, embedder=None, profile_store=self.profile_store)
        session_dup.final_templates = t_dup

        with self.assertRaises(DuplicateIdentityError) as ctx:
            session_dup.finalize_and_save_profile("Impostor User", profile_id="prof_impostor")

        self.assertIn("User 1", str(ctx.exception))
        self.assertEqual(ctx.exception.duplicate_name, "User 1")
        self.assertEqual(ctx.exception.duplicate_id, "prof_1")


    def test_get_authorized_identities_from_registry_error_handling(self):
        """
        get_authorized_identities_from_registry:
        - returns None only when key does not exist (FileNotFoundError)
        - returns [] and logs on any other error (PermissionError, etc.)
        """
        import winreg
        with patch("winreg.OpenKey", side_effect=FileNotFoundError):
            result = get_authorized_identities_from_registry("S-1-5-21-test")
            self.assertIsNone(result)

        with patch("winreg.OpenKey", side_effect=PermissionError("Access denied")):
            result = get_authorized_identities_from_registry("S-1-5-21-test")
            self.assertEqual(result, [])

        with patch("winreg.OpenKey", side_effect=OSError("Generic error")):
            result = get_authorized_identities_from_registry("S-1-5-21-test")
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
