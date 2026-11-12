"""CPU person proposals from the pinned OpenCV Zoo NanoDet model."""
from functools import lru_cache

import cv2
import numpy as np

from .recognition import MODELS

PERSON_MODEL = MODELS / 'nanodet.onnx'
INPUT_SIZE = 416
PERSON_SCORE = .35


class PersonDetector:
    def __init__(self, model):
        self.net = cv2.dnn.readNet(str(model))
        self.outputs = self.net.getUnconnectedOutLayersNames()
        self.bins = np.arange(8, dtype=np.float32)
        self.strides = (8, 16, 32, 64)
        self.anchors = []
        for stride in self.strides:
            axis = np.arange(INPUT_SIZE // stride) * stride + (stride - 1) / 2
            x, y = np.meshgrid(axis, axis)
            self.anchors.append(np.column_stack((x.ravel(), y.ravel())))

    def proposals(self, frame):
        height, width = frame.shape[:2]
        scale = INPUT_SIZE / max(height, width)
        rw, rh = max(1, round(width * scale)), max(1, round(height * scale))
        xpad, ypad = (INPUT_SIZE - rw) // 2, (INPUT_SIZE - rh) // 2
        canvas = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.float32)
        image = cv2.resize(frame, (rw, rh), interpolation=cv2.INTER_LINEAR)
        canvas[ypad:ypad + rh, xpad:xpad + rw] = image
        canvas -= np.array([103.53, 116.28, 123.675], dtype=np.float32)
        canvas /= np.array([57.375, 57.12, 58.395], dtype=np.float32)
        self.net.setInput(cv2.dnn.blobFromImage(canvas))
        output = self.net.forward(self.outputs)
        proposals = []
        for stride, anchors, scores, offsets in zip(self.strides, self.anchors, output[::2], output[1::2]):
            scores = scores.reshape(-1, 80)[:, 0]
            keep = scores > PERSON_SCORE
            if not keep.any():
                continue
            distribution = offsets.reshape(-1, 4, 8)[keep]
            weights = np.exp(distribution - distribution.max(axis=2, keepdims=True))
            distance = (weights @ self.bins) / weights.sum(axis=2) * stride
            points = anchors[keep]
            low = (points - distance[:, :2] - [xpad, ypad]) * [width / rw, height / rh]
            high = (points + distance[:, 2:] - [xpad, ypad]) * [width / rw, height / rh]
            low = np.clip(low, [0, 0], [width, height])
            high = np.clip(high, [0, 0], [width, height])
            for origin, extent, score in zip(low, high - low, scores[keep]):
                if min(extent) >= 8:
                    proposals.append(([*origin, *extent], float(score)))
        return proposals

    def detect(self, frame):
        proposals = self.proposals(frame)
        height, width = frame.shape[:2]
        if width >= 1920 and width / height >= 1.4:
            # Overlapping views make distant subjects larger at the network
            # input. The full frame preserves context for people near the lens.
            tile_width = int(width * .60)
            for left in (0, width - tile_width):
                for box, score in self.proposals(frame[:, left:left + tile_width]):
                    box[0] += left
                    proposals.append((box, score))
        if not proposals:
            return []
        boxes, scores = zip(*proposals)
        picked = cv2.dnn.NMSBoxes(boxes, scores, PERSON_SCORE, .42)
        return [np.asarray(boxes[i], dtype=np.float32) for i in picked[:12]]


class PersonMotion:
    """Follow anonymous proposals between detector scans on a small gray image."""

    def __init__(self):
        self.gray = None
        self.tracks = []

    def update(self, frame, detections=None):
        if detections is not None and not detections:
            self.tracks = []
        if not self.tracks and not detections:
            self.gray = None
            return []

        height, width = frame.shape[:2]
        ratio = min(1., 832 / max(height, width))
        small = cv2.resize(frame, (max(1, round(width * ratio)), max(1, round(height * ratio))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        scale = np.array([width / gray.shape[1], height / gray.shape[0]], dtype=np.float32)
        if detections is not None:
            self.tracks = [(box, self.seed(gray, box, scale)) for box in detections]
        elif self.gray is not None and self.gray.shape == gray.shape:
            self.follow(gray, scale)
        self.gray = gray
        return [box for box, _ in self.tracks]

    @staticmethod
    def seed(gray, box, scale):
        x, y, w, h = box
        left, top = np.maximum(0, np.int32([x + .18 * w, y + .15 * h] / scale))
        right, bottom = np.int32([x + .82 * w, y + .83 * h] / scale)
        crop = gray[top:bottom, left:right]
        if min(crop.shape) < 5:
            return None
        points = cv2.goodFeaturesToTrack(crop, maxCorners=12, qualityLevel=.02, minDistance=3)
        return points + np.array([left, top], dtype=np.float32) if points is not None else None

    def follow(self, gray, scale):
        groups = [points for _, points in self.tracks if points is not None]
        if not groups:
            return
        previous = np.concatenate(groups)
        current, status, error = cv2.calcOpticalFlowPyrLK(
            self.gray, gray, previous, None, winSize=(15, 15), maxLevel=2,
        )
        if current is None:
            return
        valid = status.ravel().astype(bool) & (error.ravel() < 22)
        frame_end = np.array([gray.shape[1], gray.shape[0]]) * scale
        offset = 0
        tracks = []
        for box, points in self.tracks:
            if points is None:
                tracks.append((box, None))
                continue
            end = offset + len(points)
            good = valid[offset:end]
            tracked = current[offset:end][good]
            if len(tracked) >= 3:
                movement = np.median(tracked[:, 0] - points[good, 0], axis=0) * scale
                origin = box[:2] + movement
                low = np.clip(origin, 0, frame_end)
                high = np.clip(origin + box[2:], 0, frame_end)
                if min(high - low) >= 8:
                    tracks.append((np.concatenate((low, high - low)), tracked))
            offset = end
        self.tracks = tracks


@lru_cache(maxsize=1)
def person_detector():
    return PersonDetector(PERSON_MODEL)
