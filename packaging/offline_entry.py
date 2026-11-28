"""Frozen classroom entry; never installs packages or downloads code."""
import os
import sys
from pathlib import Path


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    from workshop.course_server import main as serve
    args = sys.argv[1:]
    if not args:
        print('Pocket SDL — 离线课堂工作台\n1. 仿真练习（无需设备）\n2. 连接真实 Pico')
        mode = input('输入 1 或 2，按回车确认：').strip()
        if mode not in ('1', '2'):
            print('没有选择有效模式，未启动。')
            return 1
        args = ['--mode', 'simulated' if mode == '1' else 'physical']
    if '--data-dir' not in args:
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) if os.name == 'nt' else Path.home() / 'Library' / 'Application Support'
        args += ['--data-dir', str(base / 'Pocket SDL' / 'classroom-data')]
    if '--port' not in args:
        args += ['--port', '8772' if 'simulated' in args else '8770']
    return serve(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f'启动失败：{exc}', flush=True)
        raise SystemExit(1)
