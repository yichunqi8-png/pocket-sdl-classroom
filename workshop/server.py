"""Pico H + AS7341 + WS2812B Workshop 本地实时 Web 控制台服务端。

仅使用 Python 标准库 + 仓库既有依赖（numpy / pyserial），无需额外安装 Web 框架。

示例：
  # 仿真彩排模式（无需硬件）
  python workshop/server.py --mode simulated --port 8765

  # 真机现场模式（连接 Pico H，允许同局域网手机扫码）
  python workshop/server.py --mode physical --serial /dev/cu.usbmodemXXXX --host 0.0.0.0 --port 8765
"""

import argparse
import json
import queue
import socket
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pocket_sdl import LabError
from workshop.engine import WorkshopEngine
from workshop.qr import render_svg

WORKSHOP_DIR = Path(__file__).resolve().parent


def detect_lan_ip():
    """尽最大努力获取局域网 IPv4 地址，用于生成手机扫码 URL。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        sock.close()
    return "127.0.0.1"


def list_serial_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    rows = []
    for item in sorted(list_ports.comports(), key=lambda p: p.device):
        rows.append(
            {
                "device": item.device,
                "description": item.description,
                "hwid": item.hwid,
                "serial_number": item.serial_number,
            }
        )
    return rows


def make_handler(engine, mobile_url):
    ui_html = (WORKSHOP_DIR / "ui.html").read_bytes()
    mobile_html = (WORKSHOP_DIR / "mobile.html").read_bytes()

    class WorkshopHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            # 保持控制台安静整洁；仅在需要排查时可扩展
            return

        def _send_bytes(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._send_bytes(status, data, "application/json; charset=utf-8")

        def _read_json(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            if length > 65536:
                raise LabError("PROTOCOL_ERROR", "请求体过大")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise LabError("INVALID_INPUT", f"JSON 解析失败：{error}") from error
            if not isinstance(data, dict):
                raise LabError("INVALID_INPUT", "请求体必须是 JSON 对象")
            return data

        def _current_mobile_url(self):
            host_header = self.headers.get("Host")
            if host_header and not host_header.startswith(("127.0.0.1", "localhost")):
                return f"http://{host_header}/mobile"
            return mobile_url

        def _serve_sse(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            sub = engine.subscribe()
            try:
                # 首次连接立即推送完整状态
                init_payload = json.dumps(
                    {"event": "state", "data": engine.get_state()},
                    ensure_ascii=False,
                    allow_nan=False,
                )
                self.wfile.write(f"event: state\ndata: {init_payload}\n\n".encode("utf-8"))
                self.wfile.flush()

                while not engine._stop_all.is_set():
                    try:
                        item = sub.get(timeout=15.0)
                        if item is None:
                            break
                        body = json.dumps(item, ensure_ascii=False, allow_nan=False)
                        self.wfile.write(f"event: {item['event']}\ndata: {body}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                engine.unsubscribe(sub)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                self._send_bytes(HTTPStatus.OK, ui_html, "text/html; charset=utf-8")
                return
            if path in ("/mobile", "/mobile.html"):
                self._send_bytes(HTTPStatus.OK, mobile_html, "text/html; charset=utf-8")
                return
            if path == "/api/events":
                self._serve_sse()
                return
            if path == "/api/state":
                state = engine.get_state()
                state["mobile_url"] = self._current_mobile_url()
                self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
                return
            if path == "/api/ports":
                self._send_json(HTTPStatus.OK, {"ok": True, "ports": list_serial_ports()})
                return
            if path == "/api/qr.svg":
                query = parse_qs(parsed.query)
                target_url = query.get("url", [self._current_mobile_url()])[0]
                svg = render_svg(target_url).encode("utf-8")
                self._send_bytes(HTTPStatus.OK, svg, "image/svg+xml; charset=utf-8")
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": {"code": "NOT_FOUND", "message": f"未知路径：{path}"}})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self._read_json()
                if path == "/api/led/off":
                    result = engine.led_off()
                elif path == "/api/device/reconnect":
                    result = {"device_info": engine.connect()}
                elif path == "/api/calibrate":
                    result = {"calibration": engine.calibrate()}
                elif path == "/api/live/measure":
                    result = {"sample": engine.live_measure_once(body.get("x", [0.0, 0.0, 0.0]))}
                elif path == "/api/live/stream":
                    result = {
                        "state": engine.set_live_stream(
                            bool(body.get("enabled", False)),
                            body.get("x"),
                        )
                    }
                elif path == "/api/arena/target":
                    result = {
                        "arena": engine.arena_new_target(
                            kind=body.get("kind", "mystery"),
                            x=body.get("x"),
                            hide_target=body.get("hide_target", True),
                        )
                    }
                elif path == "/api/arena/reveal":
                    result = {"arena": engine.arena_reveal()}
                elif path == "/api/arena/human/start":
                    result = {
                        "arena": engine.arena_human_start(
                            player_name=body.get("player", "现场挑战者"),
                            budget=body.get("budget", 10),
                        )
                    }
                elif path == "/api/arena/human/measure":
                    result = engine.arena_human_measure(body.get("x", [0.0, 0.0, 0.0]))
                elif path == "/api/arena/ai/start":
                    result = engine.arena_start_ai(
                        strategy=body.get("strategy", "bo"),
                        budget=body.get("budget", 24),
                    )
                elif path == "/api/arena/ai/stop":
                    result = engine.stop_active_job()
                elif path == "/api/queue/submit":
                    result = {
                        "task": engine.queue_submit(
                            author=body.get("author", "匿名观众"),
                            color_name=body.get("color_name", "我的颜色"),
                            x=body.get("x", [0.3, 0.6, 0.4]),
                        )
                    }
                elif path == "/api/queue/run_next":
                    result = {"task": engine.queue_run_next(budget=body.get("budget", 16))}
                elif path == "/api/queue/auto":
                    result = engine.queue_set_auto(bool(body.get("enabled", False)))
                else:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"ok": False, "error": {"code": "NOT_FOUND", "message": f"未知接口：{path}"}},
                    )
                    return
                self._send_json(HTTPStatus.OK, {"ok": True, **result})
            except LabError as error:
                status = HTTPStatus.CONFLICT if error.code in ("BUSY", "BUDGET_EXHAUSTED", "QUEUE_EMPTY") else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": {"code": error.code, "message": error.message}})
            except Exception as error:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": {"code": type(error).__name__, "message": str(error)}},
                )

    return WorkshopHandler


def create_server(engine, host="127.0.0.1", port=8765):
    lan_ip = detect_lan_ip() if host in ("0.0.0.0", "") else host
    mobile_url = f"http://{lan_ip}:{port}/mobile"
    handler = make_handler(engine, mobile_url)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server, mobile_url


def build_parser():
    parser = argparse.ArgumentParser(description="Pico H 光谱自主实验 · Workshop 本地实时交互台")
    parser.add_argument("--mode", choices=("simulated", "physical"), default="simulated", help="运行模式：simulated（彩排仿真）或 physical（真机 Pico H）")
    parser.add_argument("--serial", help="真机模式串口（例如 /dev/cu.usbmodemXXXX 或 COM4）")
    parser.add_argument("--config", help="可选：自定义 JSON 配置文件路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，局域网扫码请传 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 监听端口（默认 8765）")
    parser.add_argument("--sim-delay", type=float, default=0.28, help="仿真模式下 AI 每步演示节拍（秒，默认 0.28）")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="启动后自动打开浏览器")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mode == "physical" and not args.serial:
        ports = list_serial_ports()
        print("错误：physical 模式必须通过 --serial 指定 Pico CDC data 串口。", file=sys.stderr)
        if ports:
            print("当前可用串口：", file=sys.stderr)
            for item in ports:
                print(f"  - {item['device']}  ({item['description']})", file=sys.stderr)
        return 2

    engine = WorkshopEngine(
        mode=args.mode.upper(),
        serial_port=args.serial,
        config_path=args.config,
        sim_delay_s=args.sim_delay,
    )
    server, mobile_url = create_server(engine, host=args.host, port=args.port)
    local_url = f"http://127.0.0.1:{args.port}/"
    print("=" * 64)
    print(f"  SDL Workshop 实时交互台已启动  [{engine.mode}]")
    print(f"  大屏主控台 : {local_url}")
    if args.host == "0.0.0.0":
        lan_ip = detect_lan_ip()
        print(f"  局域网主屏 : http://{lan_ip}:{args.port}/")
        print(f"  观众扫码页 : {mobile_url}")
    else:
        print(f"  观众手机页 : {mobile_url}  （如需同 Wi-Fi 扫码，请加 --host 0.0.0.0）")
    if engine.serial_port:
        print(f"  硬件串口   : {engine.serial_port}")
    print("  按 Ctrl-C 安全关灯并退出。")
    print("=" * 64, flush=True)

    if args.open_browser:
        webbrowser.open(local_url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务并熄灭 LED...", flush=True)
    finally:
        server.server_close()
        engine.shutdown()
        print("已安全关灯并释放设备。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
