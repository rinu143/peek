"""
Peek Face Engine - Portrait Blur & Segmentation
Layer D: Real-time background segmentation and bokeh blur.
Isolates the primary enrolling subject and suppresses secondary bystanders in the background.
"""

import os
import cv2
import numpy as np
import logging
from typing import Optional, List, Tuple
import onnxruntime as ort

from engine.detection.scrfd_detector import FaceDetection

logger = logging.getLogger("Peek.PortraitBlur")


class PortraitBlurProcessor:
    """
    Applies neural or geometric portrait background blur to camera frames.
    Keeps the primary enrolling face sharp while blurring background distractions
    and background people.
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        blur_ksize: int = 41,
        feather_ksize: int = 15,
        threshold: float = 0.5,
    ):
        self.blur_ksize = blur_ksize if blur_ksize % 2 == 1 else blur_ksize + 1
        self.feather_ksize = feather_ksize if feather_ksize % 2 == 1 else feather_ksize + 1
        self.threshold = threshold

        self.session: Optional[ort.InferenceSession] = None
        self.input_name: Optional[str] = None
        self.input_shape: Tuple[int, int] = (256, 256)

        resolved_path = model_path
        if resolved_path is None:
            models_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models"
            )
            default_path = os.path.join(models_dir, "selfie_segmentation.onnx")
            if os.path.exists(default_path):
                resolved_path = default_path
            else:
                try:
                    from engine.models.download_models import ensure_segmentation_model
                    resolved_path = ensure_segmentation_model()
                except Exception as ex:
                    logger.warning("Could not download segmentation model: %s", ex)

        if resolved_path and os.path.exists(resolved_path):
            try:
                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 2
                opts.intra_op_num_threads = 2
                self.session = ort.InferenceSession(
                    resolved_path, sess_options=opts, providers=["CPUExecutionProvider"]
                )
                self.input_name = self.session.get_inputs()[0].name
                logger.info("Loaded selfie segmentation ONNX model from: %s", resolved_path)
            except Exception as ex:
                logger.warning("Failed to initialize ONNX segmentation session (%s). Fallback enabled.", ex)
                self.session = None

    @property
    def has_onnx_model(self) -> bool:
        return self.session is not None

    def _infer_mask_onnx(self, frame: np.ndarray) -> np.ndarray:
        """Runs ONNX inference and returns full-resolution float32 mask [0.0 - 1.0]."""
        h, w = frame.shape[:2]
        target_w, target_h = self.input_shape

        # Preprocess: BGR -> RGB, resize to 256x256, normalize to [0, 1]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        blob = np.expand_dims(resized, axis=0)

        # Inference: output shape is typically (1, 256, 256, 1) or (1, 1, 256, 256)
        outputs = self.session.run(None, {self.input_name: blob})
        raw_mask = outputs[0]
        if raw_mask.ndim == 4:
            if raw_mask.shape[-1] == 1:
                mask_2d = raw_mask[0, :, :, 0]
            else:
                mask_2d = raw_mask[0, 0, :, :]
        else:
            mask_2d = np.squeeze(raw_mask)

        # Upscale mask to original frame dimensions
        return cv2.resize(mask_2d, (w, h), interpolation=cv2.INTER_LINEAR)

    def _generate_fallback_mask(
        self,
        frame_shape: Tuple[int, int],
        primary_face: Optional[FaceDetection] = None
    ) -> np.ndarray:
        """
        Synthesizes a smooth elliptical portrait vignette mask centered on the
        primary enrolling user as a zero-crash fallback when ONNX model is unavailable.
        """
        h, w = frame_shape[:2]
        mask = np.zeros((h, w), dtype=np.float32)

        if primary_face is not None:
            bx1, by1, bx2, by2 = primary_face.bbox
            cx = int((bx1 + bx2) / 2.0)
            cy = int((by1 + by2) / 2.0)
            fw = bx2 - bx1
            fh = by2 - by1
            rx = int(fw * 1.2)
            ry = int(fh * 1.6)
            # Torso expansion below chin
            torso_y = min(h, int(by2 + fh * 1.5))
        else:
            cx, cy = w // 2, h // 2
            rx, ry = int(w * 0.35), int(h * 0.45)
            torso_y = h

        # Draw smooth ellipse for head/upper body
        cv2.ellipse(mask, (cx, cy), (rx, ry), 0, 0, 360, 1.0, -1)

        # Draw trapezoid / rectangle for chest / shoulders extending to bottom
        if primary_face is not None:
            pts = np.array([
                [max(0, cx - int(rx * 1.3)), cy + int(ry * 0.5)],
                [min(w, cx + int(rx * 1.3)), cy + int(ry * 0.5)],
                [min(w, cx + int(rx * 2.0)), torso_y],
                [max(0, cx - int(rx * 2.0)), torso_y],
            ], dtype=np.int32)
            cv2.fillPoly(mask, [pts], 1.0)

        # Blur the geometric mask to produce smooth edges
        return cv2.GaussianBlur(mask, (51, 51), 0)

    def process(
        self,
        frame: np.ndarray,
        primary_face: Optional[FaceDetection] = None,
        secondary_faces: Optional[List[FaceDetection]] = None,
    ) -> np.ndarray:
        """
        Processes frame to isolate the primary enrolling person and blur the background.

        Args:
            frame: Input BGR camera image.
            primary_face: The detected face of the enrolling user.
            secondary_faces: Any additional background / bystander faces detected.

        Returns:
            Composited BGR image with sharp enrolling person and blurred background.
        """
        h, w = frame.shape[:2]

        # 1. Generate foreground segmentation mask
        if self.session is not None:
            try:
                mask = self._infer_mask_onnx(frame)
            except Exception as ex:
                logger.warning("ONNX segmentation error: %s; falling back to vignette.", ex)
                mask = self._generate_fallback_mask(frame.shape, primary_face)
        else:
            mask = self._generate_fallback_mask(frame.shape, primary_face)

        # 2. Suppress secondary bystanders in mask
        if secondary_faces:
            primary_box = primary_face.bbox if primary_face is not None else None
            for sec in secondary_faces:
                bx1, by1, bx2, by2 = sec.bbox
                bw = bx2 - bx1
                bh = by2 - by1

                # Expand box to cover head, hair, and torso of bystander
                ex1 = max(0, int(bx1 - bw * 0.4))
                ey1 = max(0, int(by1 - bh * 0.4))
                ex2 = min(w, int(bx2 + bw * 0.4))
                ey2 = min(h, int(by2 + bh * 2.0))

                # If primary face exists, ensure we do not cut into primary face bbox
                if primary_box is not None:
                    px1, py1, px2, py2 = primary_box
                    # Suppress pixels outside primary face boundary
                    sub_mask = mask[ey1:ey2, ex1:ex2]
                    # Generate coordinate grid within the region
                    y_indices, x_indices = np.ogrid[ey1:ey2, ex1:ex2]
                    inside_primary = (
                        (x_indices >= px1) & (x_indices <= px2) &
                        (y_indices >= py1) & (y_indices <= py2)
                    )
                    sub_mask[~inside_primary] = 0.0
                    mask[ey1:ey2, ex1:ex2] = sub_mask
                else:
                    mask[ey1:ey2, ex1:ex2] = 0.0

        # 3. Soft feathering along mask boundary for natural bokeh blending
        smooth_mask = cv2.GaussianBlur(mask, (self.feather_ksize, self.feather_ksize), 0)
        smooth_mask = np.clip(smooth_mask, 0.0, 1.0)
        mask_3ch = np.stack([smooth_mask] * 3, axis=-1)

        # 4. Blur background
        blurred_bg = cv2.GaussianBlur(frame, (self.blur_ksize, self.blur_ksize), 0)

        # 5. Composite sharp subject over blurred background
        composited = (
            frame.astype(np.float32) * mask_3ch +
            blurred_bg.astype(np.float32) * (1.0 - mask_3ch)
        )
        return np.clip(composited, 0, 255).astype(np.uint8)
