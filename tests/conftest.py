import os
import tempfile
from pathlib import Path

_test_directory=tempfile.TemporaryDirectory(prefix='presence-unit-')
os.environ['PRESENCE_DATA_DIR']=_test_directory.name

import pytest
from fastapi.testclient import TestClient
from backend.main import app, attempts
from backend.recognition import engine
from backend.store import db

@pytest.fixture(autouse=True)
def clean_database():
    from backend.monitoring import manager
    manager.shutdown()
    manager.workers.clear()
    with db() as con:
        for table in ['unknown_sightings','unknown_samples','sightings','cameras','attendance','templates','people','unknowns','audit','sessions']:
            con.execute(f'DELETE FROM {table}')
        con.execute("DELETE FROM config WHERE key NOT IN ('organization','timezone','match_threshold','update_threshold','match_margin','liveness_threshold','min_face_size','min_blur','vote_frames','cooldown_seconds','auto_learn','retain_unknowns','retention_days','max_templates')")
        from backend.store import DEFAULTS
        import json
        for key,value in DEFAULTS.items():
            con.execute('UPDATE config SET value=? WHERE key=?',(json.dumps(value),key))
    attempts.clear()
    engine.invalidate_gallery()
    yield

@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client

@pytest.fixture
def admin(client):
    assert client.post('/api/setup',json={'password':'test-password-12345'}).status_code==200
    return client
