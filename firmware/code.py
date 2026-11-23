"""Pico H 薄执行器。复制到 CIRCUITPY 根目录；主机只连接 CDC data 端口。"""

import hashlib
import json
import time

import board
import busio
import neopixel
import usb_cdc
from adafruit_as7341 import AS7341, Gain

FIRMWARE_VERSION = "pico-h-sdl-1.0.0"
PROTOCOL_VERSION = 1
MAX_FRAME = 8192
WATCHDOG_SECONDS = 5.0
CHANNELS_NM = [415, 445, 480, 515, 555, 590, 630, 680]

pixels = neopixel.NeoPixel(board.GP28, 1, brightness=1.0, auto_write=False)
rgb = [0, 0, 0]
i2c = None  # 延后到 hello 初始化，让接线错误可经 data 端口报告。
sensor = None
fault = None
sample_seq = 0
last_valid_command = time.monotonic()
was_connected = False
buffer = bytearray()
discard_oversized = False


def led_off():
    global rgb
    rgb = [0, 0, 0]
    pixels[0] = (0, 0, 0)
    pixels.show()


def canonical_device_config(value):
    """统一递归无空格编码，不依赖 CircuitPython JSON 的默认空格。"""
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key) + ":" + canonical_device_config(value[key]) for key in sorted(value)
        ) + "}"
    if isinstance(value, list):
        return "[" + ",".join(canonical_device_config(item) for item in value) + "]"
    return json.dumps(value)


def device_info():
    config = {
        "mode": "PHYSICAL",
        "protocol_version": PROTOCOL_VERSION,
        "address": 57,
        "atime": 100,
        "astep": 999,
        "gain": "GAIN_16X",
        "channels_nm": CHANNELS_NM,
        "gpio": {"sda": 26, "scl": 27, "rgb": 28},
        "led_max_channel": 80,
        "settle_ms": 50,
    }
    canonical = canonical_device_config(config)
    digest = hashlib.new("sha256")
    digest.update(canonical.encode("utf-8"))
    fingerprint = "".join("{:02x}".format(byte) for byte in digest.digest())
    return dict(config, firmware_version=FIRMWARE_VERSION, device_fingerprint=fingerprint)


def initialize_sensor():
    global sensor, i2c
    sensor = None
    if i2c is not None:
        i2c.deinit()
        i2c = None
    i2c = busio.I2C(board.GP27, board.GP26, frequency=100000)
    while not i2c.try_lock():
        pass
    try:
        addresses = i2c.scan()
    finally:
        i2c.unlock()
    if 57 not in addresses:
        raise RuntimeError("AS7341 address 0x39 not found")
    candidate = AS7341(i2c, address=57)
    candidate.atime = 100
    candidate.astep = 999
    candidate.gain = Gain.GAIN_16X
    candidate.led = False
    sensor = candidate


def send(message):
    serial = usb_cdc.data
    if serial is not None:
        # CircuitPython json.dumps 不支持 allow_nan 参数；协议内容只含已校验类型。
        serial.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")


def stop_with_error(request_id, code, message):
    global fault
    try:
        led_off()
    except Exception as off_error:
        print("LED off failed:", off_error)
    if fault is None:
        fault = {"code": code, "message": str(message)}
    send({"v": 1, "id": request_id, "ok": False, "error": {"code": code, "message": str(message), "state": "FAULT"}})


def handle(request):
    global last_valid_command, rgb, sample_seq, fault
    if not isinstance(request, dict) or type(request.get("v")) is not int or request.get("v") != PROTOCOL_VERSION:
        raise ValueError("protocol version must be 1")
    request_id = request.get("id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("id must be a nonempty short string")
    operation = request.get("op")
    args = request.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    if operation not in ("hello", "measure", "off"):
        raise ValueError("unknown op")
    last_valid_command = time.monotonic()
    if operation == "off":
        led_off()
        send({"v": 1, "id": request_id, "ok": True, "result": {"led_off": True}})
        return
    if operation == "hello":
        if args.get("mode") != "PHYSICAL":
            raise ValueError("physical firmware only accepts PHYSICAL mode")
        led_off()
        initialize_sensor()
        fault = None
        send({"v": 1, "id": request_id, "ok": True, "result": device_info()})
        return
    if fault is not None or sensor is None:
        raise RuntimeError("healthy hello required before measurement")
    values = args.get("rgb")
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("rgb must contain three integers")
    converted = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 80:
            raise ValueError("rgb values must be integers in [0,80]")
        converted.append(value)
    rgb = converted
    pixels[0] = tuple(rgb)
    pixels.show()
    time.sleep(0.05)
    raw = list(sensor.all_channels)
    if len(raw) != 8 or any(isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 65535 for count in raw):
        raise RuntimeError("invalid AS7341 F1-F8 channel data")
    sample_seq += 1
    last_valid_command = time.monotonic()
    send({"v": 1, "id": request_id, "ok": True, "result": {"raw": raw, "sample_seq": sample_seq}})


def process_frame(line):
    global buffer
    request_id = None
    try:
        request = json.loads(line.strip().decode("utf-8"))
        if isinstance(request, dict) and isinstance(request.get("id"), str):
            request_id = request["id"]
        handle(request)
    except ValueError as error:
        stop_with_error(request_id, "PROTOCOL_ERROR", str(error))
    except Exception as error:
        stop_with_error(request_id, "SENSOR_IO", str(error))


def loop_once():
    global buffer, discard_oversized, was_connected, last_valid_command
    serial = usb_cdc.data
    if serial is None:
        led_off()
        time.sleep(0.1)
        return
    connected = serial.connected
    if not connected:
        if any(rgb):
            led_off()
        buffer = bytearray()
        discard_oversized = False
        was_connected = False
        time.sleep(0.01)
        return
    if not was_connected:
        buffer = bytearray()
        discard_oversized = False
        was_connected = True
        last_valid_command = time.monotonic()
    if any(rgb) and time.monotonic() - last_valid_command > WATCHDOG_SECONDS:
        stop_with_error(None, "WATCHDOG", "No valid command while LED was on")
    if serial.in_waiting:
        chunk = serial.read(min(serial.in_waiting, 256))
        if chunk:
            if discard_oversized:
                if b"\n" in chunk:
                    discard_oversized = False
                    chunk = chunk.split(b"\n", 1)[1]
                else:
                    chunk = b""
            buffer.extend(chunk)
            if len(buffer) > MAX_FRAME:
                buffer = bytearray()
                discard_oversized = True
                stop_with_error(None, "PROTOCOL_ERROR", "Request frame exceeds 8192 bytes")
            while b"\n" in buffer and not discard_oversized:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                if line.strip():
                    process_frame(line)
    else:
        time.sleep(0.002)


led_off()
while True:
    try:
        loop_once()
    except Exception as error:
        stop_with_error(None, "FIRMWARE_ERROR", str(error))
        time.sleep(0.05)
