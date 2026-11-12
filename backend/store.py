"""Local transactional storage. Biometric blobs are encrypted before persistence."""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("PRESENCE_DATA_DIR", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)
KEY_FILE = DATA / "biometric.key"
if not KEY_FILE.exists():
    # Exclusive create prevents two workers generating incompatible keys.
    try:
        with KEY_FILE.open("xb") as f:
            f.write(Fernet.generate_key())
    except FileExistsError:
        pass
CIPHER = Fernet(KEY_FILE.read_bytes())

DEFAULTS = {
    "organization": "My organization", "timezone": "Asia/Baghdad",
    "match_threshold": 0.50, "update_threshold": 0.80, "match_margin": 0.10,
    "liveness_threshold": 0.90, "min_face_size": 90, "min_blur": 45.0,
    "vote_frames": 5, "cooldown_seconds": 300, "auto_learn": False,
    "retain_unknowns": False, "retention_days": 7, "max_templates": 10,
}

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return secrets.token_hex(12)

@contextmanager
def db():
    con = sqlite3.connect(DATA / "presence.sqlite3", timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()

def journal_mode_for(version):
    # SQLite WAL-reset fix and the two upstream maintenance backports.
    fixed = (version >= (3, 51, 3) or
             (3, 50, 7) <= version < (3, 51, 0) or
             (3, 44, 6) <= version < (3, 45, 0))
    return "WAL" if fixed else "DELETE"

def init_db():
    with db() as con:
        mode = journal_mode_for(sqlite3.sqlite_version_info)
        actual = con.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]
        if actual.upper() != mode:
            raise RuntimeError("Close other AttendAI processes so the database can switch journal mode.")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS people (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE,
          department TEXT NOT NULL, consent_at TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS person_options (
          person_id TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
          attendance_enabled INTEGER NOT NULL CHECK(attendance_enabled IN (0,1)));
        CREATE TABLE IF NOT EXISTS templates (
          id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
          vector BLOB NOT NULL, quality REAL NOT NULL, source TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1, replaced_id TEXT, evidence BLOB, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS template_person ON templates(person_id,active);
        CREATE TABLE IF NOT EXISTS accuracy_templates (
          template_id TEXT NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
          model TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(template_id,model));
        CREATE TABLE IF NOT EXISTS attendance (
          id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK(kind IN ('in','out')), score REAL,
          method TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS unknowns (
          id TEXT PRIMARY KEY, vector BLOB NOT NULL, sample BLOB NOT NULL,
          sightings INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS unknown_samples (
          id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL REFERENCES unknowns(id) ON DELETE CASCADE,
          vector BLOB NOT NULL, sample BLOB NOT NULL, quality REAL NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS unknown_accuracy_images (
          sample_id TEXT PRIMARY KEY REFERENCES unknown_samples(id) ON DELETE CASCADE,
          aligned BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS unknown_verified_merges (
          cluster_id TEXT PRIMARY KEY REFERENCES unknowns(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS audit (
          id TEXT PRIMARY KEY, action TEXT NOT NULL, subject TEXT NOT NULL,
          detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS cameras (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL,
          source BLOB NOT NULL, source_type TEXT NOT NULL, source_hint TEXT NOT NULL,
          role TEXT NOT NULL, trigger_mode TEXT NOT NULL,
          line_axis TEXT NOT NULL, line_position REAL NOT NULL, direction INTEGER NOT NULL,
          analysis_fps INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
          consent_at TEXT NOT NULL, created_at TEXT NOT NULL,
          distance_mode INTEGER NOT NULL DEFAULT 0,
          operation_mode TEXT NOT NULL DEFAULT 'live');
        CREATE TABLE IF NOT EXISTS sightings (
          id TEXT PRIMARY KEY, camera_id TEXT REFERENCES cameras(id) ON DELETE SET NULL,
          camera_name TEXT NOT NULL, person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
          track_id TEXT NOT NULL, state TEXT NOT NULL, score REAL, liveness REAL,
          attendance_kind TEXT, learned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS sightings_time ON sightings(created_at);
        CREATE INDEX IF NOT EXISTS sightings_person_camera ON sightings(person_id,camera_id,created_at);
        CREATE TABLE IF NOT EXISTS unknown_sightings (
          sighting_id TEXT PRIMARY KEY REFERENCES sightings(id) ON DELETE CASCADE,
          cluster_id TEXT NOT NULL REFERENCES unknowns(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS unknown_sightings_cluster ON unknown_sightings(cluster_id);
        """)
        camera_columns = {row['name'] for row in con.execute('PRAGMA table_info(cameras)')}
        if 'distance_mode' not in camera_columns:
            con.execute('ALTER TABLE cameras ADD COLUMN distance_mode INTEGER NOT NULL DEFAULT 0')
        if 'operation_mode' not in camera_columns:
            con.execute("ALTER TABLE cameras ADD COLUMN operation_mode TEXT NOT NULL DEFAULT 'live'")
        for key, value in DEFAULTS.items():
            con.execute("INSERT OR IGNORE INTO config VALUES (?,?)", (key, json.dumps(value)))

def settings(con):
    return {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT * FROM config") if r["key"] in DEFAULTS}

def audit(con, action, subject, detail=""):
    con.execute("INSERT INTO audit VALUES (?,?,?,?,?)", (uid(), action, subject, detail, now()))

def pack(vector):
    return CIPHER.encrypt(np.asarray(vector, dtype=np.float32).tobytes())

def unpack(blob):
    return np.frombuffer(CIPHER.decrypt(blob), dtype=np.float32).copy()

def purge_unknowns(con, cfg):
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cfg["retention_days"])).isoformat()
    # Dashboard and review polls should remain readers when there is no work.
    if con.execute("SELECT 1 FROM unknowns WHERE last_seen < ? LIMIT 1", (cutoff,)).fetchone():
        con.execute("DELETE FROM unknowns WHERE last_seen < ?", (cutoff,))

init_db()
