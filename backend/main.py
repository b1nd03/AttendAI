import base64
import asyncio
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, time as dt_time, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager, suppress

import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware

from .store import CIPHER, DEFAULTS, ROOT, audit, db, now, pack, purge_unknowns, settings, uid, unpack
from .recognition import ACCURACY_VERSION, FaceGallery, engine, match, normalized
from .monitoring import manager

@asynccontextmanager
async def lifespan(app):
    async def expire_unknowns():
        while True:
            with db() as con:
                purge_unknowns(con,settings(con))
            await asyncio.sleep(60)
    task=asyncio.create_task(expire_unknowns())
    warm_models = asyncio.create_task(asyncio.to_thread(engine.load))
    manager.resume()
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(manager.shutdown)
        await warm_models

app = FastAPI(title="Presence Attendance", version="1.0.0",lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","testserver"])
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=3)
mutation_lock = threading.RLock()
attempts = defaultdict(deque)
SESSION_SECONDS = 12*3600
HASHED_ASSET = re.compile(r"/assets/[^/]+-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+$")

@app.middleware("http")
async def security_headers(request, call_next):
    origin = request.headers.get("origin")
    allowed = {"http://localhost:8000","http://127.0.0.1:8000",
               "http://localhost:5173","http://127.0.0.1:5173","http://testserver"}
    allowed.add(str(request.base_url).rstrip('/'))
    if request.method not in ("GET","HEAD","OPTIONS") and origin and origin not in allowed:
        return Response("Origin is not allowed",status_code=403)
    if request.headers.get("sec-fetch-site")=="cross-site" and request.method not in ("GET","HEAD","OPTIONS"):
        return Response("Cross-site writes are not allowed",status_code=403)
    if int(request.headers.get("content-length","0")) > 32_000_000:
        return Response("Upload is too large",status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="same-origin"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Permissions-Policy"]="camera=(self), microphone=()"
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable"
        if response.status_code in (200, 304) and HASHED_ASSET.fullmatch(request.url.path)
        else "no-store"
    )
    response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ws://127.0.0.1:5173 ws://localhost:5173; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def authorized(request: Request):
    token=request.cookies.get("presence_session","")
    with db() as con:
        session=con.execute("SELECT * FROM sessions WHERE token_hash=? AND expires>?",(token_hash(token),time.time())).fetchone()
    if not session:
        raise HTTPException(401,"Sign in to your local workspace to continue.")
    return token_hash(token)

Auth = Annotated[str, Depends(authorized)]

class Credentials(BaseModel):
    password: str = Field(min_length=12,max_length=128)

def set_session(con,response):
    token=secrets.token_urlsafe(40)
    con.execute("DELETE FROM sessions WHERE expires<?",(time.time(),))
    con.execute("INSERT INTO sessions VALUES (?,?)",(token_hash(token),time.time()+SESSION_SECONDS))
    response.set_cookie("presence_session",token,max_age=SESSION_SECONDS,httponly=True,samesite="strict",secure=False)

@app.get("/api/health")
def health(request: Request):
    token = request.cookies.get("presence_session")
    with db() as con:
        setup=con.execute("SELECT value FROM config WHERE key='admin_password'").fetchone() is not None
        authenticated = bool(token and con.execute(
            "SELECT 1 FROM sessions WHERE token_hash=? AND expires>?", (token_hash(token), time.time())
        ).fetchone())
    return {"ok":True,"configured":setup,"authenticated":authenticated,"engine":engine.status()}

@app.get("/api/session")
def session(auth: Auth):
    return {"authenticated":True}

@app.post("/api/setup")
def setup(body:Credentials,response:Response,request:Request):
    if request.client.host not in ("127.0.0.1","::1","testclient"):
        raise HTTPException(403,"Initial setup is available on this computer only.")
    with mutation_lock, db() as con:
        if con.execute("SELECT 1 FROM config WHERE key='admin_password'").fetchone():
            raise HTTPException(409,"Workspace is already configured. Sign in instead.")
        salt=secrets.token_hex(16)
        digest=hashlib.pbkdf2_hmac('sha256',body.password.encode(),bytes.fromhex(salt),600_000).hex()
        con.execute("INSERT INTO config VALUES ('admin_password',?)",(json.dumps({"salt":salt,"digest":digest}),))
        set_session(con,response)
        audit(con,"workspace_created","workspace","Local administrator configured")
    return {"ok":True}

@app.post("/api/login")
def login(body:Credentials,response:Response,request:Request):
    key=request.client.host
    with mutation_lock, db() as con:
        recent=attempts[key]
        while recent and recent[0]<time.time()-300:
            recent.popleft()
        if len(recent)>=10:
            raise HTTPException(429,"Too many attempts. Try again in five minutes.")
        recent.append(time.time())
        row=con.execute("SELECT value FROM config WHERE key='admin_password'").fetchone()
        if not row:
            raise HTTPException(409,"Create your workspace first.")
        record=json.loads(row[0])
        digest=hashlib.pbkdf2_hmac('sha256',body.password.encode(),bytes.fromhex(record["salt"]),600_000).hex()
        if not hmac.compare_digest(digest,record["digest"]):
            raise HTTPException(401,"Password is incorrect. Try again.")
        recent.clear()
        set_session(con,response)
    return {"ok":True}

@app.post("/api/logout")
def logout(response:Response,auth:Auth):
    manager.stop_browser_shares(auth)
    with db() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?",(auth,))
    response.delete_cookie("presence_session")
    return {"ok":True}

def gallery(con):
    if engine.gallery_cache is not None:
        return engine.gallery_cache
    result=defaultdict(list)
    for row in con.execute("SELECT person_id,vector FROM templates WHERE active=1"):
        result[row["person_id"]].append(unpack(row["vector"]))
    engine.gallery_cache=FaceGallery(result)
    return engine.gallery_cache

def accuracy_gallery(con, baseline):
    if not baseline.people or not engine.accuracy_available():
        return None
    if engine.accuracy_cache is None:
        groups = defaultdict(list)
        for row in con.execute("""
            SELECT t.person_id, a.vector FROM accuracy_templates a
            JOIN templates t ON t.id=a.template_id
            WHERE t.active=1 AND t.source='enrollment' AND a.model=?
        """, (ACCURACY_VERSION,)):
            groups[row['person_id']].append(unpack(row['vector']))
        engine.accuracy_cache = FaceGallery({pid: rows for pid, rows in groups.items() if len(rows) >= 3})
    index = engine.accuracy_cache
    # Searching an incomplete roster could hide a competing identity.
    return index if set(index.people) == set(baseline.people) else None

def people_rows(con):
    return [dict(r) for r in con.execute("""SELECT p.*,COUNT(t.id) AS templates,
              COUNT(a.template_id) AS accuracy_templates,
              COALESCE(o.attendance_enabled,1) AS attendance_enabled FROM people p
              LEFT JOIN person_options o ON o.person_id=p.id
              LEFT JOIN templates t ON t.person_id=p.id AND t.active=1
              LEFT JOIN accuracy_templates a ON a.template_id=t.id AND a.model=?
              GROUP BY p.id ORDER BY p.name COLLATE NOCASE""", (ACCURACY_VERSION,))]

def date_bounds(day,cfg):
    try:
        d=date.fromisoformat(day)
    except ValueError:
        raise HTTPException(422,"Choose a valid date.") from None
    zone=ZoneInfo(cfg["timezone"])
    start=datetime.combine(d,dt_time.min,zone).astimezone(timezone.utc).isoformat()
    end=datetime.combine(d+timedelta(days=1),dt_time.min,zone).astimezone(timezone.utc).isoformat()
    return start,end

@app.get("/api/dashboard")
def dashboard(auth:Auth,day:str|None=None):
    with db() as con:
        cfg=settings(con)
        purge_unknowns(con,cfg)
        selected=day or datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
        start,end=date_bounds(selected,cfg)
        people=people_rows(con)
        events=[dict(r) for r in con.execute("""SELECT a.*,p.name,p.code,p.department FROM attendance a
              JOIN people p ON p.id=a.person_id WHERE a.created_at>=? AND a.created_at<? ORDER BY a.created_at DESC""",(start,end))]
        seen={r['person_id'] for r in events if r['kind']=='in'}
        latest={}
        for event in events:
            latest.setdefault(event['person_id'],event['kind'])
        buckets=[0]*24
        for event in events:
            if event['kind']=='in':
                buckets[datetime.fromisoformat(event['created_at']).astimezone(ZoneInfo(cfg['timezone'])).hour]+=1
        return {"people":people,"events":events[:200],"day":selected,"hourly":buckets,
                "stats":{"enrolled":len(people),"present":len(seen),"inside":sum(v=='in' for v in latest.values()),
                         "absent":len(people)-len(seen),"review":con.execute('SELECT COUNT(*) FROM unknowns').fetchone()[0],
                         "templates":con.execute('SELECT COUNT(*) FROM templates WHERE active=1').fetchone()[0]},
                "settings":cfg,"engine":engine.status()}

@app.get("/api/people")
def people(auth:Auth,q:str="",page:int=1):
    with db() as con:
        rows=[r for r in people_rows(con) if q.casefold() in (r['name']+' '+r['code']+' '+r['department']).casefold()]
    page=max(1,min(page,max(1,(len(rows)+11)//12)))
    return {"items":rows[(page-1)*12:page*12],"total":len(rows),"page":page}

def validate_person(name,code,department,consent):
    if not consent:
        raise HTTPException(422,"Record the person's explicit biometric consent before enrolling.")
    if not 2<=len(name.strip())<=100 or not 1<=len(code.strip())<=40 or not 1<=len(department.strip())<=80:
        raise HTTPException(422,"Enter a name (2–100 characters), ID (1–40), and department (1–80).")

def enrollment_samples(files, supervised):
    if not supervised:
        raise HTTPException(422,"An administrator must verify the person's identity in person.")
    if not 3<=len(files)<=5:
        raise HTTPException(422,"Capture or choose 3–5 different images of this person.")
    with db() as con:
        cfg=settings(con)
    samples=[]
    for upload in files:
        raw=upload.file.read(5_000_001)
        try:
            samples.append(engine.extract(raw,cfg))
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
    if len({s['fingerprint'] for s in samples})!=len(samples):
        raise HTTPException(422,"Use different images for each template.")
    validate_enrollment_consistency(samples)
    prepare_accuracy(samples)
    return samples, cfg

def validate_enrollment_consistency(samples):
    for first, second in combinations(samples, 2):
        if float(first['vector'] @ second['vector']) < .5:
            raise HTTPException(422,"The images do not consistently match one person. Capture again.")

def prepare_accuracy(samples):
    for sample in samples:
        if sample.get('aligned') is not None:
            sample['accuracy_vector'] = engine.accuracy_vector(sample['aligned'])

def write_enrollment(con, person_id, samples):
    ready_count = sum(sample.get('accuracy_vector') is not None for sample in samples)
    complete = ready_count >= 3
    for sample in samples:
        template_id = uid()
        con.execute("INSERT INTO templates VALUES (?,?,?,?,?,1,NULL,NULL,?)",
                    (template_id,person_id,pack(sample['vector']),sample['quality'],'enrollment',now()))
        if complete and sample.get('accuracy_vector') is not None:
            con.execute("INSERT INTO accuracy_templates VALUES (?,?,?)",
                        (template_id,ACCURACY_VERSION,pack(sample['accuracy_vector'])))
    return ready_count if complete else 0

@app.post("/api/people")
def enroll(auth:Auth,name:Annotated[str,Form()],code:Annotated[str,Form()],department:Annotated[str,Form()],
           consent:Annotated[bool,Form()],supervised:Annotated[bool,Form()],files:list[UploadFile]=File(...)):
    validate_person(name,code,department,consent)
    samples, cfg = enrollment_samples(files, supervised)
    with mutation_lock,engine.lock,db() as con:
        for s in samples:
            existing,score,_=match(s['vector'],gallery(con),cfg['match_threshold'],0)
            if existing:
                raise HTTPException(409,"This face is already enrolled. Open the existing person's profile.")
        person_id=uid()
        try:
            con.execute("INSERT INTO people VALUES (?,?,?,?,?,?)",(person_id,name.strip(),code.strip(),department.strip(),now(),now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"That ID is already in use. Choose another ID.") from None
        accuracy_count = write_enrollment(con, person_id, samples)
        audit(con,"person_enrolled",person_id,f"{len(samples)} supervised anchor templates; explicit consent recorded")
        engine.invalidate_gallery()
    return {"id":person_id,"name":name.strip(),"templates":len(samples),"accuracy_templates":accuracy_count}

@app.post("/api/people/{person_id}/refresh-faces")
def refresh_faces(person_id: str, auth: Auth, consent: Annotated[bool, Form()],
                  supervised: Annotated[bool, Form()], files: list[UploadFile] = File(...)):
    if not consent:
        raise HTTPException(422, "Record the person's consent before refreshing their face templates.")
    if not engine.accuracy_available():
        raise HTTPException(503, "The accuracy model is unavailable. Run setup.ps1 to install it.")
    samples, cfg = enrollment_samples(files, supervised)
    if any(sample.get('accuracy_vector') is None for sample in samples):
        raise HTTPException(503, "The accuracy model could not prepare the new templates. Try again after restarting.")
    with mutation_lock, engine.lock, db() as con:
        if not con.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
            raise HTTPException(404, "This person no longer exists.")
        index = gallery(con)
        for sample in samples:
            verified, _, _ = match(sample['vector'], index, cfg['match_threshold'], cfg['match_margin'])
            if verified != person_id:
                raise HTTPException(422, "The new photos could not be verified against this person's current enrollment.")
        con.execute("UPDATE templates SET active=0 WHERE person_id=? AND active=1", (person_id,))
        count = write_enrollment(con, person_id, samples)
        con.execute("UPDATE people SET consent_at=? WHERE id=?", (now(), person_id))
        audit(con, "person_face_templates_refreshed", person_id, f"{count} supervised dual-model anchors")
        engine.invalidate_gallery()
    return {"id":person_id,"templates":len(samples),"accuracy_templates":count}

@app.delete("/api/people/{person_id}")
def forget(person_id:str,auth:Auth):
    with mutation_lock,engine.lock,db() as con:
        if not con.execute("SELECT 1 FROM people WHERE id=?",(person_id,)).fetchone():
            raise HTTPException(404,"Person no longer exists.")
        con.execute("DELETE FROM people WHERE id=?",(person_id,))
        audit(con,"person_forgotten",person_id,"Biometric templates, evidence and attendance deleted")
        engine.invalidate_gallery()
    return {"ok":True}

def record_attendance(con,person_id,kind,score,method,note,cfg):
    last=con.execute("SELECT * FROM attendance WHERE person_id=? ORDER BY created_at DESC LIMIT 1",(person_id,)).fetchone()
    current=datetime.now(timezone.utc)
    if last:
        last_at=datetime.fromisoformat(last['created_at'])
        elapsed=(current-last_at).total_seconds()
        if last['kind']!=kind and elapsed<2:
            return {"duplicate":True,"retryable":True,"message":"The previous attendance event was just recorded. Try again in a moment."}
        if last['kind']==kind and elapsed<cfg['cooldown_seconds']:
            return {"duplicate":True,"message":"Already recorded recently. Duplicate scan ignored."}
        if last['kind']==kind and last_at.astimezone(ZoneInfo(cfg['timezone'])).date()==current.astimezone(ZoneInfo(cfg['timezone'])).date():
            return {"duplicate":True,"message":f"Already checked {kind}. Choose check-{'out' if kind=='in' else 'in'} for the next event."}
    event_id=uid()
    con.execute("INSERT INTO attendance VALUES (?,?,?,?,?,?,?)",(event_id,person_id,kind,score,method,note,now()))
    audit(con,"attendance_recorded",person_id,f"check-{kind}; {method}"+(f"; {note}" if note else ""))
    return {"duplicate":False,"id":event_id,"message":f"Checked {kind} successfully."}

class ManualEntry(BaseModel):
    person_id:str
    kind:Literal['in','out']
    note:str=Field(min_length=3,max_length=300)

@app.post("/api/attendance/manual")
def manual(body:ManualEntry,auth:Auth):
    with mutation_lock, db() as con:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM people WHERE id=?",(body.person_id,)).fetchone():
            raise HTTPException(404,"Choose an enrolled person.")
        return record_attendance(con,body.person_id,body.kind,None,'manual',body.note,settings(con))

def auto_update(con,person_id,sample,score,gap,cfg):
    learning_quality = min(sample['quality'], sample.get('learning_quality', sample['quality']))
    if not cfg['auto_learn'] or score<cfg['update_threshold'] or gap<max(.15,cfg['match_margin']) or learning_quality<.8 or (sample['liveness'] or 0)<.95:
        return False
    active=con.execute("SELECT * FROM templates WHERE person_id=? AND active=1",(person_id,)).fetchall()
    anchors=[unpack(r['vector']) for r in active if r['source']=='enrollment']
    # Adaptive templates must remain close to immutable, human-verified anchors.
    if not anchors or max(float(sample['vector']@a) for a in anchors)<cfg['update_threshold']:
        return False
    if max(float(sample['vector']@unpack(r['vector'])) for r in active)>.98:
        return False
    latest=con.execute("SELECT created_at FROM templates WHERE person_id=? AND source='auto' ORDER BY created_at DESC LIMIT 1",(person_id,)).fetchone()
    if latest and (datetime.now(timezone.utc)-datetime.fromisoformat(latest[0])).total_seconds()<3600:
        return False
    replaced=None
    if len(active)>=cfg['max_templates']:
        adaptive=[r for r in active if r['source']=='auto']
        if not adaptive:
            return False
        replaced=min(adaptive,key=lambda r:(r['quality'],r['created_at']))['id']
        con.execute("UPDATE templates SET active=0 WHERE id=?",(replaced,))
    template_id=uid()
    con.execute("INSERT INTO templates VALUES (?,?,?,?,?,1,?,?,?)",(template_id,person_id,pack(sample['vector']),sample['quality'],'auto',replaced,CIPHER.encrypt(sample['sample']),now()))
    audit(con,"template_added",template_id,f"person={person_id}; cosine={score:.3f}; live={sample['liveness']:.3f}; rollback available")
    engine.gallery_cache=None
    return True

@app.post("/api/scan")
def scan(auth:Auth,file:UploadFile=File(...),stream:Annotated[str,Form(min_length=8,max_length=80)]="default-stream",kind:Annotated[Literal['in','out'],Form()]='in'):
    started=time.perf_counter()
    with db() as con:
        cfg=settings(con)
    try:
        sample=engine.extract(file.file.read(5_000_001),cfg)
    except ValueError as exc:
        return {"state":"quality","message":str(exc),"votes":0,"needed":cfg['vote_frames']}
    base={"box":sample['box'].tolist(),"width":sample['width'],"height":sample['height'],
          "quality":sample['quality'],"liveness":sample['liveness'],"needed":cfg['vote_frames']}
    key=auth+stream+kind
    if sample['liveness'] is None or sample['liveness']<cfg['liveness_threshold']:
        with engine.lock:
            engine.consensus.tracks.pop(key,None)
        return {**base,"state":"liveness","votes":0,"message":"Liveness not verified. Adjust lighting and try again; an administrator can use manual attendance."}
    with mutation_lock,engine.lock,db() as con:
        con.execute("BEGIN IMMEDIATE")
        person_id,score,gap=match(sample['vector'],gallery(con),cfg['match_threshold'],cfg['match_margin'])
        track,stable,votes=engine.consensus.add(key,sample['box'],sample['vector'],person_id,sample['fingerprint'],cfg['vote_frames'])
        result={**base,"score":round(score,4),"margin":round(gap,4),"votes":votes,"state":"verifying","message":"Hold still while we verify your face."}
        if stable and not person_id:
            result.update(state='unknown',message='Face is not enrolled. Ask an administrator to help.')
            if cfg['retain_unknowns'] and not track.completed:
                purge_unknowns(con,cfg)
                clusters=con.execute("SELECT * FROM unknowns").fetchall()
                nearest=max(clusters,key=lambda r:float(sample['vector']@unpack(r['vector'])),default=None)
                if nearest and float(sample['vector']@unpack(nearest['vector']))>=.65:
                    con.execute("UPDATE unknowns SET sightings=sightings+1,last_seen=? WHERE id=?",(now(),nearest['id']))
                elif len(clusters)<500:
                    con.execute("INSERT INTO unknowns VALUES (?,?,?,1,?,?)",(uid(),pack(sample['vector']),CIPHER.encrypt(sample['sample']),now(),now()))
                track.completed=True
        elif stable and person_id:
            person=con.execute("SELECT name FROM people WHERE id=?",(person_id,)).fetchone()
            if not track.completed:
                attendance=record_attendance(con,person_id,kind,score,'face','',cfg)
                result.update(state='duplicate' if attendance['duplicate'] else 'recorded',message=attendance['message'])
                if not attendance['duplicate']:
                    result['learned']=auto_update(con,person_id,sample,score,gap,cfg)
                track.completed=True
            else:
                result.update(state='duplicate',message='Attendance recorded. The next person can step forward.')
            result['name']=person['name']
        result['latency_ms']=round((time.perf_counter()-started)*1000)
        return result

def review_item(con, row, summary=False):
    group = {key: row[key] for key in ('id','sample','sightings','first_seen','last_seen')}
    group['sample'] = base64.b64encode(CIPHER.decrypt(group['sample'])).decode()
    group['sample_count'] = con.execute('SELECT COUNT(*) FROM unknown_samples WHERE cluster_id=?',(row['id'],)).fetchone()[0]
    latest = con.execute('''SELECT s.camera_name FROM unknown_sightings u JOIN sightings s ON s.id=u.sighting_id
                            WHERE u.cluster_id=? ORDER BY s.created_at DESC LIMIT 1''',(row['id'],)).fetchone()
    group['camera_name'] = latest['camera_name'] if latest else None
    if not summary:
        samples = con.execute('SELECT sample,quality FROM unknown_samples WHERE cluster_id=? ORDER BY created_at LIMIT 5',(row['id'],)).fetchall()
        sightings = con.execute('''SELECT s.camera_name,s.created_at,s.liveness FROM unknown_sightings u
                                   JOIN sightings s ON s.id=u.sighting_id WHERE u.cluster_id=? ORDER BY s.created_at LIMIT 20''',(row['id'],)).fetchall()
        group.update(samples=[base64.b64encode(CIPHER.decrypt(s['sample'])).decode() for s in samples],
                     qualities=[s['quality'] for s in samples], observations=[dict(s) for s in sightings])
    return group

@app.get("/api/review")
def review(auth:Auth,page:int=1,summary:bool=False,group_id:str|None=None):
    with db() as con:
        purge_unknowns(con,settings(con))
        total=con.execute("SELECT COUNT(*) FROM unknowns").fetchone()[0]
        if group_id:
            selected=con.execute('SELECT last_seen FROM unknowns WHERE id=?',(group_id,)).fetchone()
            if selected:
                newer=con.execute('SELECT count(*) FROM unknowns WHERE (last_seen,id)>(?,?)',
                                  (selected['last_seen'],group_id)).fetchone()[0]
                page=newer//12+1
        page=max(1,min(page,max(1,(total+11)//12)))
        rows=[review_item(con, row, summary) for row in con.execute(
            'SELECT id,sample,sightings,first_seen,last_seen FROM unknowns ORDER BY last_seen DESC,id DESC LIMIT 12 OFFSET ?',((page-1)*12,))]
    return {"items":rows,"total":total,"page":page}

class ReviewAssignment(BaseModel):
    person_id:str
    verified:bool

class AttendancePreference(BaseModel):
    enabled:bool

@app.patch('/api/people/{person_id}/attendance')
def set_person_attendance(person_id:str,body:AttendancePreference,auth:Auth):
    with mutation_lock,db() as con:
        if not con.execute('SELECT 1 FROM people WHERE id=?',(person_id,)).fetchone():
            raise HTTPException(404,'Person no longer exists.')
        con.execute('INSERT INTO person_options VALUES (?,?) ON CONFLICT(person_id) DO UPDATE SET attendance_enabled=excluded.attendance_enabled',
                    (person_id,int(body.enabled)))
        audit(con,'person_attendance_changed',person_id,'Automatic attendance '+('enabled' if body.enabled else 'paused'))
    return {'enabled':body.enabled}

@app.post("/api/review/{cluster_id}/assign")
def assign(cluster_id:str,body:ReviewAssignment,auth:Auth):
    if not body.verified:
        raise HTTPException(422,"Verify this person's identity in person first.")
    with mutation_lock,engine.lock,db() as con:
        row=con.execute("SELECT * FROM unknowns WHERE id=?",(cluster_id,)).fetchone()
        person=con.execute("SELECT * FROM people WHERE id=?",(body.person_id,)).fetchone()
        if not row or not person:
            raise HTTPException(404,"Cluster or person no longer exists.")
        cfg=settings(con)
        anchors=con.execute("SELECT vector FROM templates WHERE person_id=? AND active=1 AND source='enrollment'",(body.person_id,)).fetchall()
        if not anchors or max(float(unpack(row['vector'])@unpack(r['vector'])) for r in anchors)<cfg['match_threshold']:
            raise HTTPException(422,"This face differs from the enrolled anchors. Re-enroll with fresh verified images instead.")
        count=con.execute("SELECT COUNT(*) FROM templates WHERE person_id=? AND active=1",(body.person_id,)).fetchone()[0]
        if count>=cfg['max_templates']:
            raise HTTPException(409,"Gallery is full. Review or roll back adaptive templates first.")
        tid=uid()
        con.execute("INSERT INTO templates VALUES (?,?,?,?,?,1,NULL,?,?)",(tid,body.person_id,row['vector'],.8,'review',row['sample'],now()))
        con.execute("UPDATE sightings SET person_id=?,state='named' WHERE id IN (SELECT sighting_id FROM unknown_sightings WHERE cluster_id=?)",(body.person_id,cluster_id))
        con.execute("DELETE FROM unknowns WHERE id=?",(cluster_id,))
        audit(con,"cluster_assigned",tid,f"person={body.person_id}; administrator verified; attendance not changed")
        engine.invalidate_gallery()
    return {"ok":True}

@app.delete("/api/review/{cluster_id}")
def dismiss(cluster_id:str,auth:Auth):
    with mutation_lock,db() as con:
        con.execute("DELETE FROM unknowns WHERE id=?",(cluster_id,))
        audit(con,"cluster_deleted",cluster_id,"Encrypted sample and vector removed")
    return {"ok":True}

@app.get("/api/templates/{person_id}")
def templates(person_id:str,auth:Auth):
    with db() as con:
        return [dict(r) for r in con.execute("SELECT id,source,quality,active,created_at FROM templates WHERE person_id=? ORDER BY created_at DESC",(person_id,))]

@app.post("/api/templates/{template_id}/rollback")
def rollback(template_id:str,auth:Auth):
    with mutation_lock,engine.lock,db() as con:
        r=con.execute("SELECT * FROM templates WHERE id=?",(template_id,)).fetchone()
        if not r or not r['active'] or r['source']=='enrollment':
            raise HTTPException(409,"Only an active adaptive or reviewed template can be rolled back.")
        con.execute("UPDATE templates SET active=0,evidence=NULL WHERE id=?",(template_id,))
        if r['replaced_id']:
            con.execute("UPDATE templates SET active=1 WHERE id=?",(r['replaced_id'],))
        audit(con,"template_rolled_back",template_id,"Previous adaptive template restored when available")
        engine.invalidate_gallery()
    return {"ok":True}

@app.get("/api/audit")
def audit_log(auth:Auth,page:int=1):
    with db() as con:
        total=con.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        page=max(1,min(page,max(1,(total+19)//20)))
        return {"items":[dict(r) for r in con.execute("SELECT * FROM audit ORDER BY created_at DESC LIMIT 20 OFFSET ?",((page-1)*20,))],"total":total,"page":page}

@app.get("/api/settings")
def get_settings(auth:Auth):
    with db() as con:
        row=con.execute("SELECT value FROM config WHERE key='revision'").fetchone()
        return {**settings(con),"revision":json.loads(row[0]) if row else 0}

class SettingsBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int
    organization:str=Field(min_length=1,max_length=80)
    timezone:str
    match_threshold:float=Field(ge=.4,le=.95)
    update_threshold:float=Field(ge=.7,le=.99)
    match_margin:float=Field(ge=.05,le=.4)
    liveness_threshold:float=Field(ge=.8,le=.99)
    min_face_size:int=Field(ge=60,le=400)
    min_blur:float=Field(ge=20,le=300)
    vote_frames:int=Field(ge=5,le=10)
    cooldown_seconds:int=Field(ge=30,le=3600)
    auto_learn:bool
    retain_unknowns:bool
    retention_days:int=Field(ge=1,le=30)
    max_templates:int=Field(ge=5,le=10)

@app.put("/api/settings")
def save_settings(body:SettingsBody,auth:Auth):
    cfg=body.model_dump(exclude={'revision'})
    if cfg['update_threshold']<=cfg['match_threshold']+.1:
        raise HTTPException(422,"Learning threshold must be more than 0.10 above the recognition threshold.")
    try:
        ZoneInfo(cfg['timezone'])
    except ZoneInfoNotFoundError:
        raise HTTPException(422,"Use a valid IANA timezone, for example Asia/Baghdad.") from None
    with mutation_lock,engine.lock,db() as con:
        row=con.execute("SELECT value FROM config WHERE key='revision'").fetchone()
        revision=json.loads(row[0]) if row else 0
        if body.revision!=revision:
            raise HTTPException(409,"Settings changed in another window. Reload settings and try again.")
        largest=con.execute("SELECT COUNT(*) n FROM templates WHERE active=1 GROUP BY person_id ORDER BY n DESC LIMIT 1").fetchone()
        if largest and largest[0]>cfg['max_templates']:
            raise HTTPException(422,"Roll back extra templates before lowering the gallery limit.")
        for key,value in cfg.items():
            con.execute("INSERT OR REPLACE INTO config VALUES (?,?)",(key,json.dumps(value)))
        con.execute("INSERT OR REPLACE INTO config VALUES ('revision',?)",(json.dumps(revision+1),))
        if not cfg['retain_unknowns']:
            con.execute("DELETE FROM unknowns")
        purge_unknowns(con,cfg)
        audit(con,"settings_saved","workspace","Recognition and privacy settings updated")
        engine.invalidate_gallery()
    return {**cfg,"revision":revision+1}

def safe_csv(value):
    text=str(value) if value is not None else ''
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text

@app.get("/api/export")
def export(auth:Auth,start:str,end:str):
    with db() as con:
        cfg=settings(con)
        lower,_=date_bounds(start,cfg)
        _,upper=date_bounds(end,cfg)
        if start>end or (date.fromisoformat(end)-date.fromisoformat(start)).days>366:
            raise HTTPException(422,"Choose a date range of at most one year, with start before end.")
        rows=con.execute("""SELECT p.name,p.code,p.department,a.kind,a.method,a.score,a.note,a.created_at
             FROM attendance a JOIN people p ON a.person_id=p.id WHERE a.created_at>=? AND a.created_at<?
             ORDER BY a.created_at""",(lower,upper)).fetchall()
        audit(con,"attendance_exported","workspace",f"{start} to {end}; {len(rows)} rows")
    output=io.StringIO()
    writer=csv.writer(output)
    writer.writerow(['Name','ID','Department','Event','Method','Cosine similarity','Note',f"Time ({cfg['timezone']})"])
    for row in rows:
        values=list(row)
        values[-1]=datetime.fromisoformat(values[-1]).astimezone(ZoneInfo(cfg['timezone'])).isoformat()
        writer.writerow([safe_csv(v) for v in values])
    return Response('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="presence-{start}-{end}.csv"'})

from .camera_api import router as camera_router
app.include_router(camera_router)

DIST=ROOT/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')

@app.get('/{path:path}')
def index(path:str):
    if path.startswith('api/'):
        raise HTTPException(404,'Endpoint not found.')
    if DIST.exists():
        return FileResponse(DIST/'index.html')
    return Response('Frontend not built yet. Run npm run build or open http://localhost:5173',media_type='text/plain')
