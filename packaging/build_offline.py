"""Build on each target OS/architecture; ship runtime plus all local assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--label', required=True)
a = p.parse_args()
os.chdir(root)
cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console', '--name', 'PocketSDL', '--paths', str(root), '--hidden-import', 'serial.tools.list_ports', '--hidden-import', 'serial.tools.list_ports_windows', '--hidden-import', 'serial.tools.list_ports_osx']
for name in ('configs', 'docs'):
    cmd += ['--add-data', f'{root / name}{os.pathsep}{name}']
for asset in (root/'workshop').glob('*.html'):
    cmd += ['--add-data', f'{asset}{os.pathsep}workshop']
cmd += ['packaging/offline_entry.py']
subprocess.run(cmd, check=True)
bundle = root/'dist'/'PocketSDL'
for label, mode in [('Start Simulation', 'simulated'), ('Start Real Device', 'physical')]:
    if os.name == 'nt':
        (bundle/(label+'.bat')).write_bytes(('@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\nPocketSDL.exe --mode '+mode+'\r\npause\r\n').encode('utf-8'))
    else:
        f = bundle/(label+'.command')
        f.write_text('#!/bin/bash\ncd "$(dirname "$0")" || exit 1\n./PocketSDL --mode '+mode+'\nread -r -p "Press Enter to close..."\n')
        f.chmod(0o755)
shutil.copy2(root/'packaging'/'离线包使用说明.txt', bundle/'先读我.txt')
shutil.copy2(root/'LICENSE', bundle/'LICENSE')
(bundle/'BUILD.json').write_text(json.dumps({'platform':platform.platform(), 'architecture':platform.machine(), 'python':platform.python_version(), 'label':a.label}, indent=2))
# Preserve executable mode AND macOS framework symlinks. zip -y is essential on Mac.
out = root/'artifacts'
out.mkdir(exist_ok=True)
archive = out/f'Pocket-SDL-Offline-{a.label}.zip'
if archive.exists(): archive.unlink()
if sys.platform == 'darwin':
    subprocess.run(['zip', '-qry', str(archive), 'PocketSDL'], cwd=root/'dist', check=True)
else:
    shutil.make_archive(str(archive.with_suffix('')), 'zip', root/'dist', 'PocketSDL')
(archive.with_suffix('.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
print(archive)
