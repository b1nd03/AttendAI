import hashlib
import json
import sqlite3
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.main import accuracy_gallery, auto_update, gallery, record_attendance, safe_csv
from backend.recognition import ACCURACY_VERSION, Consensus, FaceGallery, engine, match, match_observation, normalized
from backend.store import CIPHER, db, journal_mode_for, now, pack, purge_unknowns, settings, uid, unpack

def vector(index=0):
    v=np.zeros(128,dtype=np.float32)
    v[index]=1
    return v

def add_person(name='Test Person',code='TEST-1',index=0):
    pid=uid()
    with db() as con:
        con.execute('INSERT INTO people VALUES (?,?,?,?,?,?)',(pid,name,code,'Testing',now(),now()))
        for _ in range(3):
            con.execute('INSERT INTO templates VALUES (?,?,?,?,?,1,NULL,NULL,?)',(uid(),pid,pack(vector(index)),1,'enrollment',now()))
    return pid

def fake_sample(raw,cfg):
    return {'vector':vector(),'box':np.array([40,40,150,180],dtype=np.float32),'sample':b'test-crop',
            'quality':1.,'liveness':.99,'width':640,'height':480,'fingerprint':hashlib.sha256(raw).hexdigest()}

def scan_frames(client,monkeypatch,count=5,sample=fake_sample,stream='stream-12345',kind='in'):
    monkeypatch.setattr(engine,'extract',sample)
    result=None
    for i in range(count):
        # Simulate independently arriving camera frames without wall-clock sleeps.
        for track in engine.consensus.tracks.values():
            track.last_vote-=1
        result=client.post('/api/scan',files={'file':('frame.jpg',f'frame-{i}'.encode(),'image/jpeg')},data={'stream':stream,'kind':kind})
        assert result.status_code==200,result.text
    return result.json()

def test_private_endpoints_require_auth(client):
    for url in ['/api/dashboard','/api/people','/api/settings','/api/review','/api/audit','/api/export?start=2026-11-01&end=2026-11-01']:
        assert client.get(url).status_code==401

def test_password_setup_session_and_logout(client):
    assert client.post('/api/setup',json={'password':'short'}).status_code==422
    result=client.post('/api/setup',json={'password':'a-secure-test-password'})
    assert result.status_code==200
    assert 'HttpOnly' in result.headers['set-cookie'] and 'SameSite=strict' in result.headers['set-cookie']
    assert client.post('/api/setup',json={'password':'a-secure-test-password'}).status_code==409
    assert client.get('/api/dashboard').status_code==200
    client.post('/api/logout')
    assert client.get('/api/dashboard').status_code==401
    assert client.post('/api/login',json={'password':'wrong-password-123'}).status_code==401
    assert client.post('/api/login',json={'password':'a-secure-test-password'}).status_code==200

def test_cross_origin_and_dns_rebinding_blocked(admin):
    assert admin.post('/api/logout',headers={'origin':'https://evil.example'}).status_code==403
    assert admin.get('/api/health',headers={'host':'evil.example'}).status_code==400

def test_open_set_rejects_unknown_and_ambiguous():
    assert match(vector(1),{'a':[vector()]},.5,.1)[0] is None
    assert match(vector(),{'a':[vector()],'b':[vector()]},.5,.1)[0] is None
    assert match(vector(),{'a':[vector()]},.5,.1)[0]=='a'
    assert match(vector(),{},.5,.1)[0] is None

def test_indexed_gallery_uses_best_two_and_handles_single_template():
    runner_up = normalized(.6 * vector() + .8 * vector(1))
    index = FaceGallery({'first': [vector(1), vector(), vector()], 'second': [runner_up]})
    person, score, gap = match(vector(), index, .5, .1)
    assert person == 'first' and score == pytest.approx(1) and gap == pytest.approx(.4)
    person, score, gap = match(runner_up, index, .5, .1)
    assert person == 'second' and score == pytest.approx(1) and gap == pytest.approx(.3)

def test_second_model_checks_close_calls_without_overriding_another_identity(monkeypatch):
    index = FaceGallery({'first': [vector()], 'second': [vector(1)]})
    near = {'vector': normalized(.46 * vector() + .888 * vector(2)), 'aligned': np.zeros((112,112,3))}
    with db() as con:
        cfg = settings(con)
    monkeypatch.setattr(engine, 'accuracy_vector', lambda _: vector())
    assert match_observation(near, index, None, cfg)[0] is None
    assert match_observation(near, index, index, cfg)[0] == 'first'
    monkeypatch.setattr(engine, 'accuracy_vector', lambda _: vector(1))
    assert match_observation(near, index, index, cfg)[0] is None
    strong = {**near, 'vector': vector()}
    assert match_observation(strong, index, index, cfg)[0::3] == ('first', False)

def test_accuracy_gallery_waits_for_full_roster_and_refresh_preserves_attendance(admin, monkeypatch):
    first = add_person()
    second = add_person('Second Person', 'TEST-2', 1)
    monkeypatch.setattr(engine, 'accuracy_available', lambda: True)
    monkeypatch.setattr(engine, 'accuracy_vector', lambda _: vector())
    monkeypatch.setattr(engine, 'extract', lambda raw, cfg: {**fake_sample(raw, cfg), 'aligned': np.zeros((112,112,3))})
    with db() as con:
        templates = con.execute('SELECT id FROM templates WHERE person_id=?', (second,)).fetchall()
        con.executemany('INSERT INTO accuracy_templates VALUES (?,?,?)',
                        [(row['id'], ACCURACY_VERSION, pack(vector(1))) for row in templates])
        assert accuracy_gallery(con, gallery(con)) is None
    attendance = admin.post('/api/attendance/manual', json={'person_id':first,'kind':'in','note':'Existing attendance'})
    assert attendance.status_code == 200
    images = [('files', (f'frame-{i}.jpg', f'frame-{i}'.encode(), 'image/jpeg')) for i in range(3)]
    refreshed = admin.post(f'/api/people/{first}/refresh-faces', files=images,
                           data={'consent':'true','supervised':'true'})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['accuracy_templates'] == 3
    with db() as con:
        assert accuracy_gallery(con, gallery(con)) is not None
        assert con.execute('SELECT COUNT(*) FROM attendance WHERE person_id=?', (first,)).fetchone()[0] == 1

def test_cipher_round_trip_has_no_plain_vector():
    original=vector();encrypted=pack(original)
    assert original.tobytes() not in encrypted
    assert np.array_equal(unpack(encrypted),original)

def test_unknown_retention_poll_does_not_open_an_empty_write_transaction():
    with db() as con:
        purge_unknowns(con, settings(con))
        assert not con.in_transaction

def test_database_journal_mode_matches_sqlite_safety_policy():
    assert journal_mode_for((3, 49, 1)) == 'DELETE'
    assert journal_mode_for((3, 51, 2)) == 'DELETE'
    assert journal_mode_for((3, 44, 6)) == 'WAL'
    assert journal_mode_for((3, 50, 7)) == 'WAL'
    assert journal_mode_for((3, 51, 3)) == 'WAL'
    with db() as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0].upper() == journal_mode_for(sqlite3.sqlite_version_info)

def test_bad_vectors_rejected():
    with pytest.raises(ValueError):normalized(np.zeros(128))
    with pytest.raises(ValueError):normalized(np.array([float('nan')]))

def test_consensus_does_not_count_duplicate_frames(monkeypatch):
    consensus=Consensus();box=np.array([10,10,100,100],dtype=float)
    monkeypatch.setattr('backend.recognition.time.monotonic',lambda:100.)
    for _ in range(10):
        track,stable,votes=consensus.add('a',box,vector(),'person','same-image',5)
        track.last_vote-=1
    assert votes==1 and not stable

def test_track_resets_on_identity_change(monkeypatch):
    consensus=Consensus();box=np.array([10,10,100,100],dtype=float)
    monkeypatch.setattr('backend.recognition.time.monotonic',lambda:100.)
    first,_,_=consensus.add('a',box,vector(),'person1','one',5)
    second,stable,votes=consensus.add('a',box,vector(1),'person2','two',5)
    assert first is not second and votes==1 and not stable

def test_five_frames_record_once_and_duplicates_ignored(admin,monkeypatch):
    add_person()
    assert scan_frames(admin,monkeypatch,4)['state']=='verifying'
    # Start a fresh stream for a full independent five-frame sequence.
    result=scan_frames(admin,monkeypatch,5,stream='other-stream-123')
    assert result['state']=='recorded'
    assert scan_frames(admin,monkeypatch,5,stream='third-stream-123')['state']=='duplicate'
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==1

@pytest.mark.parametrize('live',[None,0,.79])
def test_liveness_failure_never_records_or_learns(admin,monkeypatch,live):
    add_person()
    def sample(raw,cfg):return {**fake_sample(raw,cfg),'liveness':live}
    assert scan_frames(admin,monkeypatch,sample=sample)['state']=='liveness'
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM templates').fetchone()[0]==3

def test_unknown_never_marks_present_and_storage_is_opt_in(admin,monkeypatch):
    assert scan_frames(admin,monkeypatch)['state']=='unknown'
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM unknowns').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
        con.execute("UPDATE config SET value='true' WHERE key='retain_unknowns'")
    assert scan_frames(admin,monkeypatch,stream='new-unknown-stream')['state']=='unknown'
    with db() as con:
        row=con.execute('SELECT * FROM unknowns').fetchone()
        assert row is not None and row['sample']!=b'test-crop'
        assert CIPHER.decrypt(row['sample'])==b'test-crop'

def test_enrollment_requires_consent_and_supervision(admin):
    fields={'name':'New Person','code':'NEW1','department':'Testing','consent':'false','supervised':'true'}
    uploads=[('files',('x.jpg',b'test','image/jpeg'))]*3
    assert admin.post('/api/people',data=fields,files=uploads).status_code==422
    fields.update(consent='true',supervised='false')
    assert admin.post('/api/people',data=fields,files=uploads).status_code==422

def test_enrollment_roundtrip_and_forget(admin,monkeypatch):
    monkeypatch.setattr(engine,'extract',fake_sample)
    uploads=[('files',(f'{i}.jpg',f'{i}'.encode(),'image/jpeg')) for i in range(3)]
    fields={'name':'New Person','code':'NEW1','department':'Testing','consent':'true','supervised':'true'}
    result=admin.post('/api/people',data=fields,files=uploads)
    assert result.status_code==200,result.text
    pid=result.json()['id']
    assert len(admin.get('/api/templates/'+pid).json())==3
    assert admin.post('/api/attendance/manual',json={'person_id':pid,'kind':'in','note':'In person verification'}).status_code==200
    assert admin.delete('/api/people/'+pid).status_code==200
    with db() as con:
        assert con.execute('SELECT COUNT(*) FROM templates').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM audit').fetchone()[0]>=3

def test_duplicate_enrollment_rejected(admin,monkeypatch):
    add_person();monkeypatch.setattr(engine,'extract',fake_sample)
    result=admin.post('/api/people',data={'name':'Same Person','code':'DIFF','department':'Test','consent':'true','supervised':'true'},files=[('files',(f'{i}.jpg',str(i).encode(),'image/jpeg')) for i in range(3)])
    assert result.status_code==409

def test_settings_conflict_and_unsafe_threshold_rejected(admin):
    cfg=admin.get('/api/settings').json()
    assert admin.put('/api/settings',json={**cfg,'update_threshold':.55}).status_code==422
    assert admin.put('/api/settings',json={**cfg,'timezone':'Not/AZone'}).status_code==422
    assert admin.put('/api/settings',json={**cfg,'organization':'Changed'}).status_code==200
    assert admin.put('/api/settings',json=cfg).status_code==409

def test_turning_off_unknown_storage_purges_biometrics(admin):
    with db() as con:
        con.execute('INSERT INTO unknowns VALUES (?,?,?,1,?,?)',(uid(),pack(vector()),CIPHER.encrypt(b'image'),now(),now()))
    cfg=admin.get('/api/settings').json()
    assert admin.put('/api/settings',json={**cfg,'retain_unknowns':False}).status_code==200
    assert admin.get('/api/review').json()['total']==0

def test_manual_duplicate_cooldown(admin):
    pid=add_person()
    payload={'person_id':pid,'kind':'in','note':'Identity checked manually'}
    assert not admin.post('/api/attendance/manual',json=payload).json()['duplicate']
    assert admin.post('/api/attendance/manual',json=payload).json()['duplicate']
    assert admin.post('/api/attendance/manual',json={**payload,'kind':'out'}).json()['duplicate']

def test_auto_updates_keep_anchors_and_cap_gallery(admin):
    pid=add_person()
    v=normalized(vector()+.3*vector(1))
    sample={**fake_sample(b'image',{}),'vector':v}
    with db() as con:
        cfg={**settings(con),'auto_learn':True,'max_templates':5}
        assert not auto_update(con,pid,{**sample,'learning_quality':.4},.95,.5,cfg)
        assert auto_update(con,pid,sample,.95,.5,cfg)
        # Subsequent update blocked for at least an hour.
        assert not auto_update(con,pid,{**sample,'vector':normalized(vector()+.3*vector(2))},.95,.5,cfg)
        assert con.execute("SELECT COUNT(*) FROM templates WHERE source='enrollment' AND active=1").fetchone()[0]==3
        tid=con.execute("SELECT id FROM templates WHERE source='auto'").fetchone()[0]
    assert admin.post('/api/templates/'+tid+'/rollback').status_code==200
    assert admin.post('/api/templates/'+tid+'/rollback').status_code==409

def test_anchor_rollback_is_forbidden(admin):
    add_person()
    with db() as con:tid=con.execute('SELECT id FROM templates LIMIT 1').fetchone()[0]
    assert admin.post('/api/templates/'+tid+'/rollback').status_code==409

def test_csv_formula_injection_and_date_bounds(admin):
    pid=add_person(name='=HYPERLINK("evil")')
    admin.post('/api/attendance/manual',json={'person_id':pid,'kind':'in','note':'+dangerous formula'})
    from backend.main import date_bounds
    with db() as con:cfg=settings(con)
    from zoneinfo import ZoneInfo
    current=datetime.now(ZoneInfo(cfg['timezone'])).date().isoformat()
    response=admin.get(f'/api/export?start={current}&end={current}')
    assert response.status_code==200
    assert "'=HYPERLINK" in response.text and "'+dangerous" in response.text
    assert admin.get('/api/export?start=invalid&end=2026-01-01').status_code==422
    assert admin.get('/api/export?start=2026-05-05&end=2026-05-01').status_code==422
    assert date_bounds('2026-11-05',cfg)[0].startswith('2026-11-04T21:00')

def test_real_models_load_and_process_non_face():
    assert engine.status()['recognition']
    blank=np.zeros((480,640,3),dtype=np.uint8)
    _,buffer=cv2.imencode('.jpg',blank)
    with db() as con:cfg=settings(con)
    with pytest.raises(ValueError,match='No face found'):
        engine.extract(buffer.tobytes(),cfg)

def test_real_face_smoke():
    path=Path(__file__).parent/'fixtures/face.jpg'
    if not path.exists():pytest.skip('Optional upstream test fixture not downloaded')
    with db() as con:cfg=settings(con)
    sample=engine.extract(path.read_bytes(),cfg)
    assert sample['vector'].shape==(128,)
    assert abs(np.linalg.norm(sample['vector'])-1)<1e-5
    assert sample['liveness'] is not None and 0<=sample['liveness']<=1
