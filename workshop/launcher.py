"""Physical-only student launcher. Never flash firmware or fall back to simulation."""
import argparse
import json
import os
import sys
import time
import webbrowser
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from serial.tools import list_ports
from pocket_sdl import LabError
from pocket_sdl.config import load_config
from pocket_sdl.core import validate_raw
from pocket_sdl.device import _port_sort_key
from pocket_sdl.transport import SerialDevice
from workshop.engine import WorkshopEngine, REPO_ROOT
from workshop.server import make_handler

MARKER = 'pocket-sdl-student-launcher-v1'


def _safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        stream = kwargs.get('file') or sys.stdout
        text = kwargs.get('sep', ' ').join(str(a) for a in args) + kwargs.get('end', '\n')
        enc = getattr(stream, 'encoding', None) or 'utf-8'
        stream.write(text.encode(enc, errors='replace').decode(enc))
        if kwargs.get('flush'):
            stream.flush()


def pico_ports():
    # Restrict probes to known Pico/CircuitPython VID or Pico descriptions.
    return [p for p in list_ports.comports() if p.vid in (0x2E8A, 0x239A)
            or any(t in (p.description or '').lower() for t in ('pico', 'rp2040'))]


def select_board(ports, serial_number=None, ask=input):
    groups = {}
    for port in ports:
        # No serial number: keep separate, never assume two ports belong to one board.
        key = port.serial_number or port.device
        groups.setdefault(key, []).append(port)
    if serial_number:
        if serial_number not in groups:
            raise LabError('NO_DEVICE', f'未找到指定板子 {serial_number}')
        return groups[serial_number]
    if not groups:
        raise LabError('NO_DEVICE', '没有发现 Pico 串口。检查数据线；如出现 RPI-RP2，请普通拔插退出 BOOTSEL；固件由助教配置。')
    if len(groups) == 1:
        return next(iter(groups.values()))
    keys = sorted(groups)
    _safe_print('发现多个设备，请选择本组设备：')
    for i, key in enumerate(keys, 1):
        _safe_print(f'  {i}. 序列号 {key}：' + ', '.join(p.device for p in groups[key]))
    while True:
        answer = ask('输入编号（q 退出）：').strip()
        if answer.lower() == 'q':
            raise LabError('CANCELLED', '已取消连接')
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return groups[keys[int(answer) - 1]]
        _safe_print('请输入列表中的编号。')


def probe(ports, config):
    errors = []
    for port in sorted(ports, key=_port_sort_key):
        dev = None
        try:
            dev = SerialDevice(port.device, config, hello_timeout=2.0)
            raw = validate_raw(dev.measure((0, 0, 0)), config['sensor']['saturation_raw_exclusive'])
            _safe_print(f'自检通过：{port.device}\n真实暗场 F1–F8：{raw.tolist()}')
            return port.device
        except LabError as error:
            errors.append(f'{port.device}: {error.code}: {error.message}')
        finally:
            if dev is not None:
                dev.close()
                if os.name == 'nt':
                    time.sleep(0.15)
    raise LabError('DEVICE_CHECK_FAILED', '\n'.join(errors) + '\n检查串口占用、固件和 AS7341 接线；修改接线前拔掉 USB。')


def existing(url, serial_number=None):
    try:
        with urlopen(url + 'launcher-health', timeout=2) as response:
            data = json.load(response)
        return (data.get('launcher') == MARKER and data.get('mode') == 'PHYSICAL'
                and (not serial_number or data.get('serial_number') == serial_number))
    except (OSError, ValueError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pocket SDL 学生真机启动器')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--serial-number', help='多块板时按 USB 序列号指定')
    parser.add_argument('--check-only', action='store_true', help='只检查设备，不启动网页')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('端口必须为 1–65535')
    url = f'http://127.0.0.1:{args.port}/'
    server = None
    engine = None
    try:
        # Reserve port before probing hardware; repeated clicks cannot interrupt the owner.
        if not args.check_only:
            try:
                server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(None, url + 'mobile'))
                server.daemon_threads = True
            except OSError:
                if existing(url, args.serial_number):
                    _safe_print('真机操作台已在运行，打开已有页面。')
                    if not args.no_open:
                        webbrowser.open(url)
                    return 0
                raise LabError('PORT_BUSY', f'端口 {args.port} 被其他程序或旧版操作台占用。请先关闭旧服务，或请助教指定另一端口。')
        config = load_config(REPO_ROOT / 'configs' / 'physical.json')
        ports = select_board(pico_ports(), args.serial_number)
        serial_port = probe(ports, config)
        if args.check_only:
            return 0
        engine = WorkshopEngine(mode='PHYSICAL', serial_port=serial_port)
        base = make_handler(engine, url + 'mobile')
        board_serial = next(p.serial_number for p in ports if p.device == serial_port)

        class Handler(base):
            def do_GET(self):
                if self.path == '/launcher-health':
                    self._send_json(200, {'launcher': MARKER, 'mode': engine.mode,
                                         'serial_number': board_serial})
                else:
                    super().do_GET()

        server.RequestHandlerClass = Handler
        _safe_print(f'\nPocket SDL 真机操作台 [PHYSICAL]\n{url}\n保持此窗口打开。退出请按 Ctrl+C。\n自检仅验证一次暗场；实验前仍需固定遮光装置并校准。', flush=True)
        if not args.no_open:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        _safe_print('\n正在关灯并释放设备……')
    except (LabError, OSError, EOFError) as error:
        _safe_print(f'\n启动失败：{error}\n没有切换到仿真。请保留此窗口给助教检查。', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.shutdown()
        if server is not None:
            server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
