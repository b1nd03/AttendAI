"""CPU recognition, open-set matching, quality gating and temporal consensus.

OpenCV YuNet + SFace (128-D). Scores are cosine similarities, not probabilities.
The inference lock is necessary: OpenCV DNN instances have mutable input state.
"""
import hashlib
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .tracking import iou
from .centerface import CenterFace

MODELS = Path(__file__).resolve().parents[1] / "models"
CENTERFACE_MODEL = MODELS / "centerface.onnx"
ACCURACY_MODEL = MODELS / "auraface.onnx"
ACCURACY_VERSION = "auraface-r100-v1"
ACCURACY_THRESHOLD = .46
ACCURACY_MARGIN = .12

class FaceSizeError(ValueError):
    def __init__(self, pixels, required):
        self.pixels = int(pixels)
        self.required = required
        super().__init__(f'Face detail is {self.pixels} pixels across. This workspace requires {required}. '
                         'Move closer or use optical zoom.')

def face_detail(aligned):
    # SFace aligns to 112x112. Measure the inner face, excluding the hair
    # and frame edges, and suppress fine sensor noise before measuring detail.
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)[28:98, 17:95]
    filtered = cv2.GaussianBlur(gray, (3, 3), 0.6)
    sharpness = float(cv2.Laplacian(filtered, cv2.CV_32F).var())
    return sharpness, float(gray.mean())

def normalized(v):
    v = np.asarray(v, dtype=np.float32).ravel()
    n = np.linalg.norm(v)
    if not np.isfinite(n) or n < 1e-8:
        raise ValueError("Invalid face template. Capture another image.")
    return v / n

class FaceGallery:
    """A reusable CPU search index over the active, normalized templates."""

    def __init__(self, templates):
        self.people = tuple(templates)
        if not self.people:
            return
        rows = list(templates.values())
        width = max(2, max(map(len, rows)))
        self.vectors = np.zeros((len(rows), width, len(rows[0][0])), dtype=np.float32)
        self.present = np.zeros((len(rows), width), dtype=bool)
        for index, row in enumerate(rows):
            self.vectors[index, :len(row)] = row
            self.present[index, :len(row)] = True

    def scores(self, vector):
        scores = np.einsum("ptd,d->pt", self.vectors, vector)
        scores[~self.present] = -np.inf
        best_two = np.partition(scores, -2, axis=1)[:, -2:]
        return np.where(np.isfinite(best_two[:, 0]), best_two.mean(axis=1), best_two[:, 1])


def rank(vector, gallery):
    if not isinstance(gallery, FaceGallery):
        gallery = FaceGallery(gallery)
    if not gallery.people:
        return None, 0.0, 0.0
    # Preserve the mean of the best two templates and the runner-up rule.
    scores = gallery.scores(vector)
    best = int(scores.argmax())
    person_id, score = gallery.people[best], float(scores[best])
    scores[best] = -np.inf
    gap = score - (float(scores.max()) if len(scores) > 1 else 0.0)
    return person_id, score, gap


def match(vector, gallery, threshold, margin):
    person_id, score, gap = rank(vector, gallery)
    return (person_id if score >= threshold and gap >= margin else None), score, gap


def match_observation(sample, gallery, accuracy_gallery, cfg):
    """Recover close misses only when the second model supports the same identity."""
    candidate, score, gap = rank(sample['vector'], gallery)
    threshold, margin = cfg['match_threshold'], cfg['match_margin']
    accepted = candidate if score >= threshold and gap >= margin else None
    aligned = sample.get('aligned')
    if (
        accepted is not None or accuracy_gallery is None or aligned is None or candidate is None
        or score < max(.42, threshold - .08) or gap < max(.04, margin - .06)
    ):
        return accepted, score, gap, False
    second_vector = engine.accuracy_vector(aligned)
    if second_vector is None:
        return accepted, score, gap, False
    confirmed, _, _ = match(second_vector, accuracy_gallery, ACCURACY_THRESHOLD, ACCURACY_MARGIN)
    return (candidate if confirmed == candidate else None), score, gap, True

@dataclass
class Track:
    box: np.ndarray
    vector: np.ndarray
    touched: float
    votes: deque = field(default_factory=lambda: deque(maxlen=10))
    fingerprints: deque = field(default_factory=lambda: deque(maxlen=20))
    completed: bool = False
    last_vote: float = 0

class Consensus:
    """One-person kiosk tracks: expire, reset on identity/position change, vote once/frame."""
    def __init__(self):
        self.tracks = {}

    def clear(self):
        self.tracks.clear()

    def add(self, stream, box, vector, person_id, fingerprint, frames):
        t = time.monotonic()
        self.tracks = {k: v for k, v in self.tracks.items() if t - v.touched < 4}
        track = self.tracks.get(stream)
        if track is not None:
            x1 = np.maximum(track.box[:2], box[:2])
            x2 = np.minimum(track.box[:2]+track.box[2:], box[:2]+box[2:])
            intersection = float(np.prod(np.maximum(0, x2-x1)))
            union = float(np.prod(track.box[2:])+np.prod(box[2:])-intersection)
            if intersection/max(union, 1) < 0.15 or float(track.vector @ vector) < 0.65:
                track = None
        if track is None:
            track = Track(box.copy(), vector.copy(), t)
            self.tracks[stream] = track
        track.box, track.touched = box.copy(), t
        # Retries and identical JPEG replays cannot contribute extra votes.
        if fingerprint not in track.fingerprints and t-track.last_vote >= 0.18:
            track.fingerprints.append(fingerprint)
            track.votes.append(person_id or "unknown")
            track.last_vote = t
        needed = frames
        votes = list(track.votes)[-needed:]
        winner, count = Counter(votes).most_common(1)[0] if votes else ("unknown",0)
        stable = len(votes) >= needed and count >= needed and winner == (person_id or "unknown")
        return track, stable, min(count, needed)

class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.load_lock = threading.Lock()
        self.load_finished = False
        self.detector = None
        self.centerface = None
        self.centerface_failed = False
        self.recognizer = None
        self.anti_spoof = None
        self.error = None
        self.consensus = Consensus()
        self.gallery_cache = None
        self.accuracy_cache = None
        self.accuracy_net = None
        self.accuracy_lock = threading.Lock()
        self.accuracy_failed = False
        self.gallery_epoch = 0

    def invalidate_gallery(self):
        self.gallery_cache = None
        self.accuracy_cache = None
        self.gallery_epoch += 1
        self.consensus.clear()

    def load(self):
        if self.load_finished:
            return
        with self.load_lock:
            if self.load_finished:
                return
            try:
                detector = cv2.FaceDetectorYN.create(str(MODELS/"yunet.onnx"), "", (640,480), 0.9, 0.3, 5000)
                recognizer = cv2.FaceRecognizerSF.create(str(MODELS/"sface.onnx"), "")
            except cv2.error:
                self.error = "Face models are missing. Run setup.ps1 to download and verify them."
                self.load_finished = True
                return
            try:
                from .liveness import AntiSpoof
                self.anti_spoof = AntiSpoof(MODELS)
            except (ImportError, OSError, RuntimeError):
                self.anti_spoof = None
            self.detector = detector
            self.recognizer = recognizer
            self.load_finished = True

    def status(self):
        return {"recognition": self.recognizer is not None, "liveness": self.anti_spoof is not None,
                "model": "SFace / YuNet", "dimensions": 128, "device": "CPU", "error": self.error,
                "recovery_detector": "CenterFace" if not self.centerface_failed and CENTERFACE_MODEL.is_file() else None,
                "accuracy_model": "AuraFace R100" if self.accuracy_available() else None,
                "warming": not self.load_finished}

    def accuracy_available(self):
        return not self.accuracy_failed and ACCURACY_MODEL.is_file()

    def accuracy_vector(self, aligned):
        if not self.accuracy_available():
            return None
        with self.accuracy_lock:
            try:
                if self.accuracy_net is None:
                    self.accuracy_net = cv2.dnn.readNetFromONNX(str(ACCURACY_MODEL))
                blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112),
                                             (127.5, 127.5, 127.5), swapRB=True)
                self.accuracy_net.setInput(blob)
                return normalized(self.accuracy_net.forward())
            except (cv2.error, ValueError):
                self.accuracy_failed = True
                return None

    def extract(self, raw, cfg):
        self.load()
        if self.detector is None:
            raise ValueError(self.error)
        if len(raw) > 5_000_000:
            raise ValueError("Image must be smaller than 5 MB.")
        # Check dimensions before decode to reject decompression bombs.
        from PIL import Image
        import io
        try:
            with Image.open(io.BytesIO(raw)) as meta:
                if meta.width * meta.height > 12_000_000:
                    raise ValueError("Image must be at most 12 megapixels.")
        except (OSError, Image.DecompressionBombError):
            raise ValueError("Use a valid JPEG or PNG image.") from None
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Image could not be read. Use JPEG or PNG.")
        h, w = frame.shape[:2]
        if max(h,w)>1280:
            frame = cv2.resize(frame, (int(w*1280/max(h,w)), int(h*1280/max(h,w))))
        h, w = frame.shape[:2]
        with self.lock:
            self.detector.setInputSize((w,h))
            _, faces = self.detector.detect(frame)
            if faces is None or len(faces)==0:
                raise ValueError("No face found. Face the camera and improve the lighting.")
            if len(faces) != 1:
                raise ValueError("One person at a time. Keep other faces outside the camera view.")
            sample=self.face_features(frame,faces[0],cfg)
        return {**sample,"fingerprint": hashlib.sha256(raw).hexdigest()}

    def face_features(self, frame, face, cfg, source_detail=None, *, quality_only=False):
        """Caller holds inference lock. Used for enrollment AND each face in a room."""
        h,w=frame.shape[:2]
        x,y,bw,bh=face[:4]
        pixels = min(bw, bh) if source_detail is None else source_detail
        if pixels<cfg['min_face_size']:
            raise FaceSizeError(pixels, cfg['min_face_size'])
        if x<0 or y<0 or x+bw>w or y+bh>h:
            raise ValueError('Face is partly outside the frame.')
        aligned=self.recognizer.alignCrop(frame,face)
        detail, brightness = face_detail(aligned)
        if brightness <= 40:
            raise ValueError('The face is too dark. Try a light in front of you.')
        if brightness >= 225:
            raise ValueError('The face is overexposed. Move away from direct light.')
        if detail < cfg['min_blur']:
            raise ValueError('The face looks soft. Hold still or add light so the camera can focus.')
        if quality_only:
            return {'box': face[:4].copy(), 'eligible': True, 'face_pixels': int(pixels)}
        crop=frame[int(y):int(y+bh),int(x):int(x+bw)]
        vector=normalized(self.recognizer.feature(aligned))
        live=self.anti_spoof.predict(frame,face[:4]) if self.anti_spoof else None
        _,jpeg=cv2.imencode('.jpg',cv2.resize(crop,(160,160)),[cv2.IMWRITE_JPEG_QUALITY,80])
        # Keep the previous raw-crop quality floor for unattended gallery updates.
        legacy_detail = None
        if cfg['auto_learn'] and detail >= 144:
            raw_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            legacy_detail = float(cv2.Laplacian(raw_gray, cv2.CV_32F).var())
        return {"vector": vector, "aligned": aligned, "box": face[:4].copy(), "sample": jpeg.tobytes(),
                "quality": min(1.0, detail/180), "blur": round(detail,1), "brightness": round(brightness),
                "learning_quality": min(1.0, detail/180, (legacy_detail or 0)/180),
                "liveness": live, "width": w, "height": h, "eligible":True}

    def locate_faces(self, frame, limit, minimum_edge=0):
        """Return YuNet boxes and landmarks in the input image's coordinates."""
        h, w = frame.shape[:2]
        edge = max(h, w)
        target = min(limit, max(minimum_edge, edge))
        if target == edge:
            resized = frame
        else:
            resized = cv2.resize(
                frame, (max(1, round(w * target / edge)), max(1, round(h * target / edge))),
                interpolation=cv2.INTER_AREA if target < edge else cv2.INTER_LINEAR,
            )
        rh, rw = resized.shape[:2]
        self.detector.setInputSize((rw, rh))
        _, faces = self.detector.detect(resized)
        if faces is None:
            return []
        if target != edge:
            faces[:, :14:2] *= w / rw
            faces[:, 1:14:2] *= h / rh
        return faces

    def sample_face(self, frame, face, cfg, source_detail=None, *, quality_only=False):
        try:
            return self.face_features(frame, face, cfg, source_detail, quality_only=quality_only)
        except FaceSizeError as exc:
            return {'box': face[:4].copy(), 'eligible': False, 'error': str(exc),
                    'face_pixels': exc.pixels, 'required_face_pixels': exc.required}
        except ValueError as exc:
            return {'box': face[:4].copy(), 'eligible': False, 'error': str(exc),
                    'face_pixels': int(min(face[2:4]) if source_detail is None else source_detail)}

    def recover_frame(self, frame, cfg, angle, *, enhanced=False, quality_only=False):
        """Search one padded orientation, then use only real source pixels."""
        self.load()
        if self.detector is None:
            return []
        height, width = frame.shape[:2]
        scale = min(1., 1280 / max(height, width))
        source = frame if scale == 1 else cv2.resize(
            frame, (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        h, w = source.shape[:2]
        pad = round(min(h, w) * .14)
        transform = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.)
        transform[:, 2] += pad
        search = cv2.warpAffine(source, transform, (w + 2 * pad, h + 2 * pad),
                                borderValue=(80, 80, 80))
        inverse = cv2.invertAffineTransform(transform)
        source_scale = np.float32([width / w, height / h])
        bounds = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
        with self.lock:
            faces = self.locate_recovery_faces(search, enhanced)
            results = []
            for face in faces[:25]:
                x, y, fw, fh = face[:4]
                rect = np.float32([[[x, y], [x + fw, y], [x + fw, y + fh], [x, y + fh]]])
                polygon = cv2.transform(rect, inverse)[0] * source_scale
                visible_area, _ = cv2.intersectConvexConvex(polygon, bounds)
                area = cv2.contourArea(polygon)
                if area <= 0 or visible_area < .5 * area:
                    continue
                landmarks = cv2.transform(face[4:14].reshape(1, 5, 2), inverse)[0] * source_scale
                low = np.maximum(0, polygon.min(axis=0))
                high = np.minimum([width, height], polygon.max(axis=0))
                face[:4] = [*low, *(high - low)]
                face[4:14] = landmarks.ravel()
                if visible_area < .95 * area or np.any((landmarks < 0) | (landmarks >= [width, height])):
                    results.append({
                        'box': face[:4].copy(), 'eligible': False,
                        'error': 'Part of the face is outside the camera view. Move toward the center.',
                    })
                    continue
                native_detail = min(np.linalg.norm(polygon[1] - polygon[0]), np.linalg.norm(polygon[3] - polygon[0]))
                results.append(self.sample_face(frame, face, cfg, native_detail, quality_only=quality_only))
            return results

    def locate_recovery_faces(self, search, enhanced):
        if enhanced and not self.centerface_failed and CENTERFACE_MODEL.is_file():
            try:
                if self.centerface is None:
                    self.centerface = CenterFace(CENTERFACE_MODEL)
                return self.centerface.locate(search)
            except cv2.error:
                # An unsupported or damaged secondary model leaves YuNet usable.
                self.centerface_failed = True
        return self.locate_faces(search, 832)

    def detect_native_tile(self, frame, cfg, position, *, quality_only=False):
        """Search one overlapping crop and score faces in full-frame pixels."""
        self.load()
        if self.detector is None:
            raise ValueError(self.error)
        height, width = frame.shape[:2]
        tile_width, tile_height = round(width * .36), round(height * .36)
        left = round((width - tile_width) * position[0])
        top = round((height - tile_height) * position[1])
        with self.lock:
            faces = self.locate_faces(frame[top:top+tile_height, left:left+tile_width], 1440)
            for face in faces:
                face[:2] += [left, top]
                face[4:14:2] += left
                face[5:14:2] += top
            return [self.sample_face(frame, face, cfg, quality_only=quality_only) for face in faces[:25]]

    def detect_frame(self, frame, cfg, people=None, *, quality_only=False):
        """Detect all visible faces. Poor faces never prevent other people being processed."""
        self.load()
        if self.detector is None:
            raise ValueError(self.error)
        h, w = frame.shape[:2]
        # The distance profile uses native-resolution head crops plus a bounded
        # whole-frame pass. Standard mode preserves the existing input policy.
        edge = max(h, w)
        limit = 832 if people is not None else max(832, (40 * edge + cfg['min_face_size'] - 1) // cfg['min_face_size'])
        with self.lock:
            faces = []
            if people is not None:
                for x, y, bw, bh in people:
                    left, right = max(0, int(x - .18 * bw)), min(w, int(x + 1.18 * bw))
                    top, bottom = max(0, int(y - .16 * bh)), min(h, int(y + .46 * bh))
                    if right - left < 16 or bottom - top < 16:
                        continue
                    for face in self.locate_faces(frame[top:bottom, left:right], 512, 288):
                        face[:2] += [left, top]
                        face[4:14:2] += left
                        face[5:14:2] += top
                        if not any(iou(face[:4], other[:4]) > .35 for other in faces):
                            faces.append(face)
            whole_frame = self.locate_faces(frame, limit)
            crop_faces = tuple(faces)
            faces.extend(
                face for face in whole_frame
                if not any(iou(face[:4], other[:4]) > .45 for other in crop_faces)
            )
            return [self.sample_face(frame, face, cfg, quality_only=quality_only) for face in faces[:25]]

engine = Engine()
