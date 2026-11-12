"""Conservative multi-face association and per-track identity consensus.

Track numbers are local to a camera and session. Recognition associates camera
sightings through the enrolled gallery, never through guessed names for unknowns.
"""
from collections import deque
from dataclasses import dataclass, field
import hashlib
import time
import numpy as np
from .store import uid

UNCERTAIN_FRAME_BUDGET = 2

def iou(a,b):
    lo=np.maximum(a[:2],b[:2]); hi=np.minimum(a[:2]+a[2:],b[:2]+b[2:])
    area=float(np.prod(np.maximum(0,hi-lo)))
    return area/max(1,float(np.prod(a[2:])+np.prod(b[2:])-area))

@dataclass
class FaceTrack:
    id:str
    box:np.ndarray
    touched:float
    vector:np.ndarray|None=None
    votes:deque=field(default_factory=lambda:deque(maxlen=10 + UNCERTAIN_FRAME_BUDGET))
    hashes:deque=field(default_factory=lambda:deque(maxlen=30))
    last_voted:float=0
    quality_misses:int=0
    unknown_sighting_id:str|None=None
    unknown_cluster:str|None=None
    review_dismissed:bool=False
    review_retry_at:float=0.
    emitted:set=field(default_factory=set)
    line_side:int=0
    line_identity:str|None=None
    crossing:int=0
    crossing_at:float=0
    crossing_emitted:set=field(default_factory=set)
    attendance_feedback:dict=field(default_factory=dict)

    def reset_crossing(self):
        self.line_side = 0
        self.line_identity = None
        self.crossing = 0
        self.crossing_at = 0
        self.crossing_emitted.clear()
        self.attendance_feedback.clear()

class MultiFaceTracker:
    def __init__(self):
        self.tracks={}

    def update(self,samples,stamp=None):
        stamp=time.monotonic() if stamp is None else stamp
        self.tracks={k:t for k,t in self.tracks.items() if stamp-t.touched<2.5}
        candidates=[]
        for i,s in enumerate(samples):
            for key,t in self.tracks.items():
                overlap=iou(s['box'],t.box)
                similarity=float(s['vector']@t.vector) if s.get('eligible') and t.vector is not None else None
                # Appearance mismatch forbids association even when boxes overlap.
                if similarity is not None and similarity<.45:
                    continue
                center_a=s['box'][:2]+s['box'][2:]/2
                center_b=t.box[:2]+t.box[2:]/2
                displacement=float(np.linalg.norm(center_a-center_b))/max(1,float(np.linalg.norm(t.box[2:])))
                if overlap>.05 or (similarity is not None and similarity>.8 and displacement<1.5):
                    candidates.append((overlap*.6+(similarity or 0)*.4,i,key))
        # Ambiguous associations lose temporal history instead of inheriting a name.
        ambiguous=set()
        for i in range(len(samples)):
            options=sorted((c[0] for c in candidates if c[1]==i),reverse=True)
            if len(options)>1 and options[0]-options[1]<.06:
                ambiguous.add(i)
        used_samples=set();used_tracks=set();pairs=[]
        for score,i,key in sorted(candidates,reverse=True):
            if i in ambiguous or i in used_samples or key in used_tracks:
                continue
            used_samples.add(i);used_tracks.add(key)
            pairs.append((self.tracks[key],samples[i]))
        for i,s in enumerate(samples):
            if i not in used_samples:
                t=FaceTrack(uid(),s['box'].copy(),stamp)
                self.tracks[t.id]=t
                pairs.append((t,s))
        for t,s in pairs:
            t.box=s['box'].copy();t.touched=stamp
            if s.get('eligible'):
                t.vector=s['vector'].copy()
                t.quality_misses=0
            else:
                t.quality_misses+=1
                if t.quality_misses>UNCERTAIN_FRAME_BUDGET:
                    t.votes.clear()
                    t.reset_crossing()
        return pairs

def line_crossing(track,camera,width,height,stamp=None,identity=None):
    stamp=time.monotonic() if stamp is None else stamp
    if identity is not None:
        if track.line_identity is not None and identity != track.line_identity:
            track.reset_crossing()
        track.line_identity = identity
    axis=0 if camera['line_axis']=='x' else 1
    center=(track.box[axis]+track.box[axis+2]/2)/(width if axis==0 else height)
    delta=center-camera['line_position']
    side=1 if delta>.03 else -1 if delta<-.03 else 0
    if side and track.line_side and side!=track.line_side:
        track.crossing=1 if side>track.line_side else -1
        track.crossing_at=stamp
        track.crossing_emitted.clear()
        track.attendance_feedback.clear()
    if side:track.line_side=side
    return track.crossing if stamp-track.crossing_at<3 else 0

def add_vote(track,sample,person_id,score,margin,needed,stamp=None):
    stamp=time.monotonic() if stamp is None else stamp
    # Exact repeats cannot manufacture a majority from a frozen feed.
    fingerprint=sample.get('fingerprint') or hashlib.sha256(sample['sample']).hexdigest()
    if person_id is not None:
        previous = next((v['person_id'] for v in reversed(track.votes) if v['person_id'] is not None), None)
        if previous is not None and previous != person_id:
            track.votes.clear()
    if fingerprint not in track.hashes and stamp-track.last_voted>=.15:
        track.hashes.append(fingerprint)
        track.votes.append({'person_id':person_id,'score':score,'margin':margin,
                            'live':sample.get('liveness') or 0.,'quality':sample['quality'],'sample':sample})
        track.last_voted=stamp
    supporting = []
    window = []
    uncertain = 0
    for vote in reversed(track.votes):
        if vote['person_id'] == person_id:
            supporting.append(vote)
        elif person_id is not None and vote['person_id'] is None and uncertain < UNCERTAIN_FRAME_BUDGET:
            uncertain += 1
        else:
            break
        window.append(vote)
        if len(supporting) == needed:
            break
    # Uncertain frames never count for identity. A weak liveness result anywhere
    # between the supporting frames still blocks automatic attendance.
    supporting.reverse()
    live = min((v['live'] for v in window), default=0.)
    return len(supporting) == needed, supporting, min(live, sample.get('liveness') or 0.)
