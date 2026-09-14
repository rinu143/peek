"""
Peek Face Engine - SCRFD Face Detector
Layer B: Face & 5-point landmark detection using SCRFD-500M ONNX model.
"""

import os
import cv2
import numpy as np
import onnxruntime as ort
from dataclasses import dataclass
from typing import List, Tuple, Optional

@dataclass
class FaceDetection:
    bbox: np.ndarray  # [x1, y1, x2, y2]
    confidence: float
    landmarks: np.ndarray  # shape (5, 2): [left_eye, right_eye, nose, left_mouth, right_mouth]
    
    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]
        
    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]
        
    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


class SCRFDDetector:
    """
    SCRFD (Sample and Computation Redistribution for Efficient Face Detection)
    Face and 5-point facial landmark detector.
    """

    def __init__(
        self,
        model_path: str,
        input_size: Tuple[int, int] = (640, 640),
        conf_threshold: float = 0.5,
        nms_threshold: float = 0.4,
    ):
        self.model_path = model_path
        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        self.strides = [8, 16, 32]
        
        # Initialize ONNX runtime session with CPU execution provider
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 2
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        
        # Precompute anchor grids
        self.anchor_centers = self._generate_anchors(self.input_size, self.strides)

    @staticmethod
    def _generate_anchors(input_size: Tuple[int, int], strides: List[int]) -> List[np.ndarray]:
        anchors = []
        for s in strides:
            h, w = input_size[0] // s, input_size[1] // s
            xv, yv = np.meshgrid(np.arange(w), np.arange(h))
            grid = np.stack([xv, yv], axis=-1).reshape(-1, 2) * s
            # 2 anchors per grid position in SCRFD
            grid = np.repeat(grid, 2, axis=0)
            anchors.append(grid.astype(np.float32))
        return anchors

    def detect(self, image: np.ndarray) -> List[FaceDetection]:
        """
        Detects faces and extracts 5 landmarks in the given BGR image.
        Returns list of FaceDetection objects sorted by area (largest first).
        """
        orig_h, orig_w = image.shape[:2]
        target_w, target_h = self.input_size
        
        # Compute scaling with aspect ratio preserved
        scale = min(target_w / orig_w, target_h / orig_h)
        resized_w, resized_h = int(orig_w * scale), int(orig_h * scale)
        resized = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
        
        # Letterbox pad to target input size
        padded = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        padded[:resized_h, :resized_w] = resized
        
        # Normalize: (x - 127.5) / 128.0 and transpose to NCHW
        blob = (padded[:, :, ::-1].astype(np.float32) - 127.5) / 128.0
        blob = np.transpose(blob, (2, 0, 1))[None, ...]

        # Run inference
        outputs = self.session.run(None, {self.input_name: blob})
        
        # Parse outputs (9 tensors: 3 scores, 3 bboxes, 3 landmarks)
        scores_list = outputs[0:3]
        bboxes_list = outputs[3:6]
        kps_list = outputs[6:9]

        all_boxes = []
        all_scores = []
        all_kps = []

        for stride_idx, stride in enumerate(self.strides):
            scores = scores_list[stride_idx].flatten()
            bboxes = bboxes_list[stride_idx]
            kpss = kps_list[stride_idx]
            anchors = self.anchor_centers[stride_idx]

            pos_inds = np.where(scores >= self.conf_threshold)[0]
            if len(pos_inds) == 0:
                continue

            scores = scores[pos_inds]
            bboxes = bboxes[pos_inds] * stride
            kpss = kpss[pos_inds] * stride
            anchors = anchors[pos_inds]

            # Decode bounding boxes: [x1, y1, x2, y2]
            x1 = (anchors[:, 0] - bboxes[:, 0]) / scale
            y1 = (anchors[:, 1] - bboxes[:, 1]) / scale
            x2 = (anchors[:, 0] + bboxes[:, 2]) / scale
            y2 = (anchors[:, 1] + bboxes[:, 3]) / scale
            boxes = np.stack([x1, y1, x2, y2], axis=-1)

            # Decode 5 landmarks: (kpx, kpy)
            kps = np.zeros((len(pos_inds), 5, 2), dtype=np.float32)
            for i in range(5):
                kps[:, i, 0] = (anchors[:, 0] + kpss[:, i * 2]) / scale
                kps[:, i, 1] = (anchors[:, 1] + kpss[:, i * 2 + 1]) / scale

            all_boxes.append(boxes)
            all_scores.append(scores)
            all_kps.append(kps)

        if not all_boxes:
            return []

        all_boxes = np.concatenate(all_boxes, axis=0)
        all_scores = np.concatenate(all_scores, axis=0)
        all_kps = np.concatenate(all_kps, axis=0)

        # Apply Non-Maximum Suppression (NMS)
        keep_indices = self._nms(all_boxes, all_scores, self.nms_threshold)
        
        detections = []
        for idx in keep_indices:
            # Clip bbox to image boundaries
            box = all_boxes[idx]
            box[0] = max(0, min(box[0], orig_w - 1))
            box[1] = max(0, min(box[1], orig_h - 1))
            box[2] = max(0, min(box[2], orig_w - 1))
            box[3] = max(0, min(box[3], orig_h - 1))
            
            detections.append(FaceDetection(
                bbox=box,
                confidence=float(all_scores[idx]),
                landmarks=all_kps[idx]
            ))

        # Sort by area descending (largest face first)
        detections.sort(key=lambda d: d.area, reverse=True)
        return detections

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> List[int]:
        """Classic greedy Non-Maximum Suppression."""
        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        order = scores.argsort()[::-1]

        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            ovr = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)

            inds = np.where(ovr <= threshold)[0]
            order = order[inds + 1]

        return keep
