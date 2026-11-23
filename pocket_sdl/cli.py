"""简洁的同步命令行。"""

import argparse
import json
import sys

from . import LabError, __version__
from .config import load_config
from .workflow import confirm, doctor, prepare, run


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def list_ports():
    try:
        from serial.tools import list_ports as serial_ports
    except ImportError as error:
        raise LabError("SERIAL_IO", "请先安装 pyserial") from error
    rows = []
    for port in sorted(serial_ports.comports(), key=lambda item: item.device):
        rows.append({"device": port.device, "description": port.description, "hwid": port.hwid, "vid": port.vid, "pid": port.pid, "serial_number": port.serial_number})
    print_json(rows)
    if not rows:
        print("未发现串口。请连接 Pico、等待复位完成并检查 USB 数据线。", file=sys.stderr)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m pocket_sdl", description="Pocket SDL: Pico H + AS7341 + WS2812B 自主光谱闭环")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-ports", help="列出串口，不自动猜测或访问设备")
    for name, help_text in (("doctor", "真机接口与单次暗场自检"), ("prepare", "建立独立暗场、参考、目标和稳定性校准")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--config", required=True)
        item.add_argument("--port")
        if name == "prepare":
            item.add_argument("--out", required=True)
    item = sub.add_parser("run", help="使用已有校准运行 random 或 bo")
    item.add_argument("--calibration", required=True)
    item.add_argument("--strategy", choices=("random", "bo"), required=True)
    item.add_argument("--out", required=True)
    item.add_argument("--port")
    item = sub.add_parser("confirm", help="独立测量最佳点三次，不回写训练集")
    item.add_argument("--run", required=True, dest="run_dir")
    item.add_argument("--out", required=True)
    item.add_argument("--port")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list-ports":
            return list_ports()
        if args.command == "doctor":
            print_json(doctor(args.config, args.port))
        elif args.command == "prepare":
            print_json(prepare(args.config, args.out, args.port))
        elif args.command == "run":
            print_json(run(args.calibration, args.strategy, args.out, args.port))
        elif args.command == "confirm":
            print_json(confirm(args.run_dir, args.out, args.port))
        return 0
    except LabError as error:
        print(f"错误 [{error.code}]：{error.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("用户中止；已保留故障目录，恢复时请新建输出目录。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
