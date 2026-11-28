"""CircuitPython USB CDC data 的带 ID、deadline JSON 协议。"""

import json
import time
import uuid

from . import LabError
from .config import canonical_json, strict_json, validate_device_info
from .core import led_values, raw_vector, validate_x


def decode_line(line, expected_id=None, expected_version=1):
    """独立校验一帧，供串口和单元测试共同使用。"""
    try:
        if not isinstance(line, bytes):
            raise ValueError("响应不是 bytes")
        if len(line) > 8192:
            raise ValueError("响应帧超过 8192 bytes")
        message = strict_json(line.decode("utf-8"))
        if not isinstance(message, dict) or type(message.get("v")) is not int or message.get("v") != expected_version:
            raise ValueError("协议版本不匹配")
        if not isinstance(message.get("ok"), bool):
            raise ValueError("响应缺少合法 ok")
        if "id" not in message or not (message["id"] is None or (isinstance(message["id"], str) and message["id"])):
            raise ValueError("响应缺少合法 id")
        if expected_id is not None and message["id"] != expected_id:
            raise ValueError(f"响应 ID 不匹配：{message['id']!r}")
        if message["ok"]:
            if not isinstance(message.get("result"), dict) or "error" in message:
                raise ValueError("成功响应缺少 result")
        else:
            error = message.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
                raise ValueError("错误响应缺少 error.code/message")
            raise LabError(error["code"], error["message"])
        return message
    except LabError:
        raise
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise LabError("PROTOCOL_ERROR", f"无效 Pico 响应：{error}") from error


class SerialDevice:
    source = "PHYSICAL"

    def __init__(self, port, config, hello_timeout=5.0):
        if not isinstance(port, str) or not port.strip():
            raise LabError("CONFIG_INVALID", "物理设备必须显式指定串口")
        self.config = config
        self.port_name = port
        self.serial = None
        self.closed = False
        self.request_count = 0
        try:
            import serial
        except ImportError as error:
            raise LabError("SERIAL_IO", "缺少 pyserial，请先安装 requirements.txt") from error
        try:
            self.serial = serial.Serial(
                port=port,
                baudrate=115200,
                timeout=0.05,
                write_timeout=2.0,
                rtscts=False,
                dsrdtr=False,
            )
            self.hello(timeout=hello_timeout)
        except LabError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise LabError("SERIAL_IO", f"打开串口 {port} 失败：{error}") from error

    def _request(self, operation, arguments=None, timeout=2.0):
        if self.closed or self.serial is None:
            raise LabError("DISCONNECTED", "串口已关闭")
        self.request_count += 1
        request_id = uuid.uuid4().hex
        frame = {"v": 1, "id": request_id, "op": operation, "args": arguments or {}}
        data = (canonical_json(frame) + "\n").encode("utf-8")
        deadline = time.monotonic() + timeout
        buffer = bytearray()
        try:
            # 发送前清理陈旧输入；不能在 write 后清理，避免丢弃立即响应。
            self.serial.reset_input_buffer()
            self.serial.write(data)
            self.serial.flush()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LabError("SERIAL_TIMEOUT", f"{operation} 在 {timeout:g}s 内没有匹配响应")
                if self.serial.in_waiting:
                    chunk = self.serial.read(self.serial.in_waiting)
                    if not chunk:
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > 8192:
                        raise LabError("PROTOCOL_ERROR", "Pico 响应过长")
                    while b"\n" in buffer:
                        line, _, rest = buffer.partition(b"\n")
                        buffer = bytearray(rest)
                        if line.strip():
                            return decode_line(bytes(line).strip(), request_id)["result"]
                else:
                    # 读取等待上限由 timeout=0.05 控制；sleep 仅避免忙等。
                    time.sleep(min(0.005, remaining))
        except LabError:
            raise
        except Exception as error:
            raise LabError("DISCONNECTED", f"USB 串口通信中断：{error}") from error

    def hello(self, timeout=5.0):
        result = self._request("hello", {"mode": self.source}, timeout)
        validate_device_info(result, self.config)
        self.device_info = result
        return result

    def measure(self, x):
        rgb = led_values(validate_x(x))
        result = self._request("measure", {"rgb": rgb}, 5.0)
        if "raw" not in result or type(result.get("sample_seq")) is not int:
            raise LabError("PROTOCOL_ERROR", "measure 缺少 raw 或 sample_seq")
        return raw_vector(result["raw"])

    def off(self):
        try:
            result = self._request("off", timeout=2.0)
            return bool(result.get("led_off", False))
        except LabError:
            return False

    def close(self):
        if self.closed:
            return
        try:
            self.off()
        finally:
            self.closed = True
            if self.serial is not None:
                try:
                    self.serial.close()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
