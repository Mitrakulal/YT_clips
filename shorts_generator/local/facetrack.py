"""MediaPipe face tracking for the vertical reframer.

Lazy-imports mediapipe so an uninstalled package never breaks the pipeline:
if mediapipe is missing, callers fall back to the Haar cascade path.

Detection runs on a downscaled frame (max 640px wide) for CPU speed; the
returned center is scaled back to full-frame coordinates. Smoothing uses an
EMA with a deadzone so the crop glides instead of jumping between speakers.
"""
from typing import Optional, Tuple


class MediaFaceTracker:
    """Face-center tracker using MediaPipe FaceDetection (short-range model)."""

    def __init__(self, smoothing: float = 0.10, deadzone_frac: float = 0.02):
        try:
            import mediapipe as mp  # type: ignore
        except ImportError:
            raise RuntimeError("mediapipe not installed — use FACE_TRACK=haar or install mediapipe")
        self._mp = mp
        self._fd = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=0.5
        )
        self._smoothing = smoothing
        self._deadzone = deadzone_frac
        self._track: Optional[Tuple[float, float]] = None
        self._w = 0
        self._h = 0

    def track(self, frame):
        """Feed one BGR frame; return smoothed (cx, cy) or None.

        Returns None when no face has ever been detected (caller keeps its
        anchor) or when no face is present this frame (caller keeps the last
        known position). """
        h, w = frame.shape[:2]
        scale = 640.0 / max(w, h) if max(w, h) > 640 else 1.0
        small = frame if scale >= 1.0 else _resize(frame, scale)

        rgb = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=small)
        results = self._fd.process(rgb)

        cx = cy = None
        if results.detections:
            # Pick the largest face (biggest bounding box area).
            det = max(
                results.detections,
                key=lambda d: d.location_data.relative_bounding_box.width
                * d.location_data.relative_bounding_box.height,
            )
            bb = det.location_data.relative_bounding_box
            cx = (bb.xmin + bb.width / 2.0) * w
            cy = (bb.ymin + bb.height / 2.0) * h

        if cx is None or cy is None:
            return self._track  # stale position is fine — caller clamps

        if self._track is None:
            self._track = (cx, cy)
        else:
            tx, ty = self._track
            dx, dy = cx - tx, cy - ty
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > self._deadzone * w:
                self._track = (
                    tx + dx * self._smoothing,
                    ty + dy * self._smoothing,
                )
        return (int(self._track[0]), int(self._track[1]))


def _resize(frame, scale: float):
    import cv2  # type: ignore

    return cv2.resize(frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)))
