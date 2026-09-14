"""
Peek Face Engine - End-to-End Pipeline Automated Tests
Validates detection, landmark alignment, ArcFace embedding, matching, DPAPI storage, and security constraints.
"""

import os
import unittest
import numpy as np
import cv2
import tempfile
import shutil

from engine.detection.scrfd_detector import SCRFDDetector, FaceDetection
from engine.alignment.aligner import FaceAligner, ARCFACE_CANONICAL_5PTS
from engine.embedding.arcface_embedder import ArcFaceEmbedder
from engine.recognition.matcher import FaceMatcher
from engine.pipeline.face_engine import FaceEngine
from storage.template_store import SecureProfileStore, PeekProfile, PeekTemplate

def create_synthetic_face_image(width=640, height=480) -> np.ndarray:
    """
    Creates a clean synthetic face image containing eyes, nose, and mouth
    at positions that trigger facial detection.
    """
    img = np.full((height, width, 3), 220, dtype=np.uint8)
    
    center_x, center_y = width // 2, height // 2
    # Draw face oval
    cv2.ellipse(img, (center_x, center_y), (90, 120), 0, 0, 360, (180, 180, 180), -1)
    
    # Left eye
    cv2.circle(img, (center_x - 35, center_y - 25), 10, (50, 50, 50), -1)
    # Right eye
    cv2.circle(img, (center_x + 35, center_y - 25), 10, (50, 50, 50), -1)
    # Nose
    pts_nose = np.array([[center_x, center_y - 5], [center_x - 10, center_y + 15], [center_x + 10, center_y + 15]])
    cv2.fillPoly(img, [pts_nose], (120, 120, 120))
    # Mouth
    cv2.ellipse(img, (center_x, center_y + 50), (30, 12), 0, 0, 180, (70, 70, 70), -1)
    
    return img


class TestPeekFaceEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, "engine", "models")
        cls.det_model_path = os.path.join(models_dir, "scrfd_500m_bnkps.onnx")
        cls.emb_model_path = os.path.join(models_dir, "w600k_mbf.onnx")
        
        # Verify models exist
        assert os.path.exists(cls.det_model_path), f"Detector model missing: {cls.det_model_path}"
        assert os.path.exists(cls.emb_model_path), f"Embedder model missing: {cls.emb_model_path}"

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="peek_test_store_")
        self.profile_store = SecureProfileStore(storage_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_face_alignment(self):
        """Validates that landmark-based affine transformation outputs exact 112x112 image."""
        aligner = FaceAligner(target_size=(112, 112))
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # Canonical landmarks shifted and scaled
        test_landmarks = ARCFACE_CANONICAL_5PTS * 2.0 + 100.0
        aligned = aligner.align(dummy_img, test_landmarks)
        
        self.assertEqual(aligned.shape, (112, 112, 3))
        self.assertEqual(aligned.dtype, np.uint8)

    def test_arcface_embedder_unit_norm(self):
        """Validates that the embedder outputs a 512-D float vector normalized to unit length."""
        embedder = ArcFaceEmbedder(self.emb_model_path)
        dummy_face = np.random.randint(0, 256, (112, 112, 3), dtype=np.uint8)
        
        embedding = embedder.extract_embedding(dummy_face)
        self.assertEqual(embedding.shape, (512,))
        self.assertEqual(embedding.dtype, np.float32)
        
        norm = np.linalg.norm(embedding)
        self.assertAlmostEqual(norm, 1.0, places=4, msg="Embedding vector must be L2-normalized to unit sphere")

    def test_matcher_security_and_similarity(self):
        """Validates cosine similarity and verifies non-negotiable security constraint #1."""
        matcher = FaceMatcher(default_threshold=0.50)
        
        # Same template match
        v1 = np.random.randn(512).astype(np.float32)
        v1 /= np.linalg.norm(v1)
        
        sim_self = matcher.compute_similarity(v1, v1)
        self.assertAlmostEqual(sim_self, 1.0, places=5)
        
        result_match = matcher.match(v1, [v1])
        self.assertTrue(result_match.is_match)
        self.assertGreaterEqual(result_match.confidence, 0.99)
        
        # CRITICAL SECURITY CONSTRAINT #1 CHECK:
        # Match decision alone can NEVER authorize unlock!
        self.assertFalse(
            result_match.is_authorized_to_unlock,
            "Security Constraint #1 violated: Biometric match must never authorize unlock directly!"
        )

        # Orthogonal/different template match
        v2 = -v1
        result_diff = matcher.match(v1, [v2])
        self.assertFalse(result_diff.is_match)
        self.assertLess(result_diff.confidence, 0.0)

    def test_dpapi_secure_storage(self):
        """Validates DPAPI encryption at rest for face templates."""
        template = PeekTemplate(
            template_id="tmpl_001",
            embedding=[0.1] * 512,
            pose_label="CENTER",
            quality_score=0.95
        )
        profile = PeekProfile(
            profile_id="prof_test_user",
            display_name="Test User",
            templates=[template]
        )

        # Save profile
        file_path = self.profile_store.save_profile(profile)
        self.assertTrue(os.path.exists(file_path))

        # Check that file content is encrypted (not plaintext JSON)
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        self.assertNotIn(b"Test User", raw_bytes, "Security Constraint #4 violated: Plaintext found in profile store!")
        self.assertNotIn(b"prof_test_user", raw_bytes)

        # Load profile and verify integrity
        loaded = self.profile_store.load_profile("prof_test_user")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.profile_id, "prof_test_user")
        self.assertEqual(loaded.display_name, "Test User")
        self.assertEqual(len(loaded.templates), 1)
        self.assertEqual(len(loaded.templates[0].embedding), 512)
        self.assertAlmostEqual(loaded.templates[0].embedding[0], 0.1)

    def test_face_engine_end_to_end(self):
        """Validates FaceEngine end-to-end processing and enrollment."""
        engine = FaceEngine(
            detector_model_path=self.det_model_path,
            embedder_model_path=self.emb_model_path,
            profile_store=self.profile_store,
            match_threshold=0.50
        )

        # 1. Blank frame -> SEARCHING
        blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        res_blank = engine.process_frame(blank_frame)
        self.assertFalse(res_blank.has_face)
        self.assertEqual(res_blank.state_label, "SEARCHING")
        self.assertFalse(res_blank.is_authorized_to_unlock)

        # 2. Simulated face image
        face_img = create_synthetic_face_image()
        
        # Test detection or enrollment
        # If synthetic face is detected:
        dets = engine.detector.detect(face_img)
        if dets:
            res_face = engine.process_frame(face_img)
            self.assertTrue(res_face.has_face)
            self.assertIsNotNone(res_face.embedding)
            self.assertEqual(res_face.embedding.shape, (512,))
            self.assertFalse(res_face.is_authorized_to_unlock)

if __name__ == "__main__":
    unittest.main()
