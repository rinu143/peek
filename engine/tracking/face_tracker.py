"""
Peek Face Engine - Dominant-Face Tracker
Layer B: Persistent face tracking using IoU association and sticky dominant-target selection.

Prevents unstable target-switching when multiple people or background bystanders are visible.
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
from engine.detection.scrfd_detector import FaceDetection

@dataclass
class TrackedFace:
    track_id: int
    bbox: np.ndarray        # [x1, y1, x2, y2]
    landmarks: np.ndarray   # (5, 2)
    confidence: float
    hits: int = 1           # Consecutive detection hits
    age: int = 1            # Total frames tracked
    time_since_update: int = 0
    is_confirmed: bool = False

    @property
    def width(self) -> float:
        return float(self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return float(self.bbox[3] - self.bbox[1])

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def centroid(self) -> Tuple[float, float]:
        return (float((self.bbox[0] + self.bbox[2]) / 2.0),
                float((self.bbox[1] + self.bbox[3]) / 2.0))


def compute_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    """Computes Intersection over Union (IoU) between two bounding boxes [x1, y1, x2, y2]."""
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter_area = inter_w * inter_h

    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])

    denom = area_a + area_b - inter_area
    if denom <= 0.0:
        return 0.0
    return float(inter_area / denom)


class FaceTracker:
    """
    Multi-object tracker for faces with sticky dominant-target selection.
    Associates detections across frames using IoU and centroid distance.
    """

    def __init__(
        self,
        min_hits_to_confirm: int = 3,
        max_missing_frames: int = 5,
        iou_threshold: float = 0.30,
        hysteresis_area_margin: float = 1.25
    ):
        self.min_hits = min_hits_to_confirm
        self.max_missing_frames = max_missing_frames
        self.iou_threshold = iou_threshold
        self.hysteresis_margin = hysteresis_area_margin

        self.next_track_id = 1
        self.tracks: Dict[int, TrackedFace] = {}
        self.dominant_track_id: Optional[int] = None
        self.challenger_track_id: Optional[int] = None
        self.challenger_frames: int = 0

    def update(self, detections: List[FaceDetection]) -> Tuple[Optional[TrackedFace], List[TrackedFace]]:
        """
        Updates tracks with incoming frame detections and returns (dominant_target, all_active_tracks).
        """
        # Increment age and time_since_update for existing tracks
        for track in self.tracks.values():
            track.age += 1
            track.time_since_update += 1

        matched_tracks = set()
        matched_dets = set()

        # Step 1: Greedy IoU matching between existing tracks and new detections
        if self.tracks and detections:
            track_items = list(self.tracks.items())
            iou_matrix = np.zeros((len(track_items), len(detections)), dtype=np.float32)

            for i, (_, trk) in enumerate(track_items):
                for j, det in enumerate(detections):
                    iou_matrix[i, j] = compute_iou(trk.bbox, det.bbox)

            # Match greedily starting from highest IoU
            while True:
                max_val = float(iou_matrix.max()) if iou_matrix.size > 0 else 0.0
                if max_val < self.iou_threshold:
                    break
                max_idx = np.unravel_index(np.argmax(iou_matrix), iou_matrix.shape)
                t_idx, d_idx = int(max_idx[0]), int(max_idx[1])

                trk_id = track_items[t_idx][0]
                det = detections[d_idx]

                # Update matched track
                target_track = self.tracks[trk_id]
                target_track.bbox = det.bbox
                target_track.landmarks = det.landmarks
                target_track.confidence = det.confidence
                target_track.hits += 1
                target_track.time_since_update = 0
                if target_track.hits >= self.min_hits:
                    target_track.is_confirmed = True

                matched_tracks.add(trk_id)
                matched_dets.add(d_idx)

                # Clear matched row and column
                iou_matrix[t_idx, :] = -1.0
                iou_matrix[:, d_idx] = -1.0

        # Step 2: Create new tracks for unmatched detections
        for d_idx, det in enumerate(detections):
            if d_idx not in matched_dets:
                new_track = TrackedFace(
                    track_id=self.next_track_id,
                    bbox=det.bbox,
                    landmarks=det.landmarks,
                    confidence=det.confidence,
                    hits=1,
                    age=1,
                    time_since_update=0,
                    is_confirmed=(self.min_hits <= 1)
                )
                self.tracks[self.next_track_id] = new_track
                self.next_track_id += 1

        # Step 3: Prune stale tracks exceeding max_missing_frames
        pruned_ids = [t_id for t_id, trk in self.tracks.items() if trk.time_since_update > self.max_missing_frames]
        for t_id in pruned_ids:
            del self.tracks[t_id]
            if self.dominant_track_id == t_id:
                self.dominant_track_id = None
            if self.challenger_track_id == t_id:
                self.challenger_track_id = None
                self.challenger_frames = 0

        # Step 4: Sticky dominant-target selection with hysteresis
        active_tracks = [t for t in self.tracks.values() if t.time_since_update == 0]
        dominant_target = self._select_dominant_target(active_tracks)

        return dominant_target, list(self.tracks.values())

    def _select_dominant_target(self, active_tracks: List[TrackedFace]) -> Optional[TrackedFace]:
        if not active_tracks:
            return None

        # Filter to confirmed tracks if any exist, otherwise all active
        confirmed = [t for t in active_tracks if t.is_confirmed]
        candidates = confirmed if confirmed else active_tracks

        # If no current dominant target, pick the largest candidate
        if self.dominant_track_id is None or self.dominant_track_id not in self.tracks:
            largest = max(candidates, key=lambda t: t.area)
            self.dominant_track_id = largest.track_id
            self.challenger_track_id = None
            self.challenger_frames = 0
            return largest

        current_dominant = self.tracks[self.dominant_track_id]

        # Check if another candidate significantly outperforms the current dominant target
        other_candidates = [t for t in candidates if t.track_id != self.dominant_track_id]
        if other_candidates:
            top_challenger = max(other_candidates, key=lambda t: t.area)
            if top_challenger.area > current_dominant.area * self.hysteresis_margin:
                if self.challenger_track_id == top_challenger.track_id:
                    self.challenger_frames += 1
                    # Require challenger to maintain superiority for 4 consecutive frames before switching
                    if self.challenger_frames >= 4:
                        self.dominant_track_id = top_challenger.track_id
                        self.challenger_track_id = None
                        self.challenger_frames = 0
                        return top_challenger
                else:
                    self.challenger_track_id = top_challenger.track_id
                    self.challenger_frames = 1
            else:
                self.challenger_track_id = None
                self.challenger_frames = 0

        return current_dominant

    def reset(self):
        """Resets all tracks and tracker state."""
        self.tracks.clear()
        self.dominant_track_id = None
        self.challenger_track_id = None
        self.challenger_frames = 0
