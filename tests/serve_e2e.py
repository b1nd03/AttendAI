"""Isolated backend for UI tests; never points tests at the real attendance DB."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
directory=tempfile.TemporaryDirectory(prefix='presence-e2e-')
os.environ['PRESENCE_DATA_DIR']=directory.name
import uvicorn
if __name__=='__main__':
    uvicorn.run('backend.main:app',host='127.0.0.1',port=8001,log_level='warning')
