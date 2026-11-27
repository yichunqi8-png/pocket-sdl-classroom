"""Exercise the frozen binary outside its source tree, with no Python on PATH."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import sys
import time
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='Pocket SDL 离线 ') as tmp:
    work = Path(tmp)
    app = work/'app'
    shutil.copytree(root/'dist'/'PocketSDL', app, symlinks=True)
    binary = app/('PocketSDL.exe' if os.name == 'nt' else 'PocketSDL')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env = dict(os.environ, PYTHONPATH='', PYTHONHOME='', PATH='', HTTP_PROXY='http://127.0.0.1:1', HTTPS_PROXY='http://127.0.0.1:1', NO_PROXY='127.0.0.1,localhost')
    log = open(work/'server.log', 'w', encoding='utf-8')
    proc = subprocess.Popen([str(binary), '--mode', 'simulated', '--no-open', '--port', str(port), '--data-dir', str(work/'data')], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
    def request(path, body=None):
        req = Request(f'http://127.0.0.1:{port}'+path, data=None if body is None else json.dumps(body).encode(), headers={'Content-Type':'application/json'})
        with urlopen(req, timeout=10) as response: return response.read()
    try:
        for _ in range(80):
            if proc.poll() is not None: raise RuntimeError('frozen app exited')
            try:
                health=json.loads(request('/launcher-health'));break
            except OSError: time.sleep(.25)
        else: raise RuntimeError('startup timed out')
        assert health['mode']=='SIMULATED'
        for path in ['/', '/console', '/wiring/overview', '/wiring/led', '/wiring/sensor']:
            assert len(request(path))>100
        for rgb in [[1,0,0],[0,1,0],[0,0,1]]:
            result=json.loads(request('/api/live/measure', {'x':rgb}))
            assert result['ok'], result
        assert json.loads(request('/api/led/off', {}))['ok']
        assert list((work/'data').glob('*.json*'))
        print('PASS: relocated frozen binary, empty PATH, Chinese/spaced path, HTML/wiring assets, RGB simulation and saved records')
    finally:
        proc.terminate()
        try: proc.wait(timeout=15)
        except subprocess.TimeoutExpired: proc.kill();proc.wait()
        log.close()
        print((work/'server.log').read_text(encoding='utf-8', errors='replace'))
