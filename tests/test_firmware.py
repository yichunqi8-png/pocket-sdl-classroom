"""通过 API 桩运行 firmware/code.py 的 JSON 帧；不代表实机验证。"""

import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pocket_sdl.config import expected_device_fingerprint, load_config


class NeoPixel:
    def __init__(self, *args, **kwargs):
        self.color = (0, 0, 0)

    def __setitem__(self, index, color):
        self.color = color

    def show(self):
        pass


class I2C:
    def __init__(self, *args, **kwargs):
        pass

    def try_lock(self):
        return True

    def scan(self):
        return [57]

    def unlock(self):
        pass

    def deinit(self):
        self.deinitialized = True


class Data:
    def __init__(self):
        self.input = io.BytesIO()
        self.output = io.BytesIO()
        self.connected = True

    @property
    def in_waiting(self):
        pos = self.input.tell()
        return len(self.input.getbuffer()) - pos

    def read(self, count):
        return self.input.read(count)

    def write(self, data):
        return self.output.write(data)

    def feed(self, frame):
        self.input = io.BytesIO(frame)
        return frame


class Sensor:
    def __init__(self, *args, **kwargs):
        self.all_channels = (10, 20, 30, 40, 50, 60, 70, 80)


def firmware_module():
    # 测试在受控桩上运行项目自身固件，非任何下载脚本。
    root = Path(__file__).resolve().parents[1]
    old_modules = {name: sys.modules.get(name) for name in ("board", "busio", "neopixel", "usb_cdc", "adafruit_as7341")}
    board = types.ModuleType("board")
    board.GP26, board.GP27, board.GP28 = 26, 27, 28
    busio = types.ModuleType("busio")
    busio.I2C = I2C
    pixel = types.ModuleType("neopixel")
    pixel.NeoPixel = NeoPixel
    usb = types.ModuleType("usb_cdc")
    usb.data = Data()
    sensor = types.ModuleType("adafruit_as7341")
    sensor.AS7341 = Sensor
    sensor.Gain = types.SimpleNamespace(GAIN_16X="GAIN_16X")
    try:
        sys.modules.update(board=board, busio=busio, neopixel=pixel, usb_cdc=usb, adafruit_as7341=sensor)
        spec = importlib.util.spec_from_file_location("sdl_test_firmware", root / "firmware" / "code.py")
        module = importlib.util.module_from_spec(spec)
        with patch("time.sleep", side_effect=SystemExit):
            try:
                spec.loader.exec_module(module)
            except SystemExit:
                pass
        return module, usb.data
    finally:
        for name, value in old_modules.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class FirmwareProtocolTests(unittest.TestCase):
    def setUp(self):
        self.module, self.data = firmware_module()
        self.module.handle({"v": 1, "id": "hello", "op": "hello", "args": {"mode": "PHYSICAL"}})
        self.data.output.seek(0)
        self.data.output.truncate()

    def tearDown(self):
        sys.modules.pop("sdl_test_firmware", None)

    def test_split_and_joined_frames_disconnect(self):
        first = json.dumps({"v": 1, "id": "a", "op": "measure", "args": {"rgb": [24, 48, 32]}}).encode()
        second = json.dumps({"v": 1, "id": "b", "op": "off", "args": {}}).encode()
        frames = [first[:15], first[15:] + b"\n" + second + b"\n"]
        for frame in frames:
            self.data.feed(frame)
            self.module.loop_once()
        responses = [json.loads(line) for line in self.data.output.getvalue().splitlines() if line]
        self.assertEqual([r["id"] for r in responses], ["a", "b"])
        self.assertEqual(responses[0]["result"]["raw"], [10, 20, 30, 40, 50, 60, 70, 80])
        self.module.rgb = [1, 1, 1]
        self.data.connected = False
        self.module.loop_once()
        self.assertEqual(self.module.rgb, [0, 0, 0])

    def test_recovery_releases_old_i2c(self):
        previous = self.module.i2c
        self.module.stop_with_error("fault", "SENSOR_IO", "injected")
        self.module.handle({"v": 1, "id": "recovered", "op": "hello", "args": {"mode": "PHYSICAL"}})
        self.assertTrue(previous.deinitialized)
        self.assertIsNone(self.module.fault)

    def test_hello_fingerprint_and_measure(self):
        self.assertEqual(self.module.device_info()["device_fingerprint"], expected_device_fingerprint(load_config("configs/physical.json")))
        self.module.handle({"v": 1, "id": "measure", "op": "measure", "args": {"rgb": [24, 48, 32]}})
        response = json.loads(self.data.output.getvalue())
        self.assertEqual(response["result"]["raw"], [10, 20, 30, 40, 50, 60, 70, 80])
        self.assertEqual(self.module.pixels.color, (24, 48, 32))
        self.module.handle({"v": 1, "id": "off", "op": "off", "args": {}})
        self.assertEqual(self.module.pixels.color, (0, 0, 0))

    def test_bad_values_off_and_do_not_measure(self):
        self.module.sensor.all_channels = (0,) * 8
        with self.assertRaises(ValueError):
            self.module.handle({"v": 1, "id": "bad", "op": "measure", "args": {"rgb": [81, 0, 0]}})
        self.module.stop_with_error("bad", "PROTOCOL_ERROR", "invalid")
        self.assertEqual(self.module.pixels.color, (0, 0, 0))
        self.assertIsNotNone(self.module.fault)
        with self.assertRaises(RuntimeError):
            self.module.handle({"v": 1, "id": "m", "op": "measure", "args": {"rgb": [0, 0, 0]}})
        self.module.handle({"v": 1, "id": "off", "op": "off", "args": {}})

    def test_simulated_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.module.handle({"v": 1, "id": "sim", "op": "hello", "args": {"mode": "SIMULATED"}})


if __name__ == "__main__":
    unittest.main()
