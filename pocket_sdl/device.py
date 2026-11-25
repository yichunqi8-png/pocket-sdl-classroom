"""面向 Uni-Lab OS 的常驻设备封装与串口自动发现模块。

将硬件通信、USB CDC data 串口自动发现、5 秒看门狗关灯保护、线程锁、
内存状态缓存、饱和校验与校准质量门全部收口在设备层。
"""

import copy
import logging
import math
import numbers
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import LabError
from .config import (
    expected_device_fingerprint,
    experiment_fingerprint,
    load_config,
    validate_device_info,
)
from .core import (
    CHANNELS_NM,
    calibration_stats,
    led_values,
    relative_stability,
    spectral_loss,
    validate_raw,
    validate_x,
)
from .ledger import Ledger, atomic_json, timestamp
from .workflow import open_device, read_calibration

# Driver stays independent; only the integration adapter registers UniLabOS types.
def device(**_kwargs: Any) -> Callable[[Any], Any]:
    def _decorator(cls: Any) -> Any:
        return cls

    return _decorator

def action(**_kwargs: Any) -> Callable[[Any], Any]:
    def _decorator(fn: Any) -> Any:
        return fn

    return _decorator

def not_action(fn: Any) -> Any:
    return fn

def topic_config(**_kwargs: Any) -> Callable[[Any], Any]:
    def _decorator(fn: Any) -> Any:
        return fn

    return _decorator


logger = logging.getLogger(__name__)

PICO_USB_VIDS = (0x2E8A, 0x239A)
_PORT_NUM_RE = re.compile(r"(\d+)$")
_VALID_STATUSES = ("Idle", "Running", "Error", "Offline", "Unknown")


def _port_sort_key(port_info: Any) -> Tuple[int, int, int, str]:
    """优先排列 Pico / CircuitPython 的非 Console 数据口，同板优先编号更高的端口。"""
    vid = getattr(port_info, "vid", None)
    desc = (getattr(port_info, "description", "") or "").lower()
    hwid = (getattr(port_info, "hwid", "") or "").lower()
    device_name = str(getattr(port_info, "device", ""))

    is_pico = vid in PICO_USB_VIDS or any(
        tok in desc or tok in hwid for tok in ("pico", "circuitpython", "rp2040")
    )
    is_console = "console" in desc or "repl" in desc
    is_data = "data" in desc

    match = _PORT_NUM_RE.search(device_name)
    trailing_num = int(match.group(1)) if match else -1

    return (
        0 if is_pico else 1,
        0 if is_data else (2 if is_console else 1),
        -trailing_num,
        device_name,
    )


def _normalize_rgb(value: Sequence[float], snap_to_grid: bool) -> List[float]:
    if not snap_to_grid:
        return list(validate_x(value))
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != 3:
        raise LabError("INVALID_INPUT", "RGB 必须包含三个有限数值")
    snapped: List[float] = []
    for component in value:
        if isinstance(component, (bool, np.bool_)) or not isinstance(component, numbers.Real):
            raise LabError("INVALID_INPUT", "RGB 分量不能是布尔值或其他非数值类型")
        val = float(component)
        if not math.isfinite(val) or not 0.0 <= val <= 1.0:
            raise LabError("INVALID_INPUT", "RGB 分量必须在 [0,1] 内，不能是 NaN/Infinity")
        snapped.append(round(val * 10.0) / 10.0)
    return list(validate_x(snapped))


def discover_pico_port(
    config: Dict[str, Any],
    serial_number: Optional[str] = None,
    probe_timeout: float = 1.5,
) -> Tuple[str, Any]:
    """自动探测并握手验证 Pico H 的 USB CDC data 串口，返回 (port_name, SerialDevice)。"""
    try:
        from serial.tools import list_ports
    except ImportError as error:
        raise LabError("SERIAL_IO", "缺少 pyserial，请先安装依赖") from error

    from .transport import SerialDevice

    ports = list(list_ports.comports())
    if serial_number is not None:
        ports = [item for item in ports if getattr(item, "serial_number", None) == serial_number]
        if not ports:
            raise LabError("SERIAL_IO", f"未找到序列号为 {serial_number!r} 的串口设备")

    if not ports:
        raise LabError("SERIAL_IO", "未发现任何串口；请检查 Pico H 连接与 USB 数据线")

    candidates = sorted(ports, key=_port_sort_key)
    failures: List[str] = []
    for candidate in candidates:
        port_name = str(candidate.device)
        try:
            dev = SerialDevice(port_name, config, hello_timeout=probe_timeout)
            return port_name, dev
        except LabError as error:
            failures.append(f"{port_name} ({error.code}: {error.message})")
    detail = "; ".join(failures)
    raise LabError("SERIAL_IO", f"自动探测 Pico CDC data 串口失败，已尝试：{detail}")


@device(
    id="pico_spectro",
    category=["spectrometer", "optical", "self_driving_lab"],
    display_name="Pico AS7341 光谱闭环模块",
    description="基于 Raspberry Pi Pico H、WS2812B LED 与 AS7341 八通道光谱传感器的自主光学测量设备。",
    version="1.0.0",
)
class PicoSpectroDevice:
    """常驻、线程安全、遵循 Uni-Lab OS 契约的 Pico H + AS7341 + WS2812B 设备类。

    核心约束：
    - 构造时只保存并校验配置参数，不连接硬件（需调用 connect() 后执行测量）；
    - 状态属性仅读内存缓存，绝不触发串口 I/O；
    - 默认 measure 后立即关灯，避免触发固件 5 秒看门狗；
    - 故障态阻止常规测量与校准，stop / reset / disconnect 在故障态下仍可调用。
    """

    def __init__(
        self,
        mode: str = "SIMULATED",
        port: str = "auto",
        serial_number: Optional[str] = None,
        config_path: Optional[str] = None,
        auto_off_after_measure: bool = True,
        snap_to_grid: bool = False,
        probe_timeout: float = 1.5,
    ) -> None:
        normalized_mode = str(mode).upper()
        if normalized_mode not in ("PHYSICAL", "SIMULATED"):
            raise LabError("CONFIG_INVALID", "mode 只能是 PHYSICAL 或 SIMULATED")

        if config_path is not None:
            self.config = load_config(config_path)
            if self.config["mode"] != normalized_mode:
                raise LabError(
                    "CONFIG_MISMATCH",
                    f"配置文件 mode={self.config['mode']} 与请求 mode={normalized_mode} 不一致",
                )
        else:
            self.config = load_config({"mode": normalized_mode})

        if normalized_mode == "SIMULATED" and port not in (None, "", "auto"):
            raise LabError("CONFIG_INVALID", "SIMULATED 模式不能指定物理串口 port")

        if isinstance(probe_timeout, bool) or not isinstance(probe_timeout, numbers.Real) or not math.isfinite(float(probe_timeout)) or float(probe_timeout) <= 0:
            raise LabError("CONFIG_INVALID", "probe_timeout 必须是大于 0 的有限数值")

        self._mode = normalized_mode
        self._requested_port = port
        self._serial_number = serial_number
        self._config_path = config_path
        self._auto_off_after_measure = bool(auto_off_after_measure)
        self._snap_to_grid = bool(snap_to_grid)
        self._probe_timeout = float(probe_timeout)

        self._lock = threading.RLock()
        self._injected_device: Any = None
        self._dev: Any = None
        self._resolved_port: str = ""
        self._device_info: Dict[str, Any] = {}
        self._calibration: Optional[Dict[str, Any]] = None

        # 内存状态缓存（标准状态值：Idle / Running / Error / Offline / Unknown）
        self._status: str = "Offline"
        self._connected: bool = False
        self._last_rgb: List[float] = [0.0, 0.0, 0.0]
        self._last_led_rgb: List[int] = [0, 0, 0]
        self._last_raw: List[int] = [0] * len(CHANNELS_NM)
        self._last_loss: Optional[float] = None
        self._last_error: str = ""
        self._sample_count: int = 0

    # ------------------------------------------------------------------
    # 状态属性（@topic_config 紧贴在 @property 下方，仅做内存只读查询）
    # ------------------------------------------------------------------

    @property
    @topic_config(period=2.0)
    def status(self) -> str:
        if self._status not in _VALID_STATUSES:
            return "Unknown"
        return self._status

    @property
    @topic_config(period=2.0)
    def connected(self) -> bool:
        return bool(self._connected)

    @property
    @topic_config(period=5.0)
    def mode(self) -> str:
        return self._mode

    @property
    @topic_config(period=5.0)
    def port(self) -> str:
        return self._resolved_port

    @property
    @topic_config(period=10.0)
    def channels_nm(self) -> List[int]:
        return list(CHANNELS_NM)

    @property
    @topic_config(period=10.0)
    def device_fingerprint(self) -> str:
        return str(self._device_info.get("device_fingerprint", ""))

    @property
    @topic_config(period=10.0)
    def firmware_version(self) -> str:
        return str(self._device_info.get("firmware_version", ""))

    @property
    @topic_config(period=2.0)
    def last_rgb(self) -> List[float]:
        return list(self._last_rgb)

    @property
    @topic_config(period=2.0)
    def last_led_rgb(self) -> List[int]:
        return list(self._last_led_rgb)

    @property
    @topic_config(period=2.0)
    def last_raw(self) -> List[int]:
        return list(self._last_raw)

    @property
    @topic_config(period=2.0)
    def last_loss(self) -> Optional[float]:
        return self._last_loss

    @property
    @topic_config(period=5.0)
    def calibrated(self) -> bool:
        return self._calibration is not None

    @property
    @topic_config(period=5.0)
    def calibration_id(self) -> str:
        if self._calibration is None:
            return ""
        return str(self._calibration["id"])

    @property
    @topic_config(period=2.0)
    def last_error(self) -> str:
        return self._last_error

    @property
    @topic_config(period=5.0)
    def sample_count(self) -> int:
        return self._sample_count

    # ------------------------------------------------------------------
    # 内部辅助方法（下划线开头，不暴露为动作）
    # ------------------------------------------------------------------

    def _attach_device(self, dev: Any) -> None:
        """测试辅助：注入模拟或故障设备实例，不污染 __init__ 配置签名。"""
        with self._lock:
            self._injected_device = dev

    def _require_ready(self) -> Any:
        if self._status == "Error":
            raise LabError("DEVICE_FAULT", f"设备处于故障态，请先排查并调用 reset()：{self._last_error}")
        if not self._connected or self._dev is None or getattr(self._dev, "closed", False):
            raise LabError("DISCONNECTED", "设备尚未连接或已断开，请先调用 connect()")
        return self._dev

    def _record_error(self, action_name: str, error: BaseException) -> None:
        self._status = "Error"
        self._last_error = str(error)
        logger.error("action=%s success=False error=%s", action_name, error)

    def _best_effort_off(self) -> bool:
        if self._dev is None or getattr(self._dev, "closed", False):
            return False
        try:
            return bool(self._dev.off())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 对外动作（@action，带参数范围、单位说明及稳定 success/message/data 结构）
    # ------------------------------------------------------------------

    @action(description="建立与 Pico 光谱模块的硬件或仿真连接并校验配置指纹；无需参数。")
    def connect(self) -> Dict[str, Any]:
        with self._lock:
            if self._connected and self._dev is not None and not getattr(self._dev, "closed", False):
                if self._status == "Error":
                    raise LabError("DEVICE_FAULT", f"设备处于故障态，请调用 reset() 恢复：{self._last_error}")
                data = {
                    "connected": True,
                    "mode": self._mode,
                    "port": self._resolved_port,
                    "device_info": copy.deepcopy(self._device_info),
                }
                return {"success": True, "message": "设备已连接", "data": data}

            try:
                if self._injected_device is not None:
                    dev = self._injected_device
                    if getattr(dev, "closed", False):
                        raise LabError("DISCONNECTED", "注入的设备实例已关闭")
                    info = validate_device_info(dev.device_info, self.config)
                    resolved = str(getattr(dev, "port_name", "INJECTED"))
                elif self._mode == "SIMULATED":
                    dev = open_device(self.config, port=None)
                    info = validate_device_info(dev.device_info, self.config)
                    resolved = "SIMULATED"
                else:
                    if self._requested_port in (None, "", "auto"):
                        resolved, dev = discover_pico_port(
                            self.config,
                            serial_number=self._serial_number,
                            probe_timeout=self._probe_timeout,
                        )
                    else:
                        dev = open_device(self.config, port=self._requested_port)
                        resolved = str(self._requested_port)
                    info = validate_device_info(dev.device_info, self.config)
            except BaseException as error:
                self._connected = False
                self._dev = None
                self._record_error("connect", error)
                raise

            self._dev = dev
            self._resolved_port = resolved
            self._device_info = copy.deepcopy(info)
            self._connected = True
            self._status = "Idle"
            self._last_error = ""
            logger.info("action=connect success=True mode=%s port=%s", self._mode, resolved)
            return {
                "success": True,
                "message": f"已连接设备（{self._mode}，端口 {resolved}）",
                "data": {
                    "connected": True,
                    "mode": self._mode,
                    "port": self._resolved_port,
                    "device_info": copy.deepcopy(self._device_info),
                },
            }

    @action(description="安全关闭 LED 并断开设备连接；故障态下仍可调用。")
    def disconnect(self) -> Dict[str, Any]:
        with self._lock:
            dev = self._dev
            self._dev = None
            self._connected = False
            self._status = "Offline"
            self._last_rgb = [0.0, 0.0, 0.0]
            self._last_led_rgb = [0, 0, 0]
            if dev is not None:
                try:
                    dev.close()
                except Exception as error:
                    logger.warning("action=disconnect close_warning=%s", error)
            logger.info("action=disconnect success=True")
            return {
                "success": True,
                "message": "已关闭 LED 并断开设备连接",
                "data": {"connected": False, "status": self._status},
            }

    @action(description="安全停止动作：立即关闭 WS2812B LED；故障态下仍可调用。")
    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._dev is None or getattr(self._dev, "closed", False):
                self._last_rgb = [0.0, 0.0, 0.0]
                self._last_led_rgb = [0, 0, 0]
                logger.info("action=stop success=True offline=True")
                return {
                    "success": True,
                    "message": "设备未连接，LED 状态已清零",
                    "data": {"led_off": True, "status": self._status},
                }
            ok = self._best_effort_off()
            self._last_rgb = [0.0, 0.0, 0.0]
            self._last_led_rgb = [0, 0, 0]
            if ok and self._status != "Error":
                self._status = "Idle"
            logger.info("action=stop success=%s led_off=%s", ok, ok)
            return {
                "success": bool(ok),
                "message": "已关闭 WS2812B LED" if ok else "关闭 LED 失败，请检查硬件连接",
                "data": {"led_off": bool(ok), "status": self._status},
            }

    @action(description="故障复位并重新握手恢复设备（清除 Error 态）；故障态下仍可调用。")
    def reset(self) -> Dict[str, Any]:
        with self._lock:
            if self._injected_device is not None:
                if getattr(self._injected_device, "closed", False):
                    raise LabError("DISCONNECTED", "注入的设备实例已关闭，无法复位")
                try:
                    info = validate_device_info(self._injected_device.hello(), self.config)
                except BaseException as error:
                    self._record_error("reset", error)
                    raise
                self._dev = self._injected_device
                self._device_info = copy.deepcopy(info)
                self._connected = True
                self._status = "Idle"
                self._last_error = ""
                logger.info("action=reset success=True injected=True")
                return {
                    "success": True,
                    "message": "设备已重新握手并恢复 Idle 态",
                    "data": {
                        "connected": True,
                        "mode": self._mode,
                        "port": self._resolved_port or "INJECTED",
                        "device_info": copy.deepcopy(self._device_info),
                    },
                }
            self.disconnect()
            result = self.connect()
            result["message"] = "设备已断开重连并恢复 Idle 态"
            return result

    @action(description="执行单次暗场采集与饱和自检（LED 全关，检查 AS7341 F1-F8 计数 < 60000）。")
    def doctor(self) -> Dict[str, Any]:
        with self._lock:
            dev = self._require_ready()
            self._status = "Running"
            try:
                raw = dev.measure((0.0, 0.0, 0.0))
                raw = validate_raw(raw, self.config["sensor"]["saturation_raw_exclusive"])
                self._best_effort_off()
            except BaseException as error:
                self._best_effort_off()
                self._record_error("doctor", error)
                raise
            raw_ints = [int(v) for v in raw.tolist()]
            self._last_rgb = [0.0, 0.0, 0.0]
            self._last_led_rgb = [0, 0, 0]
            self._last_raw = raw_ints
            self._status = "Idle"
            self._last_error = ""
            logger.info("action=doctor success=True dark_max=%d", max(raw_ints))
            return {
                "success": True,
                "message": "单次暗场自检通过",
                "data": {
                    "mode": self._mode,
                    "port": self._resolved_port,
                    "channels_nm": list(CHANNELS_NM),
                    "dark_raw": raw_ints,
                    "device_info": copy.deepcopy(self._device_info),
                },
            }

    @action(
        description=(
            "设置归一化 RGB 亮度并采集 AS7341 F1-F8 八通道原始光谱。"
            "参数 r, g, b 为无量纲归一化亮度，允许范围 0.0 到 1.0（默认步长 0.1，对应 LED 通道 0 到 80）；"
            "auto_off 默认为 true（测完立即关灯避免 5 秒看门狗）；"
            "snap_to_grid 为 true 时将 [0,1] 内浮点数吸附到最近 0.1 网格。"
        )
    )
    def measure(
        self,
        r: float = 0.0,
        g: float = 0.0,
        b: float = 0.0,
        auto_off: Optional[bool] = None,
        snap_to_grid: Optional[bool] = None,
    ) -> Dict[str, Any]:
        use_snap = self._snap_to_grid if snap_to_grid is None else bool(snap_to_grid)
        use_auto_off = self._auto_off_after_measure if auto_off is None else bool(auto_off)
        x = _normalize_rgb([r, g, b], snap_to_grid=use_snap)
        rgb_int = led_values(x)
        threshold = self.config["sensor"]["saturation_raw_exclusive"]

        with self._lock:
            dev = self._require_ready()
            self._status = "Running"
            started = time.monotonic()
            try:
                raw = dev.measure(x)
                raw = validate_raw(raw, threshold)
                off_done = False
                if use_auto_off:
                    off_done = bool(dev.off())
            except BaseException as error:
                self._best_effort_off()
                self._record_error("measure", error)
                raise

            duration_ms = round((time.monotonic() - started) * 1000.0, 3)
            raw_ints = [int(v) for v in raw.tolist()]
            normalized: Optional[List[float]] = None
            loss: Optional[float] = None
            cal_id = ""
            if self._calibration is not None:
                dark = np.asarray(self._calibration["dark_mean"], dtype=float)
                scale = float(self._calibration["reference_scale"])
                target = np.asarray(self._calibration["target_raw"], dtype=float)
                normalized = [float(v) for v in ((raw - dark) / scale).tolist()]
                loss = float(spectral_loss(raw, dark, scale, target))
                cal_id = str(self._calibration["id"])

            self._last_rgb = [0.0, 0.0, 0.0] if off_done else list(x)
            self._last_led_rgb = [0, 0, 0] if off_done else list(rgb_int)
            self._last_raw = raw_ints
            self._last_loss = loss
            self._sample_count += 1
            self._status = "Idle"
            self._last_error = ""
            logger.info("action=measure success=True x=%s duration_ms=%.1f loss=%s", x, duration_ms, loss)
            return {
                "success": True,
                "message": "光谱采集完成",
                "data": {
                    "x": list(x),
                    "led_rgb": list(rgb_int),
                    "channels_nm": list(CHANNELS_NM),
                    "raw": raw_ints,
                    "led_off": off_done,
                    "duration_ms": duration_ms,
                    "timestamp_utc": timestamp(),
                    "calibrated": self._calibration is not None,
                    "calibration_id": cal_id,
                    "normalized": normalized,
                    "loss": loss,
                },
            }

    @action(
        description=(
            "执行暗场、亮参考、目标与 5 次稳定性复测校准并校验质量门，结果缓存在设备内存中。"
            "target_r, target_g, target_b 为目标归一化 RGB（允许范围 0.0 到 1.0，必须同时提供或同时省略）；"
            "save_dir 为可选新建目录路径，提供时同步落盘完整审计账本。"
        )
    )
    def calibrate(
        self,
        target_r: Optional[float] = None,
        target_g: Optional[float] = None,
        target_b: Optional[float] = None,
        save_dir: Optional[str] = None,
        snap_to_grid: Optional[bool] = None,
    ) -> Dict[str, Any]:
        provided = (target_r is not None, target_g is not None, target_b is not None)
        if any(provided) and not all(provided):
            raise LabError("INVALID_INPUT", "target_r、target_g、target_b 必须同时提供或同时省略")

        cal_config = copy.deepcopy(self.config)
        if all(provided):
            use_snap = self._snap_to_grid if snap_to_grid is None else bool(snap_to_grid)
            cal_config["calibration"]["target_x"] = _normalize_rgb(
                [target_r, target_g, target_b],  # type: ignore[list-item]
                snap_to_grid=use_snap,
            )

        settings = cal_config["calibration"]
        threshold = cal_config["sensor"]["saturation_raw_exclusive"]
        exp_fp = experiment_fingerprint(cal_config)

        with self._lock:
            dev = self._require_ready()
            self._status = "Running"
            ledger: Optional[Ledger] = None
            if save_dir is not None:
                ledger = Ledger(
                    save_dir,
                    "prepare",
                    {"mode": cal_config["mode"], "experiment_fingerprint": exp_fp},
                )
                ledger.device_info = copy.deepcopy(self._device_info)

            def _sample(phase: str, x_vec: Sequence[float]) -> np.ndarray:
                started = time.monotonic()
                attempt: Dict[str, Any] = {
                    "timestamp_utc": timestamp(),
                    "phase": phase,
                    "x": list(x_vec),
                    "iteration": None,
                    "status": "error",
                    "raw": None,
                }
                try:
                    raw = dev.measure(x_vec)
                    raw = validate_raw(raw, threshold)
                except BaseException as error:
                    if not isinstance(error, KeyboardInterrupt) and ledger is not None:
                        attempt["error"] = {
                            "code": getattr(error, "code", type(error).__name__),
                            "message": str(error),
                        }
                        attempt["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
                        ledger.attempts(attempt)
                    raise
                if ledger is not None:
                    attempt["status"] = "ok"
                    attempt["raw"] = raw.tolist()
                    attempt["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
                    ledger.attempts(attempt)
                return raw

            try:
                dark = [_sample("dark", (0.0, 0.0, 0.0)) for _ in range(settings["dark_repeats"])]
                reference = [_sample("reference", settings["reference_x"]) for _ in range(settings["reference_repeats"])]
                stats = calibration_stats(dark, reference)
                target = [_sample("target", settings["target_x"]) for _ in range(settings["target_repeats"])]
                target_raw = np.mean(np.stack(target), axis=0)
                stability = [_sample("stability", settings["target_x"]) for _ in range(settings["stability_repeats"])]
                quality = relative_stability(stability, stats["dark_mean"])
                quality.update(
                    limit=settings["stability_rms_relative_max"],
                    passed=quality["rms_relative"] < settings["stability_rms_relative_max"],
                )
                if not quality["passed"]:
                    raise LabError(
                        "QUALITY_GATE_FAILED",
                        f"固定点相对波动 {quality['rms_relative']:.3%} 未小于 {settings['stability_rms_relative_max']:.3%}；请先检查几何和遮光",
                    )

                calibration: Dict[str, Any] = {
                    "schema_version": 1,
                    "mode": cal_config["mode"],
                    "status": "COMPLETED",
                    "id": exp_fp[:12] + "-" + timestamp().replace(":", "").replace("-", ""),
                    "created_at": timestamp(),
                    "channels_nm": list(CHANNELS_NM),
                    "config": cal_config,
                    "device_info": copy.deepcopy(self._device_info),
                    "device_fingerprint": expected_device_fingerprint(cal_config),
                    "experiment_fingerprint": exp_fp,
                    "raw_samples": {
                        "dark": np.stack(dark).astype(int).tolist(),
                        "reference": np.stack(reference).astype(int).tolist(),
                        "target": np.stack(target).astype(int).tolist(),
                        "stability": np.stack(stability).astype(int).tolist(),
                    },
                    "dark_mean": [float(v) for v in stats["dark_mean"].tolist()],
                    "reference_mean": [float(v) for v in stats["reference_mean"].tolist()],
                    "dark_channel_sd": [float(v) for v in stats["dark_channel_sd"].tolist()],
                    "dark_noise": float(stats["dark_noise"]),
                    "reference_scale": float(stats["scale"]),
                    "target_raw": [float(v) for v in target_raw.tolist()],
                    "target_normalized": [
                        float(v) for v in ((target_raw - stats["dark_mean"]) / stats["scale"]).tolist()
                    ],
                    "quality_gate": {
                        "scale_min": float(10.0 * stats["dark_noise"]),
                        "scale_passed": True,
                        "saturation_passed": True,
                    },
                    "stability": quality,
                }
                if ledger is not None:
                    atomic_json(ledger.path / "calibration.json", calibration)
                    ledger.complete({"operation": "prepare", "status": "ok", "calibration_id": calibration["id"]})
            except BaseException as error:
                self._best_effort_off()
                if ledger is not None:
                    ledger.fail(error, {"operation": "prepare"})
                self._record_error("calibrate", error)
                raise
            finally:
                self._best_effort_off()

            self._calibration = calibration
            self._last_rgb = [0.0, 0.0, 0.0]
            self._last_led_rgb = [0, 0, 0]
            self._status = "Idle"
            self._last_error = ""
            logger.info(
                "action=calibrate success=True id=%s scale=%.2f rms=%.4f",
                calibration["id"],
                calibration["reference_scale"],
                quality["rms_relative"],
            )
            return {
                "success": True,
                "message": "校准通过并已载入内存",
                "data": {
                    "calibration_id": calibration["id"],
                    "mode": calibration["mode"],
                    "target_x": list(settings["target_x"]),
                    "reference_scale": calibration["reference_scale"],
                    "dark_noise": calibration["dark_noise"],
                    "dark_mean": list(calibration["dark_mean"]),
                    "target_raw": list(calibration["target_raw"]),
                    "stability_rms_relative": float(quality["rms_relative"]),
                    "out": str(ledger.path) if ledger is not None else "",
                },
            }

    @action(description="从磁盘加载并审计已有 calibration.json 文件到当前设备校准态；calibration_path 为文件路径。")
    def load_calibration(self, calibration_path: str) -> Dict[str, Any]:
        data, _ = read_calibration(calibration_path)
        with self._lock:
            self._require_ready()
            if data["mode"] != self._mode:
                raise LabError(
                    "CONFIG_MISMATCH",
                    f"校准 mode={data['mode']} 与当前设备 mode={self._mode} 不一致",
                )
            if data["device_fingerprint"] != self.device_fingerprint:
                raise LabError("CONFIG_MISMATCH", "校准设备指纹与当前连接硬件不一致")
            self._calibration = data
            logger.info("action=load_calibration success=True id=%s", data["id"])
            return {
                "success": True,
                "message": f"已加载校准 {data['id']}",
                "data": {
                    "calibration_id": str(data["id"]),
                    "mode": str(data["mode"]),
                    "target_x": list(data["config"]["calibration"]["target_x"]),
                    "reference_scale": float(data["reference_scale"]),
                    "dark_noise": float(data["dark_noise"]),
                    "stability_rms_relative": float(data["stability"]["rms_relative"]),
                },
            }

    @action(description="清除设备内存中已加载的校准态。")
    def clear_calibration(self) -> Dict[str, Any]:
        with self._lock:
            self._calibration = None
            self._last_loss = None
            logger.info("action=clear_calibration success=True")
            return {
                "success": True,
                "message": "已清除内存校准态",
                "data": {"calibrated": False},
            }

    # ------------------------------------------------------------------
    # Python 侧便捷方法（标记 @not_action，避免被扫描为 auto-* 动作）
    # ------------------------------------------------------------------

    @not_action
    def measure_rgb(
        self,
        rgb: Sequence[float],
        auto_off: Optional[bool] = None,
        snap_to_grid: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if not isinstance(rgb, (list, tuple, np.ndarray)) or len(rgb) != 3:
            raise LabError("INVALID_INPUT", "rgb 必须是长度为 3 的数组")
        return self.measure(
            r=rgb[0],
            g=rgb[1],
            b=rgb[2],
            auto_off=auto_off,
            snap_to_grid=snap_to_grid,
        )

    @not_action
    def snapshot(self) -> Dict[str, Any]:
        """返回完整且严格 JSON 可序列化的设备状态快照。"""
        return {
            "status": self.status,
            "connected": self.connected,
            "mode": self.mode,
            "port": self.port,
            "channels_nm": self.channels_nm,
            "device_fingerprint": self.device_fingerprint,
            "firmware_version": self.firmware_version,
            "last_rgb": self.last_rgb,
            "last_led_rgb": self.last_led_rgb,
            "last_raw": self.last_raw,
            "last_loss": self.last_loss,
            "calibrated": self.calibrated,
            "calibration_id": self.calibration_id,
            "last_error": self.last_error,
            "sample_count": self.sample_count,
        }

    @not_action
    def close(self) -> None:
        self.disconnect()

    def __enter__(self) -> "PicoSpectroDevice":
        if not self._connected:
            self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
