"""
Peek Face Engine - ArcFace Embedder
Layer B: Extracts 512-dimensional L2-normalized feature embeddings from aligned 112x112 face crops.

SECURITY CONSTRAINT:
Raw frames are consumed in-memory and NEVER stored or persisted.
Only the 512-D float vector is returned for storage/matching.
"""

import cv2
import numpy as np
import onnxruntime as ort
import logging

logger = logging.getLogger("Peek.Embedder")

class ArcFaceEmbedder:
    """
    ArcFace MobileFaceNet feature extractor producing 512-dimensional embeddings.
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 2
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def extract_embedding(self, aligned_face: np.ndarray) -> np.ndarray:
        """
        Extracts a 512-D L2-normalized embedding vector from a (112, 112, 3) BGR face image.
        
        Args:
            aligned_face: (112, 112, 3) BGR aligned face image.
            
        Returns:
            embedding: (512,) float32 unit vector (||v||_2 = 1.0).
        """
        if aligned_face.shape[:2] != (112, 112):
            aligned_face = cv2.resize(aligned_face, (112, 112), interpolation=cv2.INTER_LINEAR)

        # Convert BGR -> RGB
        rgb = cv2.cvtColor(aligned_face, cv2.COLOR_BGR2RGB).astype(np.float32)
        
        # Standard ArcFace normalization: (x - 127.5) / 127.5
        normalized = (rgb - 127.5) / 127.5
        
        # HWC -> CHW -> NCHW
        blob = np.transpose(normalized, (2, 0, 1))[None, ...]
        
        # Inference
        outputs = self.session.run(None, {self.input_name: blob})
        raw_feat = outputs[0][0].astype(np.float32)  # shape (512,)
        
        # L2-normalize to unit sphere
        norm = np.linalg.norm(raw_feat)
        if norm > 1e-12:
            embedding = raw_feat / norm
        else:
            embedding = raw_feat

        # Frame is immediately out of scope and GC-eligible
        return embedding
