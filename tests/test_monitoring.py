import hashlib
import json
import threading
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.monitoring import CameraWorker,CameraManager,RoomProcessor,bounded_frame,manager
from backend.recognition import ACCURACY_VERSION, engine, normalized
from backend.store import CIPHER,db,settings,now,uid,pack
from backend.tracking import MultiFaceTracker,line_crossing

def vec(i):
    result=np.zeros(128,dtype=np.float32);result[i]=1;return result

def person(index=0,name='Ahmad'):
    pid=uid()
    with db() as con:
        con.execute('INSERT INTO people VALUES (?,?,?,?,?,?)',(pid,name,'ID-'+str(index),'Room',now(),now()))
        for _ in range(3):con.execute('INSERT INTO templates VALUES (?,?,?,?,?,1,NULL,NULL,?)',(uid(),pid,pack(vec(index)),1,'enrollment',now()))
    engine.invalidate_gallery()
    return pid

def camera(admin,name='Camera 1',**extra):
    body={'name':name,'location':'Room 1','source_type':'network','source':'rtsp://user:secret@192.0.2.1:554/stream','authorized':True,**extra}
    response=admin.post('/api/cameras',json=body)
    assert response.status_code==200,response.text
    with db() as con:return dict(con.execute('SELECT * FROM cameras WHERE id=?',(response.json()['id'],)).fetchone())

def sample(index=0,frame=0,x=40,live=.99):
    return {'box':np.array([x,40,110,130],dtype=np.float32),'vector':vec(index),'sample':f'crop-{index}-{frame}'.encode(),
            'quality':1.,'liveness':live,'eligible':True,'fingerprint':f'{index}-{frame}'}

def frames(processor,monkeypatch,samples,number=5):
    with db() as con:cfg=settings(con)
    output=[]
    for i in range(number):
        current=samples(i)
        monkeypatch.setattr(engine,'detect_frame',lambda frame,cfg,s=current:s)
        for track in processor.tracker.tracks.values():track.last_voted-=1
        output=processor.process(np.zeros((360,640,3),dtype=np.uint8),cfg)
    return output

def test_camera_credentials_encrypted_not_returned(admin):
    c=camera(admin)
    listing=admin.get('/api/cameras').json()
    assert 'secret' not in json.dumps(listing) and 'rtsp://' not in json.dumps(listing)
    assert c['source']!=b'rtsp://user:secret@192.0.2.1:554/stream'
    assert CIPHER.decrypt(c['source']).startswith(b'rtsp://user:secret@')
    assert listing['items'][0]['source_hint']=='192.0.2.1'

def test_camera_sources_authorization_and_limits(admin):
    base={'name':'Camera','location':'Room','source_type':'network','authorized':True}
    for value in ['file:///D:/secret.txt','not-an-ip','ftp://192.0.2.1/live']:
        assert admin.post('/api/cameras',json={**base,'source':value}).status_code==422
    assert admin.post('/api/cameras',json={**base,'source':'rtsp://192.0.2.1/stream','authorized':False}).status_code==422
    assert admin.post('/api/cameras',json={**base,'source_type':'usb','source':'-1'}).status_code==422
    c=camera(admin,source_type='usb',source='0')
    assert c['source_hint']=='USB device 0'
    assert admin.get(f"/api/cameras/{c['id']}/snapshot").status_code==404

def test_room_distance_preference_is_persisted(admin):
    source = camera(admin)
    url = f"/api/cameras/{source['id']}/distance"
    assert admin.patch(url, json={'enabled': True}).json() == {'distance_mode': True}
    assert admin.get('/api/cameras').json()['items'][0]['distance_mode'] is True
    assert admin.patch(url, json={'enabled': False}).json() == {'distance_mode': False}
    assert admin.get('/api/cameras').json()['items'][0]['distance_mode'] is False

def test_body_proposals_alone_cannot_create_a_sighting(admin, monkeypatch):
    from types import SimpleNamespace
    body = np.array([220, 15, 150, 330], dtype=np.float32)
    monkeypatch.setattr('backend.monitoring.person_detector', lambda: SimpleNamespace(detect=lambda _: [body]))
    monkeypatch.setattr(engine, 'detect_frame', lambda frame, cfg, people: [])
    processor = RoomProcessor(camera(admin, distance_mode=True))
    with db() as con:
        cfg = settings(con)
    assert processor.process(np.zeros((360, 640, 3), dtype=np.uint8), cfg) == [
        {'box': body.tolist(), 'label': 'Person', 'state': 'person'}
    ]
    assert admin.get('/api/sightings').json()['total'] == 0

def test_person_motion_follows_a_subject_between_model_scans():
    from backend.person_detection import PersonMotion

    tracker = PersonMotion()
    frame = np.zeros((320, 480, 3), dtype=np.uint8)
    frame[60:240, 100:200] = np.random.default_rng(42).integers(0, 255, (180, 100, 3), dtype=np.uint8)
    body = np.array([100, 60, 100, 180], dtype=np.float32)
    tracker.update(frame, [body])
    shifted = cv2.warpAffine(frame, np.float32([[1, 0, 12], [0, 1, 4]]), (480, 320))
    moved, = tracker.update(shifted)
    assert moved == pytest.approx([112, 64, 100, 180], abs=.5)
    assert tracker.update(shifted, []) == []

def test_multiple_known_faces_in_one_frame_record_independently(admin,monkeypatch):
    a=person(0,'Ahmad');b=person(1,'Lana');c=camera(admin)
    processor=RoomProcessor(c)
    result=frames(processor,monkeypatch,lambda i:[sample(0,i,40),sample(1,i,300)])
    assert len(result)==2 and {r['state'] for r in result}=={'recognized'}
    rows=admin.get('/api/sightings').json()['items']
    assert {r['name'] for r in rows}=={'Ahmad','Lana'}
    assert {r['attendance_kind'] for r in rows}=={'in'}
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==2

def test_unknown_discovery_name_then_recognize_on_another_camera(admin,monkeypatch):
    first=camera(admin,collect_unknowns=True)
    processor=RoomProcessor(first)
    monkeypatch.setattr(engine, 'accuracy_available', lambda: True)
    monkeypatch.setattr(engine, 'accuracy_vector', lambda _: vec(0))
    result=frames(processor,monkeypatch,lambda i:[{**sample(0,i),'aligned':np.zeros((112,112,3), dtype=np.uint8)}])
    assert result[0]['state']=='unknown'
    inbox=admin.get('/api/review').json()['items']
    assert len(inbox)==1 and len(inbox[0]['samples'])==5
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
    named=admin.post(f"/api/review/{inbox[0]['id']}/name",json={'name':'Ahmad','code':'EMP-1','department':'Engineering','consent':True,'verified':True})
    assert named.status_code==200,named.text
    assert named.json()['accuracy_templates'] == 5
    assert admin.get('/api/review').json()['total']==0
    assert admin.get('/api/sightings').json()['items'][0]['state']=='named'
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM unknown_samples').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM unknown_accuracy_images').fetchone()[0]==0
    second=camera(admin,name='Camera 2')
    result=frames(RoomProcessor(second),monkeypatch,lambda i:[sample(0,i)])
    assert result[0]['label']=='Ahmad' and result[0]['state']=='recognized'
    events=admin.get('/api/sightings').json()['items']
    assert events[0]['camera_name']=='Camera 2' and events[0]['attendance_kind']=='in'

def test_unknown_can_be_named_after_three_clear_frames_interrupted_by_blur(admin, monkeypatch):
    processor = RoomProcessor(camera(admin, collect_unknowns=True))

    def observation(i):
        frame = sample(0, i)
        if i in (1, 3):
            return [{'box': frame['box'], 'eligible': False, 'error': 'Face is too soft'}]
        return [frame]

    result = frames(processor, monkeypatch, observation)
    assert result[0]['state'] == 'unknown'
    assert 'ready to name' in result[0]['message']
    groups = admin.get('/api/review').json()['items']
    assert len(groups) == 1 and groups[0]['sample_count'] == 3
    assert admin.get('/api/sightings').json()['total'] == 1
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0] == 0

def test_already_visible_unknown_enters_review_when_saving_is_enabled(admin, monkeypatch):
    processor = RoomProcessor(camera(admin))
    first = frames(processor, monkeypatch, lambda i: [sample(0, i)])[0]
    assert first['review_status'] == 'off'
    assert admin.get('/api/review').json()['total'] == 0
    camera(admin, name='Another camera', collect_unknowns=True)

    pending = frames(processor, monkeypatch, lambda i: [sample(0, i+10)], number=2)[0]
    assert pending['review_status'] == 'collecting' and pending['review_samples'] == 2
    assert admin.get('/api/review').json()['total'] == 0
    ready = frames(processor, monkeypatch, lambda i: [sample(0, i+12)], number=1)[0]
    assert ready['track'] == first['track'] and ready['review_status'] == 'ready'
    item = admin.get('/api/review').json()['items'][0]
    assert ready['review_group_id'] == item['id'] and item['sample_count'] == 3
    assert admin.get('/api/sightings').json()['total'] == 1


def test_deleted_unknown_shows_removed_until_a_new_track(admin, monkeypatch):
    source = camera(admin, collect_unknowns=True)
    processor = RoomProcessor(source)
    ready = frames(processor, monkeypatch, lambda i: [sample(0, i)])[0]
    assert admin.delete(f"/api/review/{ready['review_group_id']}").status_code == 200
    removed = frames(processor, monkeypatch, lambda i: [sample(0, i+10)])[0]
    assert removed['track'] == ready['track'] and removed['review_status'] == 'dismissed'
    assert 'review_group_id' not in removed and admin.get('/api/review').json()['total'] == 0
    for track in processor.tracker.tracks.values():
        track.touched -= 3
    returned = frames(processor, monkeypatch, lambda i: [sample(0, i+20)])[0]
    assert returned['track'] != ready['track'] and returned['review_status'] == 'ready'


def test_duplicate_crops_do_not_show_ready_and_full_queue_retries(admin, monkeypatch):
    processor = RoomProcessor(camera(admin, collect_unknowns=True))
    pending = frames(processor, monkeypatch, lambda i: [{**sample(0, i), 'sample': b'repeat'}])[0]
    assert pending['review_status'] == 'collecting' and pending['review_samples'] == 1
    assert admin.get('/api/review').json()['total'] == 0
    with monkeypatch.context() as patch:
        patch.setattr('backend.monitoring.UNKNOWN_REVIEW_LIMIT', 0)
        full = frames(processor, patch, lambda i: [sample(0, i+10)])[0]
    assert full['review_status'] == 'full' and 'review_group_id' not in full
    assert admin.get('/api/sightings').json()['total'] == 1
    for track in processor.tracker.tracks.values():
        track.review_retry_at = 0
    ready = frames(processor, monkeypatch, lambda i: [sample(0, i+20)], number=1)[0]
    assert ready['track'] == full['track'] and ready['review_status'] == 'ready'
    assert admin.get('/api/sightings').json()['total'] == 1


def test_review_link_finds_group_outside_first_page(admin):
    ids = sorted(uid() for _ in range(13))
    at = now()
    with db() as con:
        con.executemany('INSERT INTO unknowns VALUES (?,?,?,1,?,?)',
                        ((group,pack(vec(0)),CIPHER.encrypt(b'crop'),at,at) for group in ids))
    assert ids[0] not in {item['id'] for item in admin.get('/api/review').json()['items']}
    linked = admin.get('/api/review',params={'group_id':ids[0]}).json()
    assert linked['page'] == 2 and [item['id'] for item in linked['items']] == [ids[0]]

def test_unknown_cluster_waits_until_three_compatible_samples(admin, monkeypatch):
    processor = RoomProcessor(camera(admin, collect_unknowns=True))

    def observation(i):
        frame = sample(0, i)
        if i in (0, 2):
            frame['vector'] = normalized(.55 * vec(0) + .835 * vec(1))
        return [frame]

    result = frames(processor, monkeypatch, observation, number=4)
    assert result[0]['state'] == 'verifying'
    assert '2 of 3' in result[0]['message']
    assert admin.get('/api/review').json()['total'] == 0

def test_verified_combine_retains_both_angles_and_sightings(admin, monkeypatch):
    source = camera(admin, collect_unknowns=True)
    monkeypatch.setattr(engine, 'accuracy_available', lambda: True)
    def view(angle, n):
        return [{**sample(angle, n), 'aligned': np.full((112,112,3), 50+100*angle, np.uint8)}]

    frames(RoomProcessor(source), monkeypatch, lambda i: view(0, i))
    frames(RoomProcessor(source), monkeypatch, lambda i: view(1, i))
    source_group, target = admin.get('/api/review').json()['items']
    endpoint = f"/api/review/{target['id']}/combine"
    body = {'source_id': source_group['id'], 'verified': True}
    assert admin.post(endpoint, json={**body, 'verified': False}).status_code == 422
    monkeypatch.setattr(engine, 'accuracy_vector', lambda aligned: vec(int(aligned.mean()>100)))
    assert admin.post(endpoint, json=body).status_code == 422
    assert admin.get('/api/review').json()['total'] == 2

    monkeypatch.setattr(engine, 'accuracy_vector', lambda aligned: vec(0))
    result = admin.post(endpoint, json=body)
    assert result.status_code == 200, result.text
    item = result.json()
    assert item['sample_count'] == 5 and len(item['observations']) == 2
    assert admin.get('/api/review').json()['total'] == 1
    frames(RoomProcessor(source), monkeypatch, lambda i: view(1, i+10))
    assert admin.get('/api/review').json()['total'] == 1
    result = admin.post(f"/api/review/{target['id']}/name", json={
        'name':'Ahmad','code':'SAME-ANGLE','department':'Pool','consent':True,'verified':True,
    })
    assert result.status_code == 200, result.text
    assert result.json()['accuracy_templates'] == 5
    assert {event['state'] for event in admin.get('/api/sightings').json()['items']} == {'named'}
    assert admin.get('/api/dashboard').json()['events'] == []
    for angle in (0,1):
        result = frames(RoomProcessor(source), monkeypatch, lambda i: view(angle, i+20))
        assert result[0]['person_id'] and result[0]['state'] == 'recognized'
    assert len(admin.get('/api/dashboard').json()['events']) == 1

def test_unknown_group_can_match_more_than_its_first_sample(admin, monkeypatch):
    source = camera(admin, collect_unknowns=True)
    previous = normalized(.58 * vec(0) + .82 * vec(1))
    frames(RoomProcessor(source), monkeypatch, lambda i: [sample(0,i) if i<3 else {**sample(0,i), 'vector':previous}])
    # Seed an alternate compatible sample for a group whose original anchor is only 0.58 similar.
    group = admin.get('/api/review').json()['items'][0]
    with db() as con:
        con.execute('UPDATE unknown_samples SET vector=? WHERE id=(SELECT id FROM unknown_samples WHERE cluster_id=? LIMIT 1)',
                    (pack(previous),group['id']))
        con.execute('UPDATE unknowns SET vector=? WHERE id=?',(pack(vec(0)),group['id']))
    frames(RoomProcessor(source), monkeypatch, lambda i: [{**sample(0,i+10),'vector':previous}])
    assert admin.get('/api/review').json()['total'] == 1

def test_same_person_two_cameras_one_attendance_but_two_sightings(admin,monkeypatch):
    person();a=camera(admin,name='Camera A');b=camera(admin,name='Camera B')
    frames(RoomProcessor(a),monkeypatch,lambda i:[sample(0,i)])
    frames(RoomProcessor(b),monkeypatch,lambda i:[sample(0,i)])
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM sightings').fetchone()[0]==2

def test_six_camera_streams_share_analysis_and_attendance(admin, monkeypatch):
    person()
    sources = [camera(admin, name=f'Pool camera {i}', analysis_fps=5) for i in range(1, 7)]

    class Capture:
        index = 0

        def isOpened(self):
            return True

        def read(self):
            time.sleep(.05)
            self.index += 1
            return True, np.full((360, 640, 3), self.index % 256, dtype=np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(CameraWorker, 'open_capture', staticmethod(lambda *_: Capture()))
    monkeypatch.setattr(engine, 'detect_frame', lambda frame, cfg: [sample(frame=int(frame[0, 0, 0]))])
    for source in sources:
        assert admin.post(f"/api/cameras/{source['id']}/start").status_code == 200
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        statuses = admin.get('/api/cameras').json()['items']
        sightings = admin.get('/api/sightings').json()
        if sightings['total'] == 6 and all(c['analysis_state'] == 'current' for c in statuses):
            break
        time.sleep(.05)
    assert len(statuses) == sightings['total'] == 6
    for status in statuses:
        assert status['preview_ready'] and status['analysis_state'] == 'current'
        assert admin.get(f"/api/cameras/{status['id']}/snapshot?live=true").status_code == 200
    assert sum(event['attendance_kind'] == 'in' for event in sightings['items']) == 1
    dashboard = admin.get('/api/dashboard').json()
    assert dashboard['stats']['present'] == 1 and len(dashboard['events']) == 1
    assert admin.post(f"/api/cameras/{sources[0]['id']}/stop").status_code == 200
    remaining = admin.get('/api/cameras').json()['items']
    assert sum(c['enabled'] and c['analysis_state'] == 'current' for c in remaining) == 5

def test_naming_with_attendance_off_then_enabling_in_profile(admin,monkeypatch):
    source=camera(admin,collect_unknowns=True)
    frames(RoomProcessor(source),monkeypatch,lambda i:[sample(0,i)])
    group=admin.get('/api/review').json()['items'][0]
    assert group['camera_name']=='Camera 1' and group['sample_count']==5
    assert len(group['qualities'])==5 and len(group['observations'])==1
    summary=admin.get('/api/review?summary=true').json()['items'][0]
    assert summary['sample_count']==5 and 'samples' not in summary
    response=admin.post(f"/api/review/{group['id']}/name",json={'name':'Ahmad','code':'EMP-1','department':'Engineering','consent':True,'verified':True,'attendance_enabled':False})
    assert response.status_code==200,response.text
    person_id=response.json()['id']
    processor = RoomProcessor(source)
    result = frames(processor,monkeypatch,lambda i:[sample(0,i)])
    assert 'paused' in result[0]['message']
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
    assert admin.patch(f'/api/people/{person_id}/attendance',json={'enabled':True}).json()=={'enabled':True}
    result = frames(processor,monkeypatch,lambda i:[sample(0,i+10)],number=1)
    assert result[0]['message'] == 'Checked in successfully.'
    assert admin.get('/api/sightings').json()['items'][0]['attendance_kind'] == 'in'
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==1

@pytest.mark.parametrize('liveness',[None,0,.79])
def test_weak_liveness_never_marks_automatic_attendance(admin,monkeypatch,liveness):
    person();c=camera(admin)
    result=frames(RoomProcessor(c),monkeypatch,lambda i:[sample(0,i,live=liveness)])
    assert result[0]['state']=='candidate'
    assert result[0]['manual_kind'] == 'in' and 'checking liveness' in result[0]['message']
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
        assert con.execute("SELECT COUNT(*) FROM templates WHERE source='auto'").fetchone()[0]==0

def test_liveness_recovery_records_attendance_and_keeps_confirmation_visible(admin, monkeypatch):
    person()
    processor = RoomProcessor(camera(admin))
    frames(processor, monkeypatch, lambda i: [sample(0, i, live=.10)])
    result = frames(processor, monkeypatch, lambda i: [sample(0, i + 10)])
    assert result[0]['message'] == 'Checked in successfully.'
    result = frames(processor, monkeypatch, lambda i: [sample(0, i + 20, live=.10)], number=1)
    assert result[0]['message'] == 'Checked in successfully.' and 'manual_kind' not in result[0]
    assert len(admin.get('/api/dashboard').json()['events']) == 1

@pytest.mark.parametrize('gap_liveness, expected', [(0.99, 'recognized'), (0.79, 'candidate')])
def test_brief_uncertainty_keeps_identity_votes_but_still_requires_liveness(
    admin, monkeypatch, gap_liveness, expected,
):
    person()
    processor = RoomProcessor(camera(admin))

    def observation(i):
        frame = sample(0, i)
        if i in (1, 4):
            frame['vector'] = normalized(.48 * vec(0) + .88 * vec(2))
            frame['liveness'] = gap_liveness
        return [frame]

    result = frames(processor, monkeypatch, observation, number=7)
    assert result[0]['votes'] == 5 and result[0]['state'] == expected
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0] == int(expected == 'recognized')

def test_conflicting_identity_discards_earlier_support(admin, monkeypatch):
    person()
    person(1, 'Lana')
    processor = RoomProcessor(camera(admin))

    def observation(i):
        frame = sample(0, i)
        if i == 3:
            frame['vector'] = normalized(.48 * vec(0) + .88 * vec(1))
        return [frame]

    result = frames(processor, monkeypatch, observation, number=6)
    assert result[0]['votes'] == 2 and result[0]['state'] == 'verifying'
    assert admin.get('/api/sightings').json()['total'] == 0

@pytest.mark.parametrize('liveness, state', [(.99, 'recognized'), (.79, 'candidate')])
def test_two_model_recovery_still_requires_consensus_and_liveness(admin, monkeypatch, liveness, state):
    pid = person()
    monkeypatch.setattr(engine, 'accuracy_available', lambda: True)
    monkeypatch.setattr(engine, 'accuracy_vector', lambda _: vec(0))
    with db() as con:
        templates = con.execute('SELECT id FROM templates WHERE person_id=?', (pid,)).fetchall()
        con.executemany('INSERT INTO accuracy_templates VALUES (?,?,?)',
                        [(row['id'], ACCURACY_VERSION, pack(vec(0))) for row in templates])
    processor = RoomProcessor(camera(admin))

    def observation(i):
        return [{**sample(0, i, live=liveness), 'vector': normalized(.46 * vec(0) + .888 * vec(2)),
                 'aligned': np.zeros((112,112,3), dtype=np.uint8)}]

    result = frames(processor, monkeypatch, observation)
    assert result[0]['state'] == state and result[0]['votes'] == 5
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0] == int(state == 'recognized')

def test_observation_camera_does_not_mark_attendance(admin,monkeypatch):
    person();c=camera(admin,role='observe')
    frames(RoomProcessor(c),monkeypatch,lambda i:[sample(0,i)])
    events=admin.get('/api/sightings').json()['items']
    assert events[0]['state']=='recognized' and events[0]['attendance_kind'] is None
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0

def test_frozen_frame_never_produces_consensus(admin,monkeypatch):
    person();c=camera(admin)
    result=frames(RoomProcessor(c),monkeypatch,lambda i:[sample(0,0)],number=10)
    assert result[0]['state']=='verifying'
    assert admin.get('/api/sightings').json()['total']==0

def test_small_or_blurry_person_does_not_block_another_person(admin,monkeypatch):
    person();c=camera(admin)
    result=frames(RoomProcessor(c),monkeypatch,lambda i:[sample(0,i),{'box':np.array([300,40,20,30]),'eligible':False,'error':'Face too small'}])
    assert {r['state'] for r in result}=={'recognized','quality'}
    rejected = next(r for r in result if r['state'] == 'quality')
    assert rejected['label'] == 'Face detected' and rejected['message'] == 'Face too small'
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==1

def test_line_requires_a_real_crossing(admin,monkeypatch):
    person();c=camera(admin,trigger_mode='line')
    p=RoomProcessor(c)
    frames(p,monkeypatch,lambda i:[sample(0,i,x=180)])
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
    frames(p,monkeypatch,lambda i:[sample(0,i+10,x=300)],number=1)
    with db() as con:assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==1

def test_tracking_identity_switch_does_not_inherit_votes():
    tracker=MultiFaceTracker()
    a=tracker.update([sample(0)],stamp=100)[0][0]
    b=tracker.update([sample(1)],stamp=100.3)[0][0]
    assert a.id!=b.id

def test_repeated_crossings_on_same_track_record_once_each(admin,monkeypatch):
    person();c=camera(admin,trigger_mode='line',role='entry_exit')
    p=RoomProcessor(c)
    frames(p,monkeypatch,lambda i:[sample(0,i,x=180)])
    frames(p,monkeypatch,lambda i:[sample(0,i+10,x=300)],number=1)
    with db() as con:
        con.execute('UPDATE attendance SET created_at=?',((datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),))
    frames(p,monkeypatch,lambda i:[sample(0,i+20,x=180)],number=1)
    with db() as con:
        assert [r[0] for r in con.execute('SELECT kind FROM attendance ORDER BY created_at')]==['in','out']
        con.execute("UPDATE attendance SET created_at=? WHERE kind='out'",
                    ((datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),))
    frames(p,monkeypatch,lambda i:[sample(0,i+30,x=300)],number=3)
    with db() as con:
        assert [r[0] for r in con.execute('SELECT kind FROM attendance ORDER BY created_at')]==['in','out','in']
        assert con.execute('SELECT COUNT(*) FROM sightings').fetchone()[0] == 3

def test_crossing_can_retry_after_an_opposite_event_from_another_camera(admin, monkeypatch):
    pid = person()
    processor = RoomProcessor(camera(admin, trigger_mode='line'))
    frames(processor, monkeypatch, lambda i: [sample(0, i, x=180)])
    with db() as con:
        con.execute('INSERT INTO attendance VALUES (?,?,?,?,?,?,?)', (uid(), pid, 'out', .99, 'camera', 'Exit camera', now()))
    result = frames(processor, monkeypatch, lambda i: [sample(0, i+10, x=300)], number=1)
    assert 'Try again' in result[0]['message']
    with db() as con:
        con.execute('UPDATE attendance SET created_at=?', ((datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),))
    result = frames(processor, monkeypatch, lambda i: [sample(0, i+20, x=300)], number=1)
    assert result[0]['message'] == 'Checked in successfully.'
    assert admin.get('/api/sightings').json()['items'][0]['attendance_kind'] == 'in'

def test_a_different_identity_cannot_inherit_crossing(admin, monkeypatch):
    person()
    person(1, 'Second')
    processor = RoomProcessor(camera(admin, trigger_mode='line'))
    frames(processor, monkeypatch, lambda i: [sample(0, i, x=200)])
    second = normalized(.55 * vec(0) + .835 * vec(1))
    result = frames(processor, monkeypatch, lambda i: [{**sample(1, i, x=285), 'vector': second}])
    assert result[0]['state'] == 'recognized'
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0] == 0

def test_failed_crossing_write_can_retry_on_the_same_track(admin, monkeypatch):
    person()
    processor = RoomProcessor(camera(admin, trigger_mode='line'))
    frames(processor, monkeypatch, lambda i: [sample(0, i, x=180)])
    def fail(*args, **kwargs):
        raise RuntimeError('temporary write failure')
    with monkeypatch.context() as patch:
        patch.setattr('backend.main.record_attendance', fail)
        with pytest.raises(RuntimeError, match='temporary write'):
            frames(processor, patch, lambda i: [sample(0, i+10, x=300)], number=1)
    frames(processor, monkeypatch, lambda i: [sample(0, i+20, x=300)], number=1)
    assert admin.get('/api/sightings').json()['total'] == 1

def test_frame_with_old_quality_settings_is_discarded(admin, monkeypatch):
    person()
    processor = RoomProcessor(camera(admin))
    with db() as con:
        cfg = settings(con)
    def detect(frame, previous):
        with db() as con:
            con.execute("UPDATE config SET value='100' WHERE key='min_blur'")
        return [sample(0)]
    monkeypatch.setattr(engine, 'detect_frame', detect)
    assert processor.process(np.zeros((360,640,3), dtype=np.uint8), cfg) == []
    assert not processor.tracker.tracks

def test_camera_preview_and_analysis_have_separate_health(admin):
    worker = CameraWorker(camera(admin))
    stamp = time.monotonic()
    worker.state = 'running'
    worker.running_since = stamp - 3
    worker.frame_at = stamp
    worker.frame = np.zeros((360,640,3), dtype=np.uint8)
    status = worker.public()
    assert status['preview_ready'] and status['analysis_state'] == 'delayed'
    worker.preview = worker.frame
    worker.preview_at = stamp
    assert worker.public()['analysis_state'] == 'current'
    worker.error = 'Analysis failed'
    assert worker.public()['analysis_state'] == 'error'

def test_start_stop_and_resuming_configuration(admin,monkeypatch):
    c=camera(admin)
    from backend.monitoring import manager
    called=[]
    monkeypatch.setattr(manager,'start_camera',lambda row:called.append(row['id']))
    assert admin.post(f"/api/cameras/{c['id']}/start").status_code==200
    with db() as con:assert con.execute('SELECT enabled FROM cameras').fetchone()[0]==1
    manager.resume();assert called==[c['id'],c['id']]
    assert admin.post(f"/api/cameras/{c['id']}/stop").status_code==200
    with db() as con:assert con.execute('SELECT enabled FROM cameras').fetchone()[0]==0

def test_capture_reconnect_and_stop_discards_frames(admin):
    c=camera(admin)
    class BadCapture:
        def isOpened(self):return False
        def release(self):pass
    worker=CameraWorker(c,capture_factory=lambda *_:BadCapture())
    worker.start()
    deadline=time.monotonic()+1
    while time.monotonic()<deadline and worker.state!='reconnecting':time.sleep(.01)
    assert worker.state=='reconnecting'
    worker.stop();worker.thread.join(timeout=1)
    assert worker.state=='stopped' and worker.frame is None and worker.preview is None

def test_capture_uses_single_latest_frame_slot(admin):
    c=camera(admin)
    class Capture:
        def __init__(self):self.n=0
        def isOpened(self):return True
        def read(self):
            self.n+=1;time.sleep(.002)
            return True,np.full((50,50,3),self.n%255,dtype=np.uint8)
        def release(self):pass
    worker=CameraWorker(c,capture_factory=lambda *_:Capture())
    worker.start();time.sleep(.05)
    assert worker.sequence>5 and isinstance(worker.frame,np.ndarray)
    worker.stop();worker.thread.join(timeout=1)
    assert worker.frame is None

def test_forget_person_removes_camera_identity_history(admin,monkeypatch):
    pid=person();c=camera(admin)
    frames(RoomProcessor(c),monkeypatch,lambda i:[sample(0,i)])
    assert admin.delete('/api/people/'+pid).status_code==200
    assert admin.get('/api/sightings').json()['total']==0

@pytest.mark.parametrize('size', [None, (640, 720)])
def test_real_multi_face_detection_smoke(size):
    portrait=cv2.imread(str(Path(__file__).parent/'fixtures/face.jpg'))
    assert portrait is not None
    if size:
        portrait=cv2.resize(portrait, size)
    frame=np.concatenate([portrait,portrait],axis=1)
    with db() as con:cfg=settings(con)
    samples=engine.detect_frame(frame,cfg)
    assert len(samples)==2 and all(s['eligible'] for s in samples)

def test_aligned_quality_preserves_useful_soft_faces_and_rejects_severe_blur():
    image = cv2.imread(str(Path(__file__).parent / 'fixtures/face.jpg'))
    with db() as con:
        cfg = {**settings(con), 'auto_learn': True}
    clean, = engine.detect_frame(image, cfg)
    soft_image = cv2.GaussianBlur(image, (0, 0), 1.5)
    soft, = engine.detect_frame(soft_image, cfg)
    x, y, w, h = soft['box'].astype(int)
    raw = cv2.cvtColor(soft_image[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    assert cv2.Laplacian(raw, cv2.CV_32F).var() < cfg['min_blur']
    assert soft['eligible'] and soft['learning_quality'] < .8
    assert float(soft['vector'] @ clean['vector']) > .85

    enlarged = cv2.resize(soft_image, (1024, 1024), interpolation=cv2.INTER_LINEAR)
    larger, = engine.detect_frame(enlarged, cfg)
    assert larger['eligible'] and float(larger['vector'] @ clean['vector']) > .85

    blurred = cv2.GaussianBlur(image, (0, 0), 12)
    # Reuse the clean detection to exercise the quality check even when the
    # detector would reject the blurred image first.
    with engine.lock:
        engine.detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = engine.detector.detect(image)
        with pytest.raises(ValueError, match='face looks soft'):
            engine.face_features(blurred, faces[0], cfg)

def test_room_distance_face_crop_uses_native_pixels_and_coordinates():
    portrait = cv2.imread(str(Path(__file__).parent / 'fixtures/face.jpg'))
    scene = np.full((2160, 3840, 3), 110, dtype=np.uint8)
    scene[450:962, 2400:2912] = portrait
    with db() as con:
        cfg = settings(con)
    assert not any(face['eligible'] for face in engine.detect_frame(bounded_frame(scene, 1280), cfg))
    faces = engine.detect_frame(scene, cfg, [[2440, 540, 440, 940]])
    assert len(faces) == 1 and faces[0]['eligible']
    x, y, width, height = faces[0]['box']
    assert 2500 < x < 2800 and 550 < y < 850
    assert 90 < width < 190 and 120 < height < 250

@pytest.mark.parametrize('angle, offset, eligible', [(35, 0, True), (-35, 155, False)])
def test_rotation_recovery_uses_original_pixels_and_respects_frame_edges(angle, offset, eligible):
    portrait = cv2.imread(str(Path(__file__).parent / 'fixtures/face.jpg'))
    transform = cv2.getRotationMatrix2D((256, 256), angle, 1.)
    transform[1, 2] += offset
    frame = cv2.warpAffine(portrait, transform, (512, 512), borderValue=(80, 80, 80))
    with db() as con:
        cfg = settings(con)
    assert engine.detect_frame(frame, cfg) == []
    result, = engine.recover_frame(frame, cfg, -angle)
    assert result['eligible'] is eligible
    if eligible:
        reference, = engine.detect_frame(portrait, cfg)
        assert float(result['vector'] @ reference['vector']) > .85
    else:
        assert 'outside the camera view' in result['error']
        assert 'vector' not in result

def test_centerface_recovers_tilted_face_in_a_wide_live_frame(admin):
    portrait = cv2.imread(str(Path(__file__).parent / 'fixtures/face.jpg'))
    transform = cv2.getRotationMatrix2D((256, 256), 55, 1.)
    tilted = cv2.warpAffine(portrait, transform, (512, 512), borderValue=(80, 80, 80))
    frame = np.full((720, 1280, 3), 80, dtype=np.uint8)
    frame[80:592, 390:902] = tilted
    with db() as con:
        cfg = settings(con)
    assert engine.detect_frame(frame, cfg) == []
    assert engine.recover_frame(frame, cfg, -35) == []
    recovered, = engine.recover_frame(frame, cfg, -35, enhanced=True)
    reference, = engine.detect_frame(portrait, cfg)
    assert recovered['eligible'] and float(recovered['vector'] @ reference['vector']) > .85
    live, = RoomProcessor(camera(admin)).process(frame, cfg)
    assert live['state'] == 'verifying'
    assert admin.get('/api/sightings').json()['total'] == 0

def test_http_mjpeg_preview_uses_real_decoder(admin):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    image=(Path(__file__).parent/'fixtures/face.jpg').read_bytes()
    done=threading.Event()

    class Feed(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            part=b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(image)).encode()+b'\r\n\r\n'+image+b'\r\n'
            try:
                while not done.is_set():
                    self.wfile.write(part)
                    done.wait(.07)
            except OSError:
                pass

    with ThreadingHTTPServer(('127.0.0.1',0),Feed) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            source=camera(admin,source=f'http://127.0.0.1:{server.server_port}/video',role='observe')
            assert admin.post(f"/api/cameras/{source['id']}/start").status_code==200
            deadline=time.monotonic()+9
            while time.monotonic()<deadline:
                snapshot=admin.get(f"/api/cameras/{source['id']}/snapshot")
                if snapshot.status_code==200:
                    assert snapshot.headers['content-type']=='image/jpeg'
                    assert cv2.imdecode(np.frombuffer(snapshot.content,np.uint8),cv2.IMREAD_COLOR) is not None
                    break
                done.wait(.1)
            else:
                pytest.fail('A network MJPEG frame never reached the preview endpoint.')
            assert admin.post(f"/api/cameras/{source['id']}/stop").status_code==200
            assert admin.get(f"/api/cameras/{source['id']}/snapshot").status_code==404
        finally:
            done.set()
            server.shutdown()
            thread.join(timeout=2)

def test_browser_camera_accepts_owner_frames_and_releases_on_logout(admin, monkeypatch):
    from backend.monitoring import manager

    release_inference = threading.Event()
    def slow_analysis(*args):
        release_inference.wait(3)
        return []
    monkeypatch.setattr(RoomProcessor, 'process', slow_analysis)

    assert admin.post('/api/cameras/browser',json={'authorized':False}).status_code==422
    result=admin.post('/api/cameras/browser',json={'authorized':True,'role':'observe'})
    assert result.status_code==200,result.text
    share=result.json()
    path=f"/api/cameras/{share['id']}/browser-frame"
    frame=(Path(__file__).parent/'fixtures/face.jpg').read_bytes()
    files={'file':('frame.jpg',frame,'image/jpeg')}
    assert admin.post(path,files=files,headers={'X-Camera-Share':'wrong'}).status_code==410
    headers={'X-Camera-Share':share['share_token']}
    assert admin.post(path,files={'file':('frame.jpg',b'bad','image/jpeg')},headers=headers).status_code==422
    try:
        assert admin.post(path,files=files,headers=headers).status_code==200
        snapshot = f"/api/cameras/{share['id']}/snapshot"
        assert admin.get(snapshot).status_code == 404
        live = admin.get(snapshot, params={'live': True})
        assert live.status_code == 200 and live.headers['cache-control'] == 'no-store'
        assert admin.get(snapshot, params={'live': True}, headers={'If-None-Match': live.headers['etag']}).status_code == 304
    finally:
        release_inference.set()
    assert admin.post('/api/cameras/browser',json={'authorized':True}).status_code==409
    assert share['share_token'] not in admin.get('/api/cameras').text
    worker=manager.workers[share['id']]
    assert worker.state=='running'
    assert admin.post('/api/logout').status_code==200
    assert worker.state=='stopped' and worker.frame is None and worker.share_token is None

def test_expired_browser_camera_can_reuse_its_source(admin):
    from backend.monitoring import manager

    first=admin.post('/api/cameras/browser',json={'authorized':True}).json()
    worker=manager.workers[first['id']]
    with worker.lock:worker.share_deadline=time.monotonic()-1
    second=admin.post('/api/cameras/browser',json={'authorized':True}).json()
    assert second['id']==first['id'] and second['share_token']!=first['share_token']
    assert admin.post(f"/api/cameras/{first['id']}/browser-stop",headers={'X-Camera-Share':first['share_token']}).status_code==200
    assert manager.workers[first['id']].state=='connecting'

def test_setup_measures_real_frames_without_saving_faces_and_survives_reconnect(admin):
    share = admin.post('/api/cameras/browser', json={'authorized': True, 'collect_unknowns': True}).json()
    mode_path = f"/api/cameras/{share['id']}/mode"
    assert admin.patch(mode_path, json={'mode': 'setup'}).json()['operation_mode'] == 'setup'
    worker = manager.workers[share['id']]
    headers = {'X-Camera-Share': share['share_token']}

    def send(frame):
        response = admin.post(f"/api/cameras/{share['id']}/browser-frame", headers=headers,
                              files={'file': ('camera.jpg', frame, 'image/jpeg')})
        assert response.status_code == 200, response.text

    def wait_for_frames(count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            test = worker.public()['setup_test']
            if test and test['frames'] == count:
                return test
            time.sleep(.025)
        pytest.fail(worker.public())

    fixture = Path(__file__).parent / 'fixtures/face.jpg'
    portrait = cv2.imread(str(fixture))
    _, blank = cv2.imencode('.jpg', np.zeros_like(portrait))
    send(blank.tobytes())
    assert wait_for_frames(1)['face_frames'] == 0
    time.sleep(.22)
    send(fixture.read_bytes())
    result = wait_for_frames(2)
    assert result['face_frames'] == result['usable_frames'] == 1
    assert result['largest_face_pixels'] >= result['required_face_pixels'] == 90
    assert worker.public()['tracks'][0]['state'] == 'setup_usable'
    with db() as con:
        for table in ('sightings', 'unknowns', 'attendance', 'templates'):
            assert con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0

    stopped = admin.post(f"/api/cameras/{share['id']}/browser-stop", headers=headers)
    assert stopped.status_code == 200
    restarted = admin.post('/api/cameras/browser', json={'authorized': True}).json()
    assert restarted['id'] == share['id']
    state, = admin.get('/api/cameras').json()['items']
    assert state['operation_mode'] == 'setup' and state['setup_test'] is None

def test_camera_mode_pauses_only_that_camera_and_retires_inflight_analysis(admin, monkeypatch):
    pid = person()
    source = camera(admin)
    other = camera(admin, name='Other camera')
    worker = CameraWorker(source)
    manager.workers[source['id']] = worker
    old_processor = worker.processor
    path = f"/api/cameras/{source['id']}/mode"
    assert admin.patch(path, json={'mode': 'preview'}).json()['operation_mode'] == 'preview'
    assert frames(old_processor, monkeypatch, lambda i: [sample(0, i)]) == []
    assert frames(worker.processor, monkeypatch, lambda i: [sample(0, i)]) == []
    active, = frames(RoomProcessor(other), monkeypatch, lambda i: [sample(0, i)])
    assert active['state'] == 'recognized'
    assert admin.patch(path, json={'mode': 'live'}).json()['operation_mode'] == 'live'
    assert frames(old_processor, monkeypatch, lambda i: [sample(0, i)]) == []
    resumed, = frames(worker.processor, monkeypatch, lambda i: [sample(0, i)])
    assert resumed['state'] == 'recognized'
    with db() as con:
        assert con.execute('SELECT person_id FROM attendance').fetchone()[0] == pid
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM sightings').fetchone()[0] == 2

def test_browser_high_detail_saves_a_distant_face_from_native_jpegs(admin, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr('backend.monitoring.person_detector', lambda: SimpleNamespace(detect=lambda _: []))
    portrait = cv2.imread(str(Path(__file__).parent / 'fixtures/face.jpg'))
    portrait = cv2.resize(portrait, (369,369), interpolation=cv2.INTER_AREA)
    scene = np.full((2160,3840,3), 104, dtype=np.uint8)
    scene[880:1249,1760:2129] = portrait
    with db() as con:
        cfg = settings(con)
    high, = [face for face in engine.detect_native_tile(scene,cfg,(.5,.5)) if face['eligible']]
    assert 90 <= min(high['box'][2:]) < 130
    low = engine.sample_face(bounded_frame(scene,1280), np.r_[high['box']/3, np.zeros(11)], cfg)
    assert not low['eligible'] and 30 <= low['face_pixels'] < 44 and low['required_face_pixels'] == 90

    network = RoomProcessor(camera(admin, distance_mode=True, operation_mode='setup'))
    with monkeypatch.context() as fallback:
        fallback.setattr(engine, 'detect_frame', lambda *args, **kwargs: [])
        distant, = network.process(scene, cfg)
    assert distant['state'] == 'setup_usable' and distant['face_pixels'] >= 90

    def send(share, image):
        ok, jpeg = cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,86])
        assert ok
        return admin.post(f"/api/cameras/{share['id']}/browser-frame",
                          files={'file': ('camera.jpg',jpeg.tobytes(),'image/jpeg')},
                          headers={'X-Camera-Share':share['share_token']})

    standard = admin.post('/api/cameras/browser',json={'authorized':True}).json()
    assert standard['frame_max_edge'] == 1920 and standard['upload_fps'] == 5
    assert send(standard,scene).status_code == 422
    assert send(standard,bounded_frame(scene,1920)).status_code == 200
    standard_worker = manager.workers[standard['id']]
    deadline = time.monotonic()+5
    while standard_worker.preview_sequence < 1 and time.monotonic() < deadline:
        time.sleep(.025)
    assert standard_worker.public()['analysis_size'] == (1920,1080)
    assert admin.post(f"/api/cameras/{standard['id']}/browser-stop",
                      headers={'X-Camera-Share':standard['share_token']}).status_code == 200
    detail = admin.post('/api/cameras/browser',json={'authorized':True,'distance_mode':True,'collect_unknowns':True}).json()
    assert detail['frame_max_edge'] == 3840 and detail['upload_fps'] == 3
    worker = manager.workers[detail['id']]
    assert send(detail,np.zeros((2400,3840,3),dtype=np.uint8)).status_code == 422
    def feed(sequence, brightness):
        scene[880:1249,1760:2129] = np.clip(portrait.astype(np.int16)+brightness,0,255).astype(np.uint8)
        result = send(detail,scene)
        assert result.status_code == 200, result.text
        deadline = time.monotonic()+5
        while worker.preview_sequence < sequence and time.monotonic() < deadline:
            time.sleep(.025)
        assert worker.preview_sequence >= sequence, worker.error
        time.sleep(.34)

    for sequence, brightness in enumerate((0,3,-3,5,-5), 1):
        feed(sequence, brightness)
        if any(track.get('review_status')=='ready' for track in worker.public()['tracks']):
            break
    status = worker.public()
    assert status['analysis_size'] == (3840,2160)
    queue = admin.get('/api/review').json()['items']
    assert len(queue) == 1 and queue[0]['sample_count'] >= 3
    assert any(track.get('review_group_id')==queue[0]['id'] for track in status['tracks'])
    named = admin.post(f"/api/review/{queue[0]['id']}/name",json={
        'name':'Distance fixture','code':'4K-01','department':'Test','consent':True,'verified':True,
    })
    assert named.status_code == 200, named.text
    followup = []
    for sequence, brightness in enumerate((2,-2,4,-4,6,-6), sequence+1):
        feed(sequence, brightness)
        tracks = worker.public()['tracks']
        followup.append(tracks)
        if any(track.get('person_id')==named.json()['id'] for track in tracks):
            break
    assert any(track.get('person_id')==named.json()['id'] for track in tracks), followup

def test_browser_frames_discover_and_name_a_real_face(admin):
    share = admin.post('/api/cameras/browser', json={
        'authorized': True, 'collect_unknowns': True, 'role': 'observe',
    }).json()
    worker = manager.workers[share['id']]
    upload = f"/api/cameras/{share['id']}/browser-frame"
    headers = {'X-Camera-Share': share['share_token']}
    fixtures = Path(__file__).parent / 'fixtures'
    def send(photo, sequence):
        response = admin.post(upload, files={'file': ('camera.jpg', photo, 'image/jpeg')}, headers=headers)
        assert response.status_code == 200, response.text
        deadline = time.monotonic() + 5
        while worker.preview_sequence < sequence and time.monotonic() < deadline:
            time.sleep(.025)
        assert worker.preview_sequence >= sequence, worker.error
        time.sleep(.18)

    for sequence in range(1, 4):
        send((fixtures / f'enroll-{sequence - 1}.jpg').read_bytes(), sequence)
    queue = admin.get('/api/review').json()['items']
    assert len(queue) == 1 and queue[0]['sample_count'] == 3
    live_track, = worker.public()['tracks']
    assert live_track['review_status'] == 'ready' and live_track['review_group_id'] == queue[0]['id']
    named = admin.post(f"/api/review/{queue[0]['id']}/name", json={
        'name': 'Camera Fixture', 'code': 'CAM-001', 'department': 'Test',
        'consent': True, 'verified': True,
    })
    assert named.status_code == 200, named.text
    assert named.json()['templates'] == 3
    assert admin.get('/api/review').json()['total'] == 0

    portrait = cv2.imread(str(fixtures / 'face.jpg'))
    for sequence in range(4, 9):
        adjusted = cv2.convertScaleAbs(portrait, alpha=.90 + sequence * .018)
        ok, jpeg = cv2.imencode('.jpg', adjusted)
        assert ok
        send(jpeg.tobytes(), sequence)
    track, = worker.public()['tracks']
    assert track['person_id'] == named.json()['id']
    assert track['state'] in ('candidate', 'recognized')
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0] == 0
