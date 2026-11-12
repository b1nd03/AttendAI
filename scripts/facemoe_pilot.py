"""Replay authorized approaches and compare AttendAI with an optional FaceMoE challenger."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import ExitStack
from itertools import batched, groupby
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'models' / 'facemoe-upstream'
CHECKPOINT = ROOT / 'models' / 'facemoe' / 'briar.pt'
SHA256 = '57087ca1e108ff5e6f59e13295e76e65f6d96e35111690d2c88fc9a474d6363c'
BEST_CROPS = 3


class FaceMoE:
    def __init__(self, device, precision):
        import torch
        from torch.overrides import TorchFunctionMode
        from torchvision.transforms.functional import InterpolationMode, resize

        if device == 'cuda' and not torch.cuda.is_available():
            raise ValueError('CUDA is unavailable. Use the isolated runtime from setup_facemoe.ps1.')
        if precision == 'fp16' and device != 'cuda':
            raise ValueError('FP16 requires CUDA. Use --precision fp32 on CPU.')
        with CHECKPOINT.open('rb') as checkpoint:
            if hashlib.file_digest(checkpoint, 'sha256').hexdigest() != SHA256:
                raise ValueError('FaceMoE checkpoint did not match the published SHA-256.')
        sys.path.insert(0, str(SOURCE))
        from backbones import get_model

        class CpuDropPathSchedule(TorchFunctionMode):
            def __torch_function__(self, func, types, args=(), kwargs=None):
                kwargs = kwargs or {}
                # Upstream immediately calls .item() on the drop-path schedule.
                if func is torch.linspace:
                    kwargs.setdefault('device', 'cpu')
                return func(*args, **kwargs)

        torch.set_num_threads(2)
        with torch.device('meta'), CpuDropPathSchedule():
            network = get_model('swin_moe', dropout=0.0, fp16=False, num_features=512, num_experts=3, k=2)
        weights = torch.load(CHECKPOINT, map_location='cpu', weights_only=True, mmap=True)
        network.load_state_dict(weights, strict=True, assign=True)
        self.dtype = torch.float16 if precision == 'fp16' else torch.float32
        self.network = network.eval().to(device=device, dtype=self.dtype)
        self.device = device
        self.torch = torch
        self.resize = resize
        self.bicubic = InterpolationMode.BICUBIC

    def embeddings(self, crops):
        torch = self.torch
        # Upstream uses RGB, bicubic 120×120, and normalization from [0, 1] to [-1, 1].
        rgb = np.ascontiguousarray(np.stack(crops)[..., ::-1])
        tensor = torch.from_numpy(rgb).permute(0, 3, 1, 2).float().div_(255)
        tensor = self.resize(tensor, [120, 120], self.bicubic, antialias=True).mul_(2).sub_(1)
        with torch.inference_mode():
            result = self.network(tensor.to(device=self.device, dtype=self.dtype)).float()
            vectors = torch.nn.functional.normalize(result, dim=1).cpu().numpy()
        if vectors.shape != (len(crops), 512) or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4):
            raise ValueError('FaceMoE produced invalid embeddings.')
        return vectors


def load_manifest(path):
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(raw, dict) or not isinstance(raw.get('enrollment'), dict)
            or not isinstance(raw.get('probes'), list) or any(not isinstance(item, dict) for item in raw['probes'])):
        raise ValueError('Manifest needs an enrollment object and a list of probe objects.')
    if not raw['enrollment'] or any(not name.strip() or not isinstance(paths, list) or len(paths) < 3
                                    for name, paths in raw['enrollment'].items()):
        raise ValueError('Provide at least three separate enrollment images per enrolled alias.')
    seen = set()
    digests = set()

    def resolve(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Each image and clip needs a local file path.')
        file = (path.parent / value).resolve(strict=True)
        if not file.is_file() or file in seen:
            raise ValueError(f'Every enrollment image and clip must be a distinct file: {file.name}')
        with file.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').digest()
        if digest in digests:
            raise ValueError(f'Every enrollment image and clip must have distinct contents: {file.name}')
        seen.add(file)
        digests.add(digest)
        return file

    enrollment = {name: [resolve(value) for value in paths] for name, paths in raw['enrollment'].items()}
    probes = []
    identifiers = set()
    sessions = {}
    split_run = any('split' in item or 'session' in item for item in raw['probes'])
    for item in raw['probes']:
        case = dict(item)
        if any(not isinstance(case.get(key), str) or not case[key].strip() for key in ('id', 'camera')):
            raise ValueError('Each approach needs a case ID and camera name.')
        case['path'] = resolve(case['path'])
        if case['id'] in identifiers or case['distance_m'] not in (5, 10, 15):
            raise ValueError('Use a unique case id and a distance of 5, 10 or 15 metres.')
        if case['person'] is not None and (not isinstance(case['person'], str) or case['person'] not in enrollment):
            raise ValueError('Probe person must be an enrolled alias or null for an unenrolled person.')
        case['presentation'] = case.get('presentation', 'live')
        if case['presentation'] not in ('live', 'photo', 'screen'):
            raise ValueError('Presentation must be live, photo, or screen.')
        if split_run:
            split, session = case.get('split'), case.get('session')
            if split not in ('calibration', 'holdout') or not isinstance(session, str) or not session.strip():
                raise ValueError('Give every clip a calibration or holdout split and a recording session ID.')
            session = session.strip()
            case['session'] = session
            if sessions.setdefault(session, split) != split:
                raise ValueError('A recording session cannot appear in both splits.')
        line = case.get('check_in_line')
        if line is not None and (line['axis'] not in ('x', 'y') or not .1 <= line['position'] <= .9
                                 or line['direction'] not in (-1, 1)):
            raise ValueError('Check-in line needs axis x or y, position from 0.1 to 0.9, and direction -1 or 1.')
        roi = case.get('roi')
        if roi is not None:
            x, y, w, h = roi
            if not (0 <= x < x + w <= 1 and 0 <= y < y + h <= 1):
                raise ValueError('ROI must be normalized [left, top, width, height] inside the frame.')
        identifiers.add(case['id'])
        probes.append(case)
    if not probes:
        raise ValueError('Provide at least one approach clip.')
    if split_run:
        for split in ('calibration', 'holdout'):
            live_cases = (case for case in probes if case['split'] == split and case['presentation'] == 'live')
            if {case['person'] is None for case in live_cases} != {False, True}:
                raise ValueError(f'The {split} split needs both enrolled and unenrolled live approaches.')
    return enrollment, probes


def native_region(frame, roi):
    if roi is None:
        return frame
    x, y, w, h = roi
    height, width = frame.shape[:2]
    region = frame[round(y * height):round((y + h) * height), round(x * width):round((x + w) * width)]
    if not region.size:
        raise ValueError('ROI is empty at the video resolution. Increase its width and height.')
    return region


class Approach:
    def __init__(self, tracker, line=None, collect_crops=True):
        self.tracker = tracker
        self.line = {f'line_{key}': value for key, value in line.items()} if line else None
        self.collect_crops = collect_crops
        self.tracks = defaultdict(lambda: {'frames': 0, 'crops': [], 'fingerprints': set()})
        self.analyzed = 0
        self.detected = 0
        self.ambiguous = 0
        self.max_face_pixels = 0
        self.rejections = Counter()
        self.identities = set()
        self.baseline_match = None
        self.baseline_verified = None
        self.liveness_pass_frames = 0
        self.check_in = None
        self.check_in_seconds = None
        self.crossed = False

    def observe(self, frame, seconds, cfg, gallery, second):
        from backend.recognition import engine, match_observation
        from backend.tracking import add_vote, line_crossing

        stamp = seconds + 1  # Replay clock starts after the track's initial vote timestamp.
        self.analyzed += 1
        samples = engine.detect_frame(frame, cfg)
        if samples:
            self.detected += 1
            largest = max(float(sample.get('face_pixels', min(sample['box'][2:]))) for sample in samples)
            self.max_face_pixels = max(self.max_face_pixels, largest)
        if len(samples) != 1:
            self.ambiguous += int(len(samples) > 1)
            self.tracker.update([], stamp)
            return
        track, sample = self.tracker.update(samples, stamp)[0]
        if not sample.get('eligible'):
            reason = 'face_too_small' if 'required_face_pixels' in sample else sample['error']
            self.rejections[reason] += 1
            return
        self.liveness_pass_frames += (sample.get('liveness') or 0) >= cfg['liveness_threshold']
        record = self.tracks[track.id]
        record['frames'] += 1
        fingerprint = hashlib.sha256(sample['sample']).digest()
        if fingerprint not in record['fingerprints']:
            record['fingerprints'].add(fingerprint)
            if self.collect_crops:
                record['crops'].append((sample['blur'], sample['aligned']))
                record['crops'].sort(key=lambda entry: entry[0], reverse=True)
                del record['crops'][BEST_CROPS:]
        person, score, gap, _ = match_observation(sample, gallery, second, cfg)
        direction_ok = True
        if self.line:
            height, width = frame.shape[:2]
            direction_ok = line_crossing(track, self.line, width, height, stamp, person) == self.line['line_direction']
            self.crossed |= direction_ok
        stable, _, live = add_vote(track, sample, person, score, gap, cfg['vote_frames'], stamp)
        if person and stable:
            self.identities.add(person)
            self.baseline_match = self.baseline_match or person
            if live >= cfg['liveness_threshold']:
                self.baseline_verified = self.baseline_verified or person
                if direction_ok and self.check_in is None:
                    self.check_in = person
                    self.check_in_seconds = round(seconds, 3)

    def report(self, model, challenger, cfg):
        from backend.recognition import normalized, rank

        track = max(self.tracks.values(), key=lambda row: (len(row['fingerprints']), row['frames']), default={})
        distinct = len(track.get('fingerprints', ()))
        if self.check_in:
            stage = 'would_check_in'
        elif self.baseline_verified:
            stage = 'crossing_unconfirmed'
        elif self.baseline_match:
            stage = 'liveness_unverified'
        elif distinct >= cfg['vote_frames']:
            stage = 'identity_unconfirmed'
        elif len(self.tracks) > 1:
            stage = 'track_fragmented'
        elif distinct:
            stage = 'insufficient_distinct_frames'
        elif self.rejections:
            stage = 'face_quality'
        elif self.detected:
            stage = 'multiple_faces'
        else:
            stage = 'no_face_detected'
        crops = [crop for _, crop in track.get('crops', ())]
        result = {
            'analyzed_frames': self.analyzed, 'detected_face_frames': self.detected,
            'ambiguous_frames': self.ambiguous, 'usable_tracks': len(self.tracks),
            'max_detected_face_pixels': round(self.max_face_pixels, 1),
            'usable_frames_in_main_track': track.get('frames', 0),
            'distinct_usable_frames_in_main_track': distinct, 'quality_rejections': dict(self.rejections),
            'baseline_identity': self.baseline_match, 'baseline_stable_identities': sorted(self.identities),
            'liveness_pass_frames': self.liveness_pass_frames,
            'baseline_liveness_verified': self.baseline_verified, 'baseline_crossing_seen': self.crossed if self.line else None,
            'baseline_would_check_in': self.check_in, 'baseline_check_in_video_seconds': self.check_in_seconds,
            'baseline_stage': stage, 'facemoe_crops': len(crops),
            'facemoe_rank1': None,
        }
        if model and crops:
            start = time.perf_counter()
            vectors = model.embeddings(crops)
            result['facemoe_ms'] = round((time.perf_counter() - start) * 1000, 1)
            person, score, margin = rank(normalized(vectors.mean(axis=0)), challenger)
            result['facemoe_rank1'] = {'person': person, 'cosine': score, 'margin': margin}
        return result


def evaluate(enrollment, probes, model, cfg, seconds):
    from backend.recognition import FaceGallery, engine
    from backend.tracking import MultiFaceTracker

    baseline, second, challenger = {}, {}, {}
    for person, paths in enrollment.items():
        samples = []
        for path in paths:
            frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError(f'Cannot decode enrollment image {path.name}.')
            faces = engine.detect_frame(frame, cfg)
            if len(faces) != 1 or not faces[0].get('eligible'):
                raise ValueError(f'Enrollment image {path.name} must contain one usable face.')
            samples.append(faces[0])
        baseline[person] = [sample['vector'] for sample in samples]
        second[person] = [engine.accuracy_vector(sample['aligned']) for sample in samples]
        if model:
            challenger[person] = [vector for group in batched(samples, BEST_CROPS)
                                  for vector in model.embeddings([sample['aligned'] for sample in group])]
    baseline = FaceGallery(baseline)
    second = FaceGallery(second) if all(v is not None for rows in second.values() for v in rows) else None
    challenger = FaceGallery(challenger)
    results = []
    for group in batched(probes, 6):
        with ExitStack() as stack:
            active = []
            for case in group:
                capture = cv2.VideoCapture(str(case['path']))
                stack.callback(capture.release)
                fps = capture.get(cv2.CAP_PROP_FPS)
                if not capture.isOpened() or not 0 < fps <= 240:
                    raise ValueError(f"Cannot read video or frame rate for case {case['id']}.")
                approach = Approach(MultiFaceTracker(), case.get('check_in_line'), collect_crops=model is not None)
                active.append((case, approach, capture, fps, max(1, round(fps / 5))))
            index = 0
            readers = active
            while readers:
                still_reading = []
                for case, approach, capture, fps, stride in readers:
                    if index / fps >= seconds or not capture.grab():
                        continue
                    still_reading.append((case, approach, capture, fps, stride))
                    if index % stride == 0:
                        ok, frame = capture.retrieve()
                        if ok:
                            approach.observe(native_region(frame, case.get('roi')), index / fps, cfg, baseline, second)
                index += 1
                readers = still_reading
            for case, approach, *_ in active:
                if not approach.analyzed:
                    raise ValueError(f"No usable video frames could be decoded for case {case['id']}.")
                result = {key: case[key] for key in ('id', 'camera', 'distance_m', 'person', 'split', 'session', 'presentation') if key in case}
                result['trigger'] = 'line' if case.get('check_in_line') else 'appearance'
                result.update(approach.report(model, challenger, cfg))
                results.append(result)
    return results, second is not None


def identification_counts(rows, field):
    """Count decisions per independent approach, including clips with no candidate."""
    from scripts.evaluate_thresholds import wilson_upper

    known = [row for row in rows if row['person'] is not None]
    unknown = [row for row in rows if row['person'] is None]
    correct = sum(row[field] == row['person'] for row in known)
    wrong = sum(row[field] is not None and row[field] != row['person'] for row in known)
    false = sum(row[field] is not None for row in unknown)
    return {
        'enrolled_approaches': len(known), 'unenrolled_approaches': len(unknown),
        'correct': correct, 'wrong_name': wrong, 'no_decision_enrolled': len(known) - correct - wrong,
        'false_identifications': false,
        'fnir': (len(known) - correct) / len(known) if known else None,
        'fpir': false / len(unknown) if unknown else None,
        'fpir_wilson_upper_95': wilson_upper(false, len(unknown)) if unknown else None,
    }


def calibrate(rows, target, margin):
    """Lock a score cutoff on calibration alone, prioritizing wrong-name avoidance."""
    from scripts.evaluate_thresholds import wilson_upper

    unknown = sum(row['person'] is None for row in rows)
    if not 0 < unknown < len(rows):
        raise ValueError('Calibration needs both enrolled and unenrolled approaches.')
    ranked = []
    for row in rows:
        candidate = row['facemoe_rank1']
        if candidate is not None and candidate['margin'] >= margin:
            truth = row['person']
            outcome = 'false' if truth is None else 'correct' if candidate['person'] == truth else 'wrong'
            ranked.append((candidate['cosine'], outcome))
    ranked.sort(reverse=True)
    counts = Counter()
    best_priority = None
    policy = {'cosine_threshold': None, 'minimum_margin': margin, 'target_fpir': target,
              'calibration_fpir_wilson_upper_95': None, 'status': 'no_candidate'}
    for score, tied in groupby(ranked, key=lambda entry: entry[0]):
        counts.update(outcome for _, outcome in tied)
        if counts['wrong'] or counts['false'] / unknown > target:
            break
        if not counts['correct']:
            continue
        upper = wilson_upper(counts['false'], unknown)
        supported = upper <= target
        priority = (supported, counts['correct'], -counts['false'], score)
        if best_priority is None or priority > best_priority:
            best_priority = priority
            policy.update(cosine_threshold=score, calibration_fpir_wilson_upper_95=upper,
                          status='confidence_bound' if supported else 'exploratory')
    return policy


def summarize(rows, target, margin, has_challenger):
    calibration = [row for row in rows if row.get('split') == 'calibration' and row.get('presentation', 'live') == 'live']
    policy = calibrate(calibration, target, margin) if calibration and has_challenger else None
    threshold = policy['cosine_threshold'] if policy else None
    for row in rows:
        candidate = row['facemoe_rank1']
        row['facemoe_identity'] = (candidate['person'] if threshold is not None and candidate
                                   and candidate['cosine'] >= threshold and candidate['margin'] >= margin else None)

    groups = defaultdict(list)
    for row in rows:
        split = row.get('split', 'all')
        groups[(split, None, None)].append(row)
        groups[(split, row['camera'], row['distance_m'])].append(row)
    summary = []
    for (split, camera, distance), approaches in groups.items():
        live = [row for row in approaches if row.get('presentation', 'live') == 'live']
        attacks = {}
        for label in ('photo', 'screen'):
            cases = [row for row in approaches if row.get('presentation') == label]
            if cases:
                attacks[label] = {
                    'approaches': len(cases),
                    'passed_frame_liveness': sum(row['liveness_pass_frames'] > 0 for row in cases),
                    'would_check_in': sum(row['baseline_would_check_in'] is not None for row in cases),
                }
        summary.append({
            'split': split, 'camera': camera, 'distance_m': distance,
            'recording_sessions': len({row['session'] for row in approaches if 'session' in row}) or None,
            'baseline_identity': identification_counts(live, 'baseline_identity'),
            'baseline_check_in_gate': identification_counts(live, 'baseline_would_check_in'),
            'facemoe_identity': identification_counts(live, 'facemoe_identity') if threshold is not None else None,
            'baseline_stages': dict(Counter(row['baseline_stage'] for row in live)),
            'presentation_attacks': attacks,
        })
    holdout = next((group['facemoe_identity'] for group in summary
                    if group['split'] == 'holdout' and group['camera'] is None), None)
    if policy is not None:
        policy['holdout_supports_fpir_target'] = bool(holdout and holdout['fpir_wilson_upper_95'] <= target)
    return policy, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    parser.add_argument('--precision', choices=('fp16', 'fp32'), default='fp32')
    parser.add_argument('--clip-seconds', type=float, default=15)
    parser.add_argument('--baseline-only', action='store_true', help='Run diagnostics without loading FaceMoE or CUDA.')
    parser.add_argument('--target-fpir', type=float, default=.01, help='Calibration target per unenrolled approach, default 0.01.')
    parser.add_argument('--facemoe-margin', type=float, default=.10, help='Fixed runner-up margin for FaceMoE, default 0.10.')
    args = parser.parse_args()
    if not 0 < args.clip_seconds <= 120:
        parser.error('--clip-seconds must be between 0 and 120.')
    if not 0 < args.target_fpir < 1 or not 0 <= args.facemoe_margin <= 2:
        parser.error('Target FPIR must be between 0 and 1. FaceMoE margin must be between 0 and 2.')
    sys.path.insert(0, str(ROOT))
    cv2.setNumThreads(2)
    try:
        if args.output.exists():
            raise ValueError('Choose a new output filename.')
        enrollment, probes = load_manifest(args.manifest)
        with tempfile.TemporaryDirectory(prefix='attendai-facemoe-') as workspace:
            os.environ['PRESENCE_DATA_DIR'] = workspace
            from backend.store import DEFAULTS

            model = None if args.baseline_only else FaceMoE(args.device, args.precision)
            rows, has_second_gallery = evaluate(enrollment, probes, model, DEFAULTS, args.clip_seconds)
            policy, summary = summarize(rows, args.target_fpir, args.facemoe_margin, model is not None)
            gpu = model is not None and args.device == 'cuda'
            report = {
                'schema_version': 2,
                'model': 'FaceMoE Swin-B WebFace4M BRIAR' if model else None,
                'checkpoint_sha256': SHA256 if model else None,
                'device': model.torch.cuda.get_device_name() if gpu else 'CPU',
                'precision': args.precision if model else None, 'clip_seconds': args.clip_seconds,
                'cuda_peak_allocated_mib': round(model.torch.cuda.max_memory_allocated() / 2**20, 1) if gpu else None,
                'mode': 'offline single-approach shadow replay',
                'enrolled_aliases': len(enrollment), 'baseline_auraface_gallery': has_second_gallery,
                'baseline_settings': {key: DEFAULTS[key] for key in ('min_face_size', 'min_blur', 'match_threshold',
                                                                      'match_margin', 'vote_frames', 'liveness_threshold')},
                'facemoe_policy': policy, 'summary': summary, 'cases': rows,
            }
            output = json.dumps(report, indent=2, allow_nan=False)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as file:
                file.write(output)
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError, cv2.error) as exc:
        parser.exit(2, f'Pilot failed: {exc}\n')
    print(f'Wrote {len(rows)} cases to {args.output}')
    for group in summary:
        if group['camera'] is None:
            gates = group['baseline_check_in_gate']
            print(f"{group['split']}: {gates['correct']}/{gates['enrolled_approaches']} correct baseline check-ins, "
                  f"{gates['wrong_name']} wrong names, {gates['false_identifications']}/{gates['unenrolled_approaches']} false check-ins")
            for kind, attacks in group['presentation_attacks'].items():
                print(f"  {kind} attacks: {attacks['would_check_in']}/{attacks['approaches']} would check in")
    if policy:
        print(f"FaceMoE threshold status: {policy['status']}")


if __name__ == '__main__':
    main()
