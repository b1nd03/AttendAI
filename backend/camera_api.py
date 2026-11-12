from datetime import datetime,timedelta,timezone
import base64
import sqlite3
import io
import time
from itertools import combinations
from typing import Annotated,Literal
from urllib.parse import urlsplit

import cv2
import numpy as np
from PIL import Image
from fastapi import APIRouter,Depends,File,Header,HTTPException,Response,UploadFile
from pydantic import BaseModel,Field,ConfigDict,model_validator

from .main import (authorized, mutation_lock, gallery, validate_person, date_bounds, review_item,
                   prepare_accuracy, validate_enrollment_consistency, write_enrollment)
from .monitoring import UNKNOWN_REVIEW_FRAMES, browser_capture_limits, manager
from .person_detection import PERSON_MODEL
from .recognition import engine,match
from .store import CIPHER,audit,db,now,purge_unknowns,settings,uid,unpack

router=APIRouter(prefix='/api')
Auth=Annotated[str,Depends(authorized)]
CameraMode = Literal['live', 'setup', 'preview']
INSERT_CAMERA = '''INSERT INTO cameras
    (id,name,location,source,source_type,source_hint,role,trigger_mode,line_axis,line_position,
     direction,analysis_fps,consent_at,created_at,distance_mode,operation_mode)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''

class CameraBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=2,max_length=80)
    location:str=Field(min_length=1,max_length=80)
    source_type:Literal['network','usb']
    source:str=Field(min_length=1,max_length=2000)
    role:Literal['in','out','observe','entry_exit']='in'
    trigger_mode:Literal['appearance','line']='appearance'
    line_axis:Literal['x','y']='x'
    line_position:float=Field(default=.5,ge=.1,le=.9)
    direction:Literal[-1,1]=1
    analysis_fps:int=Field(default=5,ge=1,le=8)
    distance_mode: bool = False
    operation_mode: CameraMode = 'live'
    authorized:bool
    collect_unknowns:bool=False
    improve_gallery:bool=False

    @model_validator(mode='after')
    def check_source(self):
        self.name=self.name.strip();self.location=self.location.strip();self.source=self.source.strip()
        if not self.name or not self.location:raise ValueError('Name and location are required.')
        if self.source_type=='usb':
            if not self.source.isdigit() or not 0<=int(self.source)<=20:raise ValueError('USB device index must be between 0 and 20.')
        else:
            try:
                parsed=urlsplit(self.source)
                if parsed.scheme not in ('rtsp','rtsps','http','https') or not parsed.hostname:raise ValueError()
                parsed.port
                if '\n' in self.source or '\r' in self.source:raise ValueError()
            except ValueError:raise ValueError('Use a complete RTSP, RTSPS, HTTP, or HTTPS stream address.') from None
        if self.role=='entry_exit' and self.trigger_mode!='line':raise ValueError('Entry and exit mode needs an entrance line.')
        return self

def public_camera(row):
    worker=manager.workers.get(row['id'])
    if worker:return worker.public()
    keys=['id','name','location','source_type','source_hint','role','trigger_mode','line_axis','line_position','direction','analysis_fps']
    return {key:row[key] for key in keys}|{'enabled':False,'state':'stopped','error':'','latency_ms':0,'processed_fps':0,'frame_age_seconds':None,'analysis_size':None,'analysis_state':'waiting','tracks':[],'preview_ready':False,
                                          'distance_mode':bool(row['distance_mode']), 'people_count':0,
                                          'operation_mode':row['operation_mode'], 'setup_test':None, 'source_size':None}

@router.get('/cameras')
def cameras(auth:Auth):
    with db() as con:
        return {'items':[public_camera(r) for r in con.execute('SELECT * FROM cameras ORDER BY created_at')],
                'limit':8,'engine':engine.status()}

@router.post('/cameras')
def add_camera(body:CameraBody,auth:Auth):
    if not body.authorized:raise HTTPException(422,'Confirm authorization and consent/notice arrangements for this camera.')
    if body.distance_mode and not PERSON_MODEL.is_file():
        raise HTTPException(409, 'Run setup.ps1 to install the room-distance model.')
    cid=uid()
    hint='USB device '+body.source if body.source_type=='usb' else urlsplit(body.source).hostname
    with mutation_lock,db() as con:
        if con.execute('SELECT COUNT(*) FROM cameras').fetchone()[0]>=8:raise HTTPException(409,'This local instance supports up to eight configured cameras.')
        con.execute(INSERT_CAMERA,
                    (cid,body.name,body.location,CIPHER.encrypt(body.source.encode()),body.source_type,hint,
                     body.role,body.trigger_mode,body.line_axis,body.line_position,body.direction,body.analysis_fps,
                     now(),now(),int(body.distance_mode),body.operation_mode))
        if body.collect_unknowns:
            con.execute("UPDATE config SET value='true' WHERE key='retain_unknowns'")
            audit(con,'unknown_collection_enabled','workspace','Enabled during camera setup; encrypted samples for administrator naming')
        if body.improve_gallery:
            con.execute("UPDATE config SET value='true' WHERE key='auto_learn'")
        # These are workspace settings; invalidate stale Settings forms in other tabs.
        con.execute("INSERT INTO config VALUES ('revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")
        audit(con,'camera_added',cid,'Camera configured; stopped until explicitly started')
        row=con.execute('SELECT * FROM cameras WHERE id=?',(cid,)).fetchone()
        return public_camera(row)

class DistanceModeBody(BaseModel):
    enabled: bool

class CameraModeBody(BaseModel):
    mode: CameraMode

@router.patch('/cameras/{camera_id}/mode')
def set_camera_mode(camera_id: str, body: CameraModeBody, auth: Auth):
    with mutation_lock:
        with db() as con:
            row = con.execute('SELECT * FROM cameras WHERE id=?', (camera_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Camera no longer exists.')
            if row['operation_mode'] != body.mode:
                con.execute('UPDATE cameras SET operation_mode=? WHERE id=?', (body.mode, camera_id))
                audit(con, 'camera_mode_changed', camera_id, body.mode)
            row = con.execute('SELECT * FROM cameras WHERE id=?', (camera_id,)).fetchone()
        manager.set_camera_mode(camera_id, body.mode)
        return public_camera(row)

@router.patch('/cameras/{camera_id}/distance')
def set_distance_mode(camera_id: str, body: DistanceModeBody, auth: Auth):
    with mutation_lock, db() as con:
        row = con.execute('SELECT * FROM cameras WHERE id=?', (camera_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Camera no longer exists.')
        if row['source_type'] == 'browser':
            raise HTTPException(409, 'Room-distance mode requires a server-connected camera.')
        if body.enabled and not PERSON_MODEL.is_file():
            raise HTTPException(409, 'Run setup.ps1 to install the room-distance model.')
        con.execute('UPDATE cameras SET distance_mode=? WHERE id=?', (int(body.enabled), camera_id))
        manager.set_distance_mode(camera_id, body.enabled)
        audit(con, 'camera_distance_mode', camera_id, 'Enabled' if body.enabled else 'Disabled')
    return {'distance_mode': body.enabled}

@router.post('/cameras/{camera_id}/start')
def start_camera(camera_id:str,auth:Auth):
    with mutation_lock,db() as con:
        row=con.execute('SELECT * FROM cameras WHERE id=?',(camera_id,)).fetchone()
        if not row:raise HTTPException(404,'Camera no longer exists.')
        if row['source_type'] == 'browser':
            raise HTTPException(409, 'Use Share laptop camera to start this source.')
        try:manager.start_camera(row)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        con.execute('UPDATE cameras SET enabled=1 WHERE id=?',(camera_id,))
        audit(con,'camera_started',camera_id,'Continuous monitoring started; resumes on server restart')
    return {'ok':True}

class BrowserCameraBody(BaseModel):
    authorized: bool
    role: Literal['in', 'observe'] = 'in'
    collect_unknowns: bool = False
    distance_mode: bool = False

@router.post('/cameras/browser')
def start_browser_camera(body: BrowserCameraBody, auth: Auth):
    if not body.authorized:
        raise HTTPException(422, 'Confirm that this camera is authorized before sharing.')
    limits = browser_capture_limits(body.distance_mode)
    with mutation_lock, db() as con:
        row = con.execute("SELECT * FROM cameras WHERE source_type='browser' LIMIT 1").fetchone()
        if row is None:
            if con.execute('SELECT COUNT(*) FROM cameras').fetchone()[0] >= 8:
                raise HTTPException(409, 'Remove an unused camera before adding another source.')
            camera_id = uid()
            con.execute(INSERT_CAMERA, (
                camera_id, 'Laptop camera', 'This browser', CIPHER.encrypt(b'browser'),
                'browser', 'Browser camera', body.role, 'appearance', 'x', .5, 1,
                limits['upload_fps'], now(), now(), int(body.distance_mode), 'live',
            ))
        else:
            camera_id = row['id']
            con.execute('UPDATE cameras SET role=?, analysis_fps=?, consent_at=?, distance_mode=? WHERE id=?',
                        (body.role, limits['upload_fps'], now(), int(body.distance_mode), camera_id))
        row = con.execute('SELECT * FROM cameras WHERE id=?', (camera_id,)).fetchone()
        try:
            share_token = manager.start_browser(row, auth)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if body.collect_unknowns:
            con.execute("UPDATE config SET value='true' WHERE key='retain_unknowns'")
            con.execute("INSERT INTO config VALUES ('revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")
        audit(con, 'camera_started', camera_id, 'Laptop camera shared from the browser')
    return {'id': camera_id, 'share_token': share_token, **limits}

@router.post('/cameras/{camera_id}/browser-frame')
def browser_frame(camera_id: str, auth: Auth, x_camera_share: Annotated[str, Header()], file: UploadFile = File(...)):
    worker = manager.browser_worker(camera_id, auth, x_camera_share)
    if worker is None:
        raise HTTPException(410, 'Camera sharing has ended. Start sharing again from Live Monitor.')
    with worker.lock:
        if time.monotonic() - worker.frame_at < .12:
            raise HTTPException(429, 'Camera frames are arriving too quickly.')
    limits = browser_capture_limits(worker.camera['distance_mode'])
    image = file.file.read(limits['frame_max_bytes'] + 1)
    if len(image) > limits['frame_max_bytes']:
        raise HTTPException(413, 'Camera frame is too large.')
    try:
        with Image.open(io.BytesIO(image)) as metadata:
            if (metadata.format != 'JPEG' or max(metadata.size) > limits['frame_max_edge']
                    or metadata.width * metadata.height > limits['frame_max_pixels']):
                raise ValueError('Invalid camera image')
        frame = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('Invalid camera image')
    except (OSError, ValueError, cv2.error, Image.DecompressionBombError):
        raise HTTPException(422, 'Use a valid camera frame in JPEG format.') from None
    with worker.lock:
        if not worker.accepts_share(auth, x_camera_share):
            raise HTTPException(410, 'Camera sharing has ended.')
        stamp = time.monotonic()
        if stamp - worker.frame_at < .12:
            raise HTTPException(429, 'Camera frames are arriving too quickly.')
        worker.frame = frame
        worker.frame_at = stamp
        if worker.state != 'running':
            worker.running_since = stamp
        worker.sequence += 1
        worker.share_deadline = stamp + 15
        worker.state = 'running'
    return {'ok': True}

@router.post('/cameras/{camera_id}/browser-stop')
def stop_browser_camera(camera_id: str, auth: Auth, x_camera_share: Annotated[str, Header()]):
    worker = manager.browser_worker(camera_id, auth, x_camera_share)
    if worker is not None:
        with worker.lock:
            if worker.accepts_share(auth, x_camera_share):
                worker.stop()
    return {'ok': True}

@router.post('/cameras/{camera_id}/stop')
def stop_camera(camera_id:str,auth:Auth):
    with mutation_lock,db() as con:
        if not con.execute('SELECT 1 FROM cameras WHERE id=?',(camera_id,)).fetchone():raise HTTPException(404,'Camera no longer exists.')
        manager.stop_camera(camera_id)
        con.execute('UPDATE cameras SET enabled=0 WHERE id=?',(camera_id,))
        audit(con,'camera_stopped',camera_id,'Monitoring paused and live frames discarded')
    return {'ok':True}

@router.delete('/cameras/{camera_id}')
def delete_camera(camera_id:str,auth:Auth):
    with mutation_lock,db() as con:
        manager.stop_camera(camera_id)
        con.execute('DELETE FROM cameras WHERE id=?',(camera_id,))
        audit(con,'camera_removed',camera_id,'Connection and encrypted credentials deleted; sighting history retained')
    return {'ok':True}

@router.get('/cameras/{camera_id}/snapshot')
def camera_snapshot(camera_id: str, auth: Auth, live: bool = False,
                    if_none_match: Annotated[str | None, Header()] = None):
    worker=manager.workers.get(camera_id)
    if not worker:raise HTTPException(404,'Camera is not running.')
    tag, jpeg = worker.snapshot(live, if_none_match)
    if tag is None:
        raise HTTPException(404,'No recent camera frame is available.')
    headers = {'ETag': tag}
    if jpeg is None:
        return Response(status_code=304, headers=headers)
    return Response(jpeg, media_type='image/jpeg', headers=headers | {'Content-Encoding': 'identity'})

@router.get('/sightings')
def sightings(auth:Auth,page:int=1,camera_id:str='',person_id:str='',q:str='',day:str=''):
    with db() as con:
        conditions=[];values=[]
        if camera_id:conditions.append('s.camera_id=?');values.append(camera_id)
        if person_id:conditions.append('s.person_id=?');values.append(person_id)
        if day:
            lower,upper=date_bounds(day,settings(con));conditions.extend(['s.created_at>=?','s.created_at<?']);values.extend([lower,upper])
        if q:
            conditions.append("(COALESCE(p.name,'Unknown person') LIKE ? OR s.camera_name LIKE ?)");values.extend(['%'+q+'%']*2)
        where=' WHERE '+' AND '.join(conditions) if conditions else ''
        base=' FROM sightings s LEFT JOIN people p ON p.id=s.person_id LEFT JOIN cameras c ON c.id=s.camera_id'
        total=con.execute('SELECT COUNT(*)'+base+where,values).fetchone()[0]
        page=max(1,min(page,max(1,(total+19)//20)))
        rows=con.execute("SELECT s.*,p.name,p.code,COALESCE(c.location,'Removed camera') location"+base+where+' ORDER BY s.created_at DESC LIMIT 20 OFFSET ?',values+[(page-1)*20]).fetchall()
        return {'items':[dict(r) for r in rows],'total':total,'page':page}

class NameFaceBody(BaseModel):
    name:str
    code:str
    department:str
    consent:bool
    verified:bool
    attendance_enabled:bool=True

def cluster_samples(con, cluster_id):
    return con.execute('''SELECT u.*, a.aligned FROM unknown_samples u
                          LEFT JOIN unknown_accuracy_images a ON a.sample_id=u.id
                          WHERE u.cluster_id=? ORDER BY u.quality DESC, u.created_at LIMIT 5''', (cluster_id,)).fetchall()

def review_features(rows):
    samples = []
    for row in rows:
        sample = {'vector': unpack(row['vector']), 'quality': row['quality']}
        if row['aligned'] and engine.accuracy_available():
            sample['aligned'] = cv2.imdecode(np.frombuffer(CIPHER.decrypt(row['aligned']), np.uint8), cv2.IMREAD_COLOR)
        samples.append(sample)
    return samples

def verify_review_group(samples, allow_second_model):
    try:
        validate_enrollment_consistency(samples)
    except HTTPException:
        if not allow_second_model:
            raise
        prepare_accuracy(samples)
        vectors = [sample.get('accuracy_vector') for sample in samples]
        if any(v is None for v in vectors) or any(float(a @ b) < .55 for a, b in combinations(vectors, 2)):
            raise HTTPException(422,'The two groups differ too much to combine. Capture more clear views of the same person.') from None
        return True
    return False

class CombineFaceBody(BaseModel):
    source_id:str
    verified:bool

@router.post('/review/{cluster_id}/combine')
def combine_faces(cluster_id:str,body:CombineFaceBody,auth:Auth):
    if not body.verified or body.source_id == cluster_id:
        raise HTTPException(422,'Choose a different group and verify that both groups show the same person.')
    with mutation_lock,engine.lock,db() as con:
        con.execute('BEGIN IMMEDIATE')
        purge_unknowns(con,settings(con))
        target=con.execute('SELECT * FROM unknowns WHERE id=?',(cluster_id,)).fetchone()
        source=con.execute('SELECT * FROM unknowns WHERE id=?',(body.source_id,)).fetchone()
        if not target or not source:
            raise HTTPException(404,'One of these groups is no longer awaiting review.')
        target_rows = cluster_samples(con, cluster_id)
        source_rows = cluster_samples(con, body.source_id)
        if min(len(target_rows),len(source_rows)) < 2:
            raise HTTPException(422,'Each group needs at least two usable samples to compare.')
        unique_crops = {CIPHER.decrypt(row['sample']) for row in target_rows[:3]}
        source_kept = []
        for row in source_rows:
            crop = CIPHER.decrypt(row['sample'])
            if crop not in unique_crops:
                source_kept.append(row)
                unique_crops.add(crop)
            if len(source_kept) == 2:
                break
        kept = target_rows[:3] + source_kept
        if len(kept) < UNKNOWN_REVIEW_FRAMES:
            raise HTTPException(422,'Capture at least three distinct usable samples before combining.')
        verify_review_group(review_features(kept), True)
        kept_ids = {row['id'] for row in kept}
        con.executemany('DELETE FROM unknown_samples WHERE id=?', ((row['id'],) for row in target_rows if row['id'] not in kept_ids))
        con.executemany('UPDATE unknown_samples SET cluster_id=? WHERE id=?', ((cluster_id,row['id']) for row in source_kept))
        con.execute('UPDATE unknown_sightings SET cluster_id=? WHERE cluster_id=?',(cluster_id,body.source_id))
        con.execute('UPDATE unknowns SET vector=?,sample=?,sightings=?,first_seen=?,last_seen=? WHERE id=?',
                    (kept[0]['vector'],kept[0]['sample'],target['sightings']+source['sightings'],
                     min(target['first_seen'],source['first_seen']),max(target['last_seen'],source['last_seen']),cluster_id))
        con.execute('INSERT OR IGNORE INTO unknown_verified_merges VALUES (?)',(cluster_id,))
        con.execute('DELETE FROM unknowns WHERE id=?',(body.source_id,))
        audit(con,'unknown_groups_combined',cluster_id,f'Source group {body.source_id} verified by administrator; sightings combined')
        item=review_item(con,con.execute('SELECT * FROM unknowns WHERE id=?',(cluster_id,)).fetchone())
    return item

@router.post('/review/{cluster_id}/name')
def name_face(cluster_id:str,body:NameFaceBody,auth:Auth):
    validate_person(body.name,body.code,body.department,body.consent)
    if not body.verified:raise HTTPException(422,'Review every sample and verify this person in person before naming.')
    with mutation_lock,engine.lock,db() as con:
        cluster=con.execute('SELECT * FROM unknowns WHERE id=?',(cluster_id,)).fetchone()
        rows=cluster_samples(con,cluster_id)
        if not cluster:raise HTTPException(404,'This face is no longer awaiting review.')
        if len(rows)<UNKNOWN_REVIEW_FRAMES:raise HTTPException(422,'Need at least three clear camera samples. Let the camera observe this face again, or enroll manually.')
        samples = review_features(rows)
        combined = con.execute('SELECT 1 FROM unknown_verified_merges WHERE cluster_id=?',(cluster_id,)).fetchone()
        used_second = verify_review_group(samples, bool(combined))
        cfg=settings(con)
        for sample in samples:
            if match(sample['vector'],gallery(con),cfg['match_threshold'],0)[0]:
                raise HTTPException(409,'This face matches an existing person. Assign the cluster to their profile instead.')
        if not used_second:
            prepare_accuracy(samples)
        pid=uid()
        try:con.execute('INSERT INTO people VALUES (?,?,?,?,?,?)',(pid,body.name.strip(),body.code.strip(),body.department.strip(),now(),now()))
        except sqlite3.IntegrityError:raise HTTPException(409,'That employee/student ID is already in use.') from None
        con.execute('INSERT INTO person_options VALUES (?,?)',(pid,int(body.attendance_enabled)))
        accuracy_count = write_enrollment(con, pid, samples)
        # Historical unknown sightings are labeled as human-named; attendance is never backdated.
        con.execute("UPDATE sightings SET person_id=?,state='named' WHERE id IN (SELECT sighting_id FROM unknown_sightings WHERE cluster_id=?)",(pid,cluster_id))
        con.execute('DELETE FROM unknowns WHERE id=?',(cluster_id,))
        audit(con,'person_named',pid,f'{len(samples)} camera templates verified by administrator; historical sightings named, attendance not backdated')
        engine.invalidate_gallery()
    return {'id':pid,'name':body.name.strip(),'templates':len(samples),'accuracy_templates':accuracy_count}
