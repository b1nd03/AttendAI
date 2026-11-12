import json

import numpy as np
import pytest

from scripts.facemoe_pilot import Approach, load_manifest, native_region, summarize


def test_manifest_separates_enrollment_and_unknown_approaches(tmp_path):
    images = ['enroll-a.jpg', 'enroll-b.jpg', 'enroll-c.jpg']
    for name in [*images, 'probe.avi']:
        (tmp_path / name).write_text(name, encoding='utf-8')
    path = tmp_path / 'manifest.json'
    contents = {
        'enrollment': {'member-01': images},
        'probes': [{'id': 'pass-01', 'camera': 'entrance', 'distance_m': 15,
                    'person': None, 'path': 'probe.avi', 'roi': [.25, .25, .5, .5]}],
    }
    path.write_text(json.dumps(contents), encoding='utf-8')
    enrollment, probes = load_manifest(path)
    assert enrollment['member-01'][0] == tmp_path / images[0]
    assert probes[0]['person'] is None
    assert probes[0]['path'] == tmp_path / 'probe.avi'

    contents['probes'][0]['path'] = images[0]
    path.write_text(json.dumps(contents), encoding='utf-8')
    with pytest.raises(ValueError, match='distinct file'):
        load_manifest(path)


def test_split_sessions_and_copied_clips_are_rejected(tmp_path):
    files = [f'sample-{index}' for index in range(7)]
    for name in files:
        (tmp_path / name).write_text(name, encoding='utf-8')
    probes = [
        {'id': str(i), 'camera': 'entry', 'distance_m': 10, 'person': 'member' if i % 2 else None,
         'path': files[i], 'split': 'calibration' if i < 5 else 'holdout', 'session': f'session-{i}'}
        for i in range(3, 7)
    ]
    path = tmp_path / 'manifest.json'
    def save():
        path.write_text(json.dumps({'enrollment': {'member': files[:3]}, 'probes': probes}), encoding='utf-8')

    save()
    assert len(load_manifest(path)[1]) == 4
    probes[2]['session'] = probes[0]['session']
    save()
    with pytest.raises(ValueError, match='cannot appear in both splits'):
        load_manifest(path)
    probes[2]['session'] = 'another-session'
    (tmp_path / files[5]).write_bytes((tmp_path / files[3]).read_bytes())
    save()
    with pytest.raises(ValueError, match='distinct contents'):
        load_manifest(path)


def test_holdout_cannot_influence_cutoff_and_counts_wrong_names_separately():
    def row(split, person, guess, score):
        return {'split': split, 'person': person, 'camera': 'entry', 'distance_m': 10,
                'baseline_identity': guess, 'baseline_would_check_in': None, 'baseline_stage': 'liveness_unverified',
                'facemoe_rank1': {'person': guess, 'cosine': score, 'margin': .2} if guess else None}

    rows = [row('calibration', 'member', 'member', .85), row('calibration', None, 'member', .7),
            row('holdout', 'member', 'someone-else', .95), row('holdout', None, 'member', .99),
            row('holdout', None, None, 0),
            {**row('calibration', None, 'member', .999), 'presentation': 'photo',
             'baseline_would_check_in': 'member', 'liveness_pass_frames': 3}]
    policy, groups = summarize(rows, .01, .10, True)
    assert policy['cosine_threshold'] == .85 and policy['status'] == 'exploratory'
    assert not policy['holdout_supports_fpir_target']
    holdout = next(group for group in groups if group['split'] == 'holdout' and group['camera'] is None)
    assert holdout['facemoe_identity']['wrong_name'] == 1
    assert holdout['facemoe_identity']['fpir'] == .5
    assert holdout['baseline_check_in_gate']['fpir'] == 0
    calibration = next(group for group in groups if group['split'] == 'calibration' and group['camera'] is None)
    assert calibration['facemoe_identity']['unenrolled_approaches'] == 1
    assert calibration['presentation_attacks']['photo'] == {
        'approaches': 1, 'passed_frame_liveness': 1, 'would_check_in': 1,
    }

    rows[0]['facemoe_rank1']['cosine'] = .7  # A tie must accept or reject both calibration candidates.
    policy, _ = summarize(rows, .01, .10, True)
    assert policy['cosine_threshold'] is None and policy['status'] == 'no_candidate'


def test_replay_reuses_identity_liveness_and_crossing_gates(monkeypatch):
    from backend.recognition import FaceGallery, engine
    from backend.store import DEFAULTS
    from backend.tracking import MultiFaceTracker

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    vector = np.array([1., 0.], dtype=np.float32)
    gallery = FaceGallery({'member': [vector]})

    def replay(direction, live):
        approach = Approach(MultiFaceTracker(), {'axis': 'x', 'position': .5, 'direction': direction}, collect_crops=False)
        for index, x in enumerate((85, 95, 107, 124, 135, 143)):
            sample = {'box': np.array([x, 50, 100, 100]), 'eligible': True, 'vector': vector,
                      'quality': 1., 'liveness': live, 'sample': str(index).encode()}
            monkeypatch.setattr(engine, 'detect_frame', lambda *_: [sample])
            approach.observe(frame, index * .2, DEFAULTS, gallery, None)
        return approach.report(None, None, DEFAULTS)

    accepted = replay(1, .99)
    assert accepted['baseline_would_check_in'] == 'member' and accepted['baseline_check_in_video_seconds'] == .8
    assert accepted['liveness_pass_frames'] == 6
    wrong_direction = replay(-1, .99)
    assert wrong_direction['baseline_would_check_in'] is None
    assert wrong_direction['baseline_stage'] == 'crossing_unconfirmed'
    weak_live = replay(1, .5)
    assert weak_live['baseline_would_check_in'] is None and weak_live['baseline_stage'] == 'liveness_unverified'


def test_region_retains_native_pixels():
    image = np.arange(240, dtype=np.uint8).reshape((8, 10, 3))
    region = native_region(image, [.2, .25, .6, .5])
    assert region.shape == (4, 6, 3)
    assert np.array_equal(region, image[2:6, 2:8])
