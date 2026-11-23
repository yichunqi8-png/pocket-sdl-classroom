"""Student bootstrap: only standard library until the local environment is ready."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info < (3, 10):
        raise RuntimeError('需要 Python 3.10 或更新版本；推荐课程统一使用 Python 3.12。')
    env = ROOT / '.student-venv'
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('首次启动：创建课程独立 Python 环境……', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(env)], check=True)
    check = subprocess.run([str(python), '-c', 'import numpy, serial'], capture_output=True)
    if check.returncode:
        print('首次安装依赖，需要网络。课堂前请先完成此步骤。', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    return subprocess.call([str(python), '-u', '-m', 'workshop.course_server', *sys.argv[1:]], cwd=ROOT)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'启动失败：{error}\n请保留此窗口，交给助教检查。', flush=True)
        raise SystemExit(1)
