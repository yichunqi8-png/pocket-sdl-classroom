import json
import time
import unittest
from unittest.mock import patch

from pocket_sdl import LabError
from pocket_sdl.config import expected_device_base, expected_device_fingerprint, load_config
from pocket_sdl.transport import SerialDevice, decode_line


def response(request, operation, args):
    if operation == "hello":
        info = expected_device_base(load_config("configs/physical.json"))
        info.update(device_fingerprint=expected_device_fingerprint(load_config("configs/physical.json")), firmware_version="test-firmware")
        return info
    if operation == "measure":
        return {"raw": list(range(8)), "sample_seq": 1}
    return {"led_off": True}


class FakeSerial:
    def __init__(self):
        self.buffer = bytearray()
        self.chunks = []
        self.written = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.buffer) + sum(len(chunk) for chunk in self.chunks)

    def reset_input_buffer(self):
        self.buffer.clear()
        self.chunks.clear()

    def write(self, data):
        self.written.append(data)
        request = json.loads(data)
        reply = json.dumps({"v": 1, "id": request["id"], "ok": True, "result": response(request, request["op"], request["args"])}).encode() + b"\n"
        if request["op"] == "hello":
            # 两次粘包拆包 + 空行。
            self.buffer.extend(b"\n" + reply[:11])
            self.chunks.append(reply[11:])
        else:
            self.buffer.extend(reply)
        return len(data)

    def flush(self):
        pass

    def read(self, count):
        if self.chunks and len(self.buffer) < count:
            amount = count - len(self.buffer)
            chunk = self.chunks.pop(0)
            self.buffer.extend(chunk[:amount])
            if len(chunk) > amount:
                self.chunks.insert(0, chunk[amount:])
        result = bytes(self.buffer[:count])
        self.buffer = self.buffer[count:]
        return result

    def close(self):
        self.closed = True


class TimeoutSerial(FakeSerial):
    def write(self, data):
        return len(data)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config("configs/physical.json")

    def test_decodes_error_and_bad_id(self):
        self.assertEqual(decode_line(b'{"v":1,"id":"a","ok":true,"result":{}}', "a")["result"], {})
        for raw in (b'not json', b'{"v":1,"id":"b","ok":true,"result":{}}', b'{"v":1,"id":"a","ok":true}', b'{"v":1,"id":"a","ok":true,"result":{},"error":{}}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(LabError, "PROTOCOL_ERROR"):
                decode_line(raw, "a")
        with self.assertRaisesRegex(LabError, "SENSOR_IO"):
            decode_line(b'{"v":1,"id":"a","ok":false,"error":{"code":"SENSOR_IO","message":"read"}}', "a")

    @patch("serial.Serial")
    def test_split_and_joined_response(self, serial_class):
        fake = FakeSerial()
        serial_class.return_value = fake
        dev = SerialDevice("TEST", self.config)
        self.assertEqual(dev.measure([0.3, 0.6, 0.4]).tolist(), list(range(8)))
        sent = json.loads(fake.written[-1])
        self.assertEqual(sent["args"]["rgb"], [24, 48, 32])
        dev.close()
        self.assertTrue(fake.closed)

    @patch("serial.Serial")
    def test_total_timeout(self, serial_class):
        serial_class.return_value = TimeoutSerial()
        started = time.monotonic()
        # 隔离关闭串口的清理时间，只验证本次 hello 受 5s 总期限约束。
        with patch.object(SerialDevice, "off", return_value=False), self.assertRaisesRegex(LabError, "SERIAL_TIMEOUT"):
            SerialDevice("TEST", self.config)
        self.assertGreaterEqual(time.monotonic() - started, 4.9)
        self.assertLess(time.monotonic() - started, 6.0)


if __name__ == "__main__":
    unittest.main()
