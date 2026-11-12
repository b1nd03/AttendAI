"""Continuous camera ingestion. One latest-frame slot per source, fair CPU inference.

There is no unbounded video queue and no persistent video recording. Capture
continues when a browser closes; a stop command or server shutdown stops it.
"""
import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

from .person_detection import PERSON_MODEL, PersonMotion, person_detector
from .recognition import engine, match_observation
from .store import CIPHER, audit, db, now, pack, purge_unknowns, settings, uid, unpack
from .tracking import MultiFaceTracker, add_vote, iou, line_crossing

RECOVERY_VIEWS = ((-35, True), (0, True), (-35, False), (35, False), (-55, False), (55, False))
DETAIL_TILES = ((.5, .5), (.5, 0), (1, .5), (.5, 1), (0, .5), (0, 0), (1, 0), (1, 1), (0, 1))
UNKNOWN_REVIEW_FRAMES = 3
UNKNOWN_REVIEW_LIMIT = 500
BROWSER_MAX_PIXELS = 9_000_000
logger = logging.getLogger(__name__)

def browser_capture_limits(distance_mode):
    return {
        'frame_max_edge': 3840 if distance_mode else 1920,
        'frame_max_pixels': BROWSER_MAX_PIXELS,
        'frame_max_bytes': 1_000_000,
        'upload_fps': 3 if distance_mode else 5,
    }

def review_samples(track, current):
    """Pick distinct, compatible samples that can pass the naming workflow."""
    recent = []
    for vote in reversed(track.votes):
        if vote['person_id'] is not None or not vote['sample'].get('collect_review'):
            break
        recent.append(vote['sample'])
    selected = []
    crops = set()
    for sample in sorted(recent, key=lambda row: row['quality'], reverse=True):
        if sample['sample'] in crops:
            continue
        if float(sample['vector'] @ current['vector']) < .7:
            continue
        if all(float(sample['vector'] @ prior['vector']) >= .5 for prior in selected):
            selected.append(sample)
            crops.add(sample['sample'])
        if len(selected) == 5:
            break
    return selected

def append_review_samples(con, cluster_id, images):
    rows = con.execute('SELECT sample, vector FROM unknown_samples WHERE cluster_id=?', (cluster_id,)).fetchall()
    count = len(rows)
    known = {CIPHER.decrypt(row['sample']): unpack(row['vector']) for row in rows}
    for image in images:
        if count >= 5:
            break
        if image['sample'] in known:
            continue
        if any(float(image['vector'] @ prior) < .5 for prior in known.values()):
            continue
        sample_id = uid()
        con.execute('INSERT INTO unknown_samples VALUES (?,?,?,?,?,?)',
                    (sample_id, cluster_id, pack(image['vector']), CIPHER.encrypt(image['sample']), image['quality'], now()))
        if image.get('aligned') is not None:
            ok, jpeg = cv2.imencode('.jpg', image['aligned'], [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                con.execute('INSERT INTO unknown_accuracy_images VALUES (?,?)',
                            (sample_id, CIPHER.encrypt(jpeg.tobytes())))
        known[image['sample']] = image['vector']
        count += 1
    return count

def store_unknown(con,sample,images,cfg):
    if not cfg['retain_unknowns']:
        return None, 0
    purge_unknowns(con,cfg)
    rows=con.execute('''SELECT u.id,u.vector,m.cluster_id verified_merge FROM unknowns u
                        LEFT JOIN unknown_verified_merges m ON m.cluster_id=u.id''').fetchall()
    incoming=np.stack([image['vector'] for image in images])
    candidates=[]
    for row in rows:
        anchor=unpack(row['vector'])
        if not row['verified_merge'] and float((incoming @ anchor).max()) < .50:
            continue
        templates=[anchor, *(unpack(r['vector']) for r in con.execute(
            'SELECT vector FROM unknown_samples WHERE cluster_id=?', (row['id'],)))]
        best=np.sort((incoming @ np.stack(templates).T).max(axis=1))
        candidates.append((float(best[-2]),row['id']))
    candidates.sort(reverse=True)
    if (candidates and candidates[0][0]>=.70 and
            (len(candidates)==1 or candidates[0][0]-candidates[1][0]>=.06)):
        _,cid=candidates[0]
        count = append_review_samples(con, cid, images)
        if count >= UNKNOWN_REVIEW_FRAMES:
            con.execute('UPDATE unknowns SET sightings=sightings+1,last_seen=? WHERE id=?',(now(),cid))
            return cid, count
    if len(rows) >= UNKNOWN_REVIEW_LIMIT:
        return None, 0
    cid=uid()
    con.execute('INSERT INTO unknowns VALUES (?,?,?,1,?,?)',(cid,pack(sample['vector']),CIPHER.encrypt(sample['sample']),now(),now()))
    return cid, append_review_samples(con, cid, images)

def observe_unknown(con, camera, track, sample, images, stable, score, live, cfg, stamp):
    sighting = track.unknown_sighting_id
    group = track.unknown_cluster
    dismissed = track.review_dismissed
    retry_at = track.review_retry_at
    collecting = cfg['retain_unknowns']
    enough = stable and len(images) >= UNKNOWN_REVIEW_FRAMES
    if not sighting and stable and (not collecting or enough):
        sighting = uid()
        con.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (sighting,camera['id'],camera['name'],None,track.id,'unknown',score,live,None,0,now()))

    count = 0
    if not collecting:
        group, retry_at = None, 0.
    elif sighting and not dismissed:
        linked = con.execute('''SELECT cluster_id, (SELECT count(*) FROM unknown_samples s WHERE s.cluster_id=u.cluster_id) sample_count
                                FROM unknown_sightings u WHERE sighting_id=?''', (sighting,)).fetchone()
        if linked:
            group, count = linked['cluster_id'], linked['sample_count']
            if count < 5 and enough:
                count = append_review_samples(con, group, images)
        else:
            # A combine moves the sighting link. Explicit deletion suppresses
            # re-saving this continuous track, unlike expiry or collection toggles.
            dismissed = bool(group and con.execute(
                "SELECT 1 FROM audit WHERE action='cluster_deleted' AND subject=? LIMIT 1", (group,)).fetchone())
            group = None
            if enough and not dismissed and stamp >= retry_at:
                group, count = store_unknown(con, sample, images, cfg)
                if group:
                    con.execute('INSERT INTO unknown_sightings VALUES (?,?)', (sighting, group))
                    retry_at = 0.
                else:
                    retry_at = stamp + 2.
    return sighting, group, count, dismissed, retry_at

class RoomProcessor:
    def __init__(self,camera):
        self.camera=dict(camera)
        self.tracker=MultiFaceTracker()
        self.retired = False
        self.epoch=engine.gallery_epoch
        self.people_motion = PersonMotion()
        self.people_shape = None
        self.next_people_scan = 0.
        self.recovery_turn = 0
        self.next_recovery = 0.
        self.detail_tile = 0
        self.next_detail_scan = 0.
        self.detail_tile_hold_until = 0.

    def process(self,frame,cfg,stop=None):
        from .main import accuracy_gallery, gallery, mutation_lock, record_attendance, auto_update
        if self.epoch!=engine.gallery_epoch:
            self.tracker=MultiFaceTracker();self.epoch=engine.gallery_epoch
        quality_settings = cfg['min_face_size'], cfg['min_blur']
        mode = self.camera.get('operation_mode', 'live')
        if mode == 'preview':
            return []
        testing = mode == 'setup'
        options = {'quality_only': True} if testing else {}
        overlays = []
        if self.camera.get('distance_mode') and PERSON_MODEL.is_file():
            stamp = time.monotonic()
            detections = None
            if stamp >= self.next_people_scan or self.people_shape != frame.shape[:2]:
                detections = person_detector().detect(frame)
                self.people_shape = frame.shape[:2]
                self.next_people_scan = stamp + (.95 if frame.shape[1] >= 1920 else .65)
            people = self.people_motion.update(frame, detections)
            samples = engine.detect_frame(frame, cfg, people, **options)
            overlays = [{'box': box.tolist(), 'label': 'Person', 'state': 'person'} for box in people]
        else:
            samples = engine.detect_frame(frame, cfg, **options)
        stamp = time.monotonic()
        scan_detail = (self.camera.get('distance_mode')
                       and max(frame.shape[:2]) >= 1920 and stamp >= self.next_detail_scan
                       and not any(sample['eligible'] for sample in samples))
        if scan_detail:
            details = engine.detect_native_tile(frame, cfg, DETAIL_TILES[self.detail_tile], **options)
            if any(sample['eligible'] for sample in details):
                self.detail_tile_hold_until = stamp + 1.
            if stamp < self.detail_tile_hold_until:
                self.next_detail_scan = stamp + .18
            else:
                self.detail_tile = (self.detail_tile + 1) % len(DETAIL_TILES)
                self.next_detail_scan = stamp + .45
            if details:
                samples = details + [sample for sample in samples
                                     if all(iou(sample['box'], detail['box']) <= .35 for detail in details)]
        if not samples and not scan_detail and stamp >= self.next_recovery:
            angle, enhanced = RECOVERY_VIEWS[self.recovery_turn]
            samples = engine.recover_frame(frame, cfg, angle, enhanced=enhanced, **options)
            self.next_recovery = stamp + (.12 if samples else .28)
            if not samples:
                self.recovery_turn = (self.recovery_turn + 1) % len(RECOVERY_VIEWS)
        if testing:
            for index, sample in enumerate(samples):
                usable = sample['eligible']
                overlays.append({'box': sample['box'].tolist(), 'track': str(index),
                                 'state': 'setup_usable' if usable else 'quality',
                                 'label': 'Usable face' if usable else 'Face detected',
                                 'face_pixels': sample.get('face_pixels'), 'message': sample.get('error')})
            return overlays
        if not samples:
            self.tracker.update([])
            return overlays
        h,w=frame.shape[:2]
        committed_events = []
        committed_reviews = []
        committed_feedback = []
        line_mode = self.camera['trigger_mode'] == 'line'
        with mutation_lock,engine.lock,db() as con:
            if self.retired or (stop is not None and stop.is_set()):return []
            source = con.execute('SELECT operation_mode FROM cameras WHERE id=?', (self.camera['id'],)).fetchone()
            if source is None or source['operation_mode'] != 'live':return []
            cfg=settings(con)
            # Drop a frame evaluated against an old gallery or quality policy.
            if self.epoch != engine.gallery_epoch or quality_settings != (cfg['min_face_size'], cfg['min_blur']):
                self.tracker=MultiFaceTracker();self.epoch=engine.gallery_epoch
                return overlays
            pairs=self.tracker.update(samples)
            vectors=gallery(con)
            second_index = accuracy_gallery(con, vectors)
            for track,sample in pairs:
                box=sample['box'].tolist()
                if not sample.get('eligible'):
                    result = {'box':box,'label':'Face detected','state':'quality','track':track.id,'message':sample['error']}
                    if 'required_face_pixels' in sample:
                        result.update(face_pixels=sample['face_pixels'], required_face_pixels=sample['required_face_pixels'])
                    overlays.append(result)
                    continue
                pid,score,gap,checked=match_observation(sample,vectors,second_index,cfg)
                crossed = line_crossing(track, self.camera, w, h, identity=pid) if line_mode else 0
                if pid or not cfg['retain_unknowns'] or not engine.accuracy_available():
                    sample.pop('aligned', None)
                sample['collect_review'] = not pid and cfg['retain_unknowns']
                sample['accuracy_checked'] = checked
                if checked:
                    second_index = None  # At most one heavier inference per analyzed frame.
                needed = cfg['vote_frames'] if pid else UNKNOWN_REVIEW_FRAMES
                stable,votes,live=add_vote(track,sample,pid,score,gap,needed)
                if not pid:
                    images = review_samples(track, sample) if sample['collect_review'] else []
                    sighting, cluster_id, count, dismissed, retry_at = observe_unknown(
                        con, self.camera, track, sample, images, stable, score, live, cfg, stamp)
                    committed_reviews.append((track, sighting, cluster_id, dismissed, retry_at))
                    if cfg['retain_unknowns']:
                        stable = stable and (len(images) >= needed or count >= needed or dismissed)
                verified=stable and live>=cfg['liveness_threshold']
                state='recognized' if pid and verified else 'candidate' if pid and stable else 'unknown' if stable else 'verifying'
                person = con.execute(
                    'SELECT p.name, COALESCE(o.attendance_enabled, 1) attendance_enabled '
                    'FROM people p LEFT JOIN person_options o ON p.id=o.person_id WHERE p.id=?',
                    (pid,),
                ).fetchone() if stable and pid else None
                label = person['name'] if person else 'Unknown person' if stable else 'Verifying face'
                if not pid and not stable and cfg['retain_unknowns']:
                    label = 'Collecting face samples'
                if state=='candidate':label+=' · needs verification'
                result={'box':box,'label':label,'state':state,'track':track.id,'person_id':pid if stable else None,
                        'score':round(score,3),'votes':len(votes),'needed':needed,'liveness':round(live,3)}
                if not pid and cfg['retain_unknowns']:
                    if dismissed:
                        result.update(review_status='dismissed', message='Face group removed. Step out of view for a few seconds, then return to collect again.')
                    elif count >= UNKNOWN_REVIEW_FRAMES:
                        result.update(review_status='ready', review_group_id=cluster_id, message='This face is ready to name in Name People.')
                    elif retry_at:
                        result.update(review_status='full', message='The review queue is full. Name or remove a saved group to make room.')
                    else:
                        result.update(review_status='collecting', review_samples=min(len(images), needed),
                                      message=f'{min(len(images), needed)} of {needed} usable samples. Turn slightly for another view.')
                elif not pid and stable:
                    result.update(review_status='off', message='Saving unknown faces is off. Enable it in Settings to collect samples.')
                elif pid and not stable:
                    result['message'] = f'{len(votes)} of {needed} matching frames.'
                used_accuracy = any(v['sample'].get('accuracy_checked') for v in votes)
                overlays.append(result)
                if not person:continue
                role=self.camera['role']
                triggered = not line_mode or crossed != 0
                direction_ok = not line_mode or crossed == self.camera['direction'] or role == 'entry_exit'
                emitted = track.crossing_emitted if line_mode else track.emitted
                kind = ('in' if crossed==self.camera['direction'] else 'out') if role=='entry_exit' else role
                key = (pid, kind)
                message = track.attendance_feedback.get(key)
                attendance_kind = None
                if role == 'observe':
                    result['message'] = 'Sightings only on this camera.'
                elif not person['attendance_enabled']:
                    result['message'] = 'Automatic attendance is paused for this person.'
                elif not triggered or not direction_ok:
                    result['message'] = 'Attendance waits for a crossing in the configured direction.'
                elif message is not None:
                    result['message'] = message
                elif not verified:
                    result['message'] = 'Attendance pending. The camera is still checking liveness.'
                    result['manual_kind'] = kind
                else:
                    attendance = record_attendance(con,pid,kind,min(v['score'] for v in votes),'camera',f"{self.camera['name']} · {self.camera['location']}",cfg)
                    if not attendance['duplicate']:
                        attendance_kind = kind
                    message = attendance['message']
                    if not attendance.get('retryable'):
                        committed_feedback.append((track, key, message))
                    result['message'] = message
                sighting_key = (pid, state)
                if not triggered or not direction_ok or (sighting_key in emitted and not attendance_kind):continue
                # Per-camera sightings and organization-wide attendance have separate cooldowns.
                cutoff=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
                recent=None if line_mode else con.execute(
                    'SELECT 1 FROM sightings WHERE person_id=? AND camera_id=? AND state=? AND created_at>?',
                    (pid,self.camera['id'],state,cutoff)).fetchone()
                committed_events.append((emitted, sighting_key))
                if recent and not attendance_kind:continue
                learned=False
                if verified and not used_accuracy:
                    # A new camera angle may improve a known gallery even if attendance
                    # was already recorded elsewhere. All strict learning gates still apply.
                    secure_sample={**sample,'liveness':live,'quality':min(v['quality'] for v in votes),
                                   'learning_quality':min(v['sample'].get('learning_quality', v['quality']) for v in votes)}
                    learned=auto_update(con,pid,secure_sample,min(v['score'] for v in votes),min(v['margin'] for v in votes),cfg)
                con.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (uid(),self.camera['id'],self.camera['name'],pid,track.id,state,score,live,attendance_kind,int(learned),now()))
        for emitted, event_key in committed_events:
            emitted.add(event_key)
        for track, sighting, cluster_id, dismissed, retry_at in committed_reviews:
            track.unknown_sighting_id = sighting
            track.unknown_cluster = cluster_id
            track.review_dismissed = dismissed
            track.review_retry_at = retry_at
        for track, key, message in committed_feedback:
            track.attendance_feedback[key] = message
        return overlays

def bounded_frame(frame, limit):
    h, w = frame.shape[:2]
    edge = max(h, w)
    if edge <= limit:
        return frame
    return cv2.resize(frame, (max(1, round(w * limit / edge)), max(1, round(h * limit / edge))),
                      interpolation=cv2.INTER_AREA)

def encode_preview(frame, overlays, camera):
    output = bounded_frame(frame, 720)
    show_line = camera['trigger_mode'] == 'line' and camera.get('operation_mode') != 'preview'
    if output is frame and (overlays or show_line):
        output = frame.copy()
    h, w = output.shape[:2]
    scale = np.array([w / frame.shape[1], h / frame.shape[0]] * 2)
    if show_line:
        pos=camera['line_position']
        points=((int(pos*w),0),(int(pos*w),h)) if camera['line_axis']=='x' else ((0,int(pos*h)),(w,int(pos*h)))
        cv2.line(output,*points,(220,175,90),2)
    for obj in overlays:
        x,y,bw,bh=map(int,obj['box'] * scale)
        color=(110,190,110) if obj['state'] in ('recognized', 'setup_usable') else (80,190,235) if obj['state']=='candidate' else (210,180,95) if obj['state']=='person' else (210,160,110)
        cv2.rectangle(output,(x,y),(x+bw,y+bh),color,2)
        text=obj['label'].encode('ascii','replace').decode()[:50]
        cv2.putText(output,text,(max(0,x),max(16,y-7)),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1,cv2.LINE_AA)
    _,jpeg=cv2.imencode('.jpg',output,[cv2.IMWRITE_JPEG_QUALITY,74])
    return jpeg.tobytes()

class CameraWorker:
    def __init__(self,camera,capture_factory=None):
        self.camera=dict(camera)
        self.stop_event=threading.Event()
        self.lock=threading.RLock()
        self.preview_lock = threading.Lock()
        self.preview_jpegs = {}
        self.preview_id = secrets.token_hex(8)
        self.thread=None
        self.frame=None;self.frame_at=0;self.sequence=0;self.used_sequence=-1;self.last_processed=0
        self.preview=None;self.preview_at=0;self.overlays=[]
        self.preview_sequence = -1
        self.connection_epoch=0;self.last_completed=0
        self.running_since = 0.
        self.state='stopped';self.error='';self.latency_ms=0;self.processed_fps=0
        self.processor=RoomProcessor(self.camera)
        self.setup_test = None
        self.setup_profile = None
        self.capture_factory=capture_factory or self.open_capture
        self.share_token = None
        self.share_owner = None
        self.share_deadline = 0.0

    @staticmethod
    def open_capture(source,source_type):
        if source_type=='usb':
            cap = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(int(source), cv2.CAP_MSMF)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,1280);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,720)
            return cap
        return cv2.VideoCapture(source,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,4000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,4000])

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear();self.state='connecting'
        self.thread=threading.Thread(target=self.run,name='presence-capture-'+self.camera['id'][:6],daemon=True)
        self.thread.start()

    def stop(self):
        with self.lock:
            self.stop_event.set();self.state='stopping' if self.thread and self.thread.is_alive() else 'stopped'
            self.frame=None;self.preview=None;self.overlays=[]
            self.preview_jpegs.clear()
            self.share_token = None
            self.share_owner = None
            self.share_deadline = 0.0

    def accepts_share(self, owner, token):
        return (len(token) == 43 and token.isascii() and self.share_owner == owner and self.share_token is not None
                and time.monotonic() < self.share_deadline
                and hmac.compare_digest(self.share_token, token))

    def reset_analysis(self):
        """Caller holds the worker lock. Ignore frames captured under the old profile."""
        self.processor.retired = True
        self.processor = RoomProcessor(self.camera)
        self.connection_epoch += 1
        self.used_sequence = self.sequence
        self.preview = None
        self.overlays = []
        self.preview_jpegs.clear()
        self.running_since = time.monotonic()
        self.last_completed = self.processed_fps = 0
        self.error = ''
        self.setup_test = self.setup_profile = None

    def measure_setup(self, overlays, cfg, source_shape, analysis_shape):
        profile = (cfg['min_face_size'], cfg['min_blur'], source_shape, analysis_shape)
        if profile != self.setup_profile:
            self.setup_profile = profile
            self.setup_test = {'frames': 0, 'face_frames': 0, 'usable_frames': 0,
                               'largest_face_pixels': 0, 'required_face_pixels': cfg['min_face_size'],
                               'latest_blocker': None}
        faces = [item for item in overlays if item['state'] != 'person']
        test = self.setup_test
        usable = any(item['state'] == 'setup_usable' for item in faces)
        test['frames'] += 1
        test['face_frames'] += bool(faces)
        test['usable_frames'] += usable
        if faces:
            largest = max(faces, key=lambda item: item.get('face_pixels') or 0)
            test['largest_face_pixels'] = max(test['largest_face_pixels'], largest.get('face_pixels') or 0)
            test['latest_blocker'] = None if usable else largest.get('message')

    def snapshot(self, live, previous):
        # Serialize encodes per camera, without blocking capture or analysis.
        with self.preview_lock:
            with self.lock:
                frame, stamp, sequence = (
                    (self.frame, self.frame_at, self.sequence) if live else
                    (self.preview, self.preview_at, self.preview_sequence)
                )
                if frame is None or self.stop_event.is_set() or time.monotonic() - stamp > 4:
                    return None, None
                tag = f'"{self.preview_id}-{self.connection_epoch}-{sequence}-{int(live)}"'
                if previous == tag:
                    return tag, None
                cached = self.preview_jpegs.get(live)
                if cached and cached[0] == tag:
                    return cached
                overlays = [] if live else self.overlays
                epoch = self.connection_epoch
            jpeg = encode_preview(frame, overlays, self.camera)
            with self.lock:
                if self.state != 'running' or self.stop_event.is_set() or self.connection_epoch != epoch:
                    return None, None
                self.preview_jpegs[live] = (tag, jpeg)
            return tag, jpeg

    def run(self):
        backoff=1
        source=CIPHER.decrypt(self.camera['source']).decode()
        while not self.stop_event.is_set():
            cap=None
            try:
                with self.lock:self.state='connecting'
                cap=self.capture_factory(source,self.camera['source_type'])
                if not cap.isOpened():raise RuntimeError('not opened')
                with self.lock:
                    self.state='running'
                    self.reset_analysis()
                backoff=1
                while not self.stop_event.is_set():
                    ok,frame=cap.read()
                    if not ok or frame is None:raise RuntimeError('stream ended')
                    with self.lock:
                        if self.stop_event.is_set():break
                        self.frame=frame;self.frame_at=time.monotonic();self.sequence+=1
            except Exception:
                with self.lock:
                    self.state='reconnecting';self.error='Cannot read the camera. Check its address, credentials, network, or USB connection.'
                    self.frame=None;self.preview=None;self.overlays=[]
                    self.preview_jpegs.clear()
            finally:
                if cap is not None:cap.release()
            if self.stop_event.wait(backoff):break
            backoff=min(30,backoff*2)
        with self.lock:
            self.state='stopped';self.frame=None;self.preview=None;self.overlays=[]

    def public(self):
        with self.lock:
            stamp = time.monotonic()
            age = stamp - self.frame_at if self.frame is not None else None
            analysis_age = stamp - self.preview_at if self.preview is not None else float('inf')
            mode = self.camera.get('operation_mode', 'live')
            if self.state != 'running' or age is None or age >= 4:
                analysis_state = 'waiting'
            elif mode == 'preview':
                analysis_state = 'paused'
            elif self.error:
                analysis_state = 'error'
            elif analysis_age < 2.5:
                analysis_state = 'current'
            elif self.preview is None and stamp - self.running_since < 2.5:
                analysis_state = 'starting'
            else:
                analysis_state = 'delayed'
            overlays = self.overlays if analysis_state == 'current' else []
            return {k:self.camera[k] for k in ['id','name','location','source_type','source_hint','role','trigger_mode','line_axis','line_position','direction','analysis_fps']}|{
                'state':self.state,'error':self.error,'latency_ms':self.latency_ms,
                'analysis_state':analysis_state,
                'processed_fps':round(self.processed_fps,1),'frame_age_seconds':round(age,1) if age is not None else None,
                'analysis_size':(self.preview.shape[1], self.preview.shape[0]) if analysis_age < 2.5 else None,
                'source_size':(self.frame.shape[1], self.frame.shape[0]) if age is not None and age < 4 else None,
                'operation_mode':mode, 'setup_test':dict(self.setup_test) if self.setup_test else None,
                'distance_mode':bool(self.camera.get('distance_mode')),
                'people_count':sum(obj['state']=='person' for obj in overlays),
                'tracks':[obj for obj in overlays if obj['state'] != 'person'],'preview_ready':age is not None and age<4,
                'enabled':not self.stop_event.is_set() and self.state!='stopped'}

class CameraManager:
    def __init__(self):
        self.workers={};self.lock=threading.RLock();self.stop_event=threading.Event();self.thread=None

    def ensure_scheduler(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear()
        self.thread=threading.Thread(target=self.run,name='presence-inference',daemon=True);self.thread.start()

    def start_camera(self,camera):
        with self.lock:
            existing=self.workers.get(camera['id'])
            if existing and existing.thread and existing.thread.is_alive():
                if existing.stop_event.is_set():raise ValueError('Camera is still stopping. Wait a few seconds, then start again.')
                return
            if sum(w.state!='stopped' for w in self.workers.values())>=8:
                raise ValueError('Up to eight cameras can run on this local instance.')
            worker=CameraWorker(camera);self.workers[camera['id']]=worker
            self.ensure_scheduler();worker.start()

    def start_browser(self, camera, owner):
        with self.lock:
            existing = self.workers.get(camera['id'])
            if existing and existing.share_token and time.monotonic() < existing.share_deadline:
                raise ValueError('The laptop camera is already shared. Stop it in Live Monitor before starting again.')
            if sum(w.state != 'stopped' for key, w in self.workers.items() if key != camera['id']) >= 8:
                raise ValueError('Up to eight cameras can run at once.')
            if existing:
                existing.stop()
            worker = CameraWorker(camera)
            worker.share_token = secrets.token_urlsafe(32)
            worker.share_owner = owner
            worker.share_deadline = time.monotonic() + 15
            worker.state = 'connecting'
            self.workers[camera['id']] = worker
            self.ensure_scheduler()
            return worker.share_token

    def browser_worker(self, camera_id, owner, token):
        with self.lock:
            worker = self.workers.get(camera_id)
            if worker is None:
                return None
            with worker.lock:
                return worker if worker.accepts_share(owner, token) else None

    def stop_browser_shares(self, owner):
        with self.lock:
            for worker in self.workers.values():
                with worker.lock:
                    if worker.share_owner == owner:
                        worker.stop()

    def stop_camera(self,camera_id):
        with self.lock:
            if camera_id in self.workers:self.workers[camera_id].stop()

    def set_distance_mode(self, camera_id, enabled):
        self.set_analysis_setting(camera_id, 'distance_mode', enabled)

    def set_camera_mode(self, camera_id, mode):
        self.set_analysis_setting(camera_id, 'operation_mode', mode)

    def set_analysis_setting(self, camera_id, key, value):
        with self.lock:
            worker = self.workers.get(camera_id)
            if worker:
                with worker.lock:
                    if worker.camera.get(key) != value:
                        worker.camera[key] = value
                        worker.reset_analysis()

    def resume(self):
        with db() as con:rows=con.execute("SELECT * FROM cameras WHERE enabled=1 AND source_type!='browser'").fetchall()
        for row in rows:self.start_camera(row)

    def shutdown(self):
        self.stop_event.set()
        with self.lock:workers=list(self.workers.values())
        for worker in workers:worker.stop()
        deadline=time.monotonic()+5
        for thread in [*(w.thread for w in workers),self.thread]:
            if thread:thread.join(timeout=max(0,deadline-time.monotonic()))

    def run(self):
        while not self.stop_event.is_set():
            with self.lock:workers=list(self.workers.values())
            for worker in workers:
                if self.stop_event.is_set():break
                with worker.lock:
                    if worker.share_token and time.monotonic() >= worker.share_deadline:
                        worker.stop()
                    if worker.stop_event.is_set() or worker.state!='running' or worker.frame is None:continue
                    if worker.camera.get('operation_mode') == 'preview':continue
                    current=time.monotonic()
                    if current-worker.frame_at>1.5 or worker.sequence==worker.used_sequence or current-worker.last_processed<1/worker.camera['analysis_fps']:continue
                    frame = worker.frame
                    frame_at = worker.frame_at
                    sequence = worker.sequence
                    processor = worker.processor
                    worker.used_sequence=sequence;worker.last_processed=current
                    connection_epoch=worker.connection_epoch
                try:
                    with db() as con:cfg=settings(con)
                    distance = processor.camera.get('distance_mode')
                    source_shape = frame.shape[:2]
                    if processor.camera['source_type'] == 'browser':
                        limit = browser_capture_limits(distance)['frame_max_edge']
                    else:
                        limit = 4096 if distance and PERSON_MODEL.is_file() else 1280
                    frame = bounded_frame(frame, limit)
                    overlays = processor.process(frame, cfg, worker.stop_event)
                    with worker.lock:
                        if worker.stop_event.is_set() or worker.state!='running' or worker.connection_epoch!=connection_epoch:continue
                        completed=time.monotonic()
                        worker.preview = frame
                        worker.preview_at = frame_at
                        worker.preview_sequence = sequence
                        worker.overlays = overlays
                        if processor.camera.get('operation_mode') == 'setup':
                            worker.measure_setup(overlays, cfg, source_shape, frame.shape[:2])
                        if worker.last_completed:
                            instantaneous=1/max(.001,completed-worker.last_completed)
                            worker.processed_fps=instantaneous if not worker.processed_fps else worker.processed_fps*.7+instantaneous*.3
                        worker.last_completed=completed
                        worker.latency_ms=round((completed-frame_at)*1000);worker.error=''
                except Exception:
                    message = 'Face analysis could not process this frame. Try reconnecting the camera.'
                    with worker.lock:
                        if worker.connection_epoch != connection_epoch:continue
                        if worker.error != message:
                            logger.exception('Camera analysis failed for source %s', worker.camera['id'])
                        worker.error = message
            self.stop_event.wait(.01)

manager=CameraManager()
