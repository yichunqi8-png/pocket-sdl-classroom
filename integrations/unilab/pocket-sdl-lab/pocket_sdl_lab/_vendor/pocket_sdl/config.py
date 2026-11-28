"""严格读取配置并生成设备、实验配置指纹。"""

import copy
import hashlib
import json
import math

from os import PathLike

from . import LabError
from .core import CHANNELS_NM, validate_x

DEFAULT_CONFIG = {
    "schema_version": 1,
    "mode": "PHYSICAL",
    "protocol_version": 1,
    "seed": 7,
    "sensor": {
        "address": 57,
        "atime": 100,
        "astep": 999,
        "gain": "GAIN_16X",
        "saturation_raw_exclusive": 60000,
    },
    "led": {"max_channel": 80, "settle_ms": 50},
    "calibration": {
        "reference_x": [1.0, 1.0, 1.0],
        "dark_repeats": 3,
        "reference_repeats": 3,
        "target_x": [0.3, 0.6, 0.4],
        "target_repeats": 3,
        "stability_repeats": 5,
        "stability_rms_relative_max": 0.05,
    },
    "measurement": {"budget": 24, "initial_candidates": 4, "minimum_valid": 20, "confirm_repeats": 3},
    "optimizer": {"length_scale": 0.35, "noise": 0.0025, "xi": 0.01},
}


def strict_json(text):
    def reject_constant(value):
        raise ValueError(f"JSON 不允许 {value}")

    return json.loads(text, parse_constant=reject_constant)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_digest(data):
    return hashlib.sha256(data).hexdigest()


def _merge(default, supplied):
    if set(supplied) - set(default):
        raise LabError("CONFIG_INVALID", f"未知配置字段：{sorted(set(supplied) - set(default))}")
    result = copy.deepcopy(default)
    for key, value in supplied.items():
        if isinstance(default[key], dict):
            if not isinstance(value, dict):
                raise LabError("CONFIG_INVALID", f"{key} 必须是 JSON 对象")
            result[key] = _merge(default[key], value)
        else:
            result[key] = value
    return result


def validate_config(config):
    if type(config.get("schema_version")) is not int or type(config.get("protocol_version")) is not int or config["schema_version"] != 1 or config["protocol_version"] != 1:
        raise LabError("CONFIG_INVALID", "仅支持 schema/protocol version 1")
    if config.get("mode") not in ("PHYSICAL", "SIMULATED"):
        raise LabError("CONFIG_INVALID", "mode 只能是 PHYSICAL 或 SIMULATED")
    if config["mode"] == "PHYSICAL" and "simulation" in config:
        raise LabError("CONFIG_INVALID", "物理配置不能混入 simulation")
    sensor = config["sensor"]
    for field, low, high in (("address", 1, 127), ("atime", 0, 255), ("astep", 0, 65534), ("saturation_raw_exclusive", 1, 65536)):
        if isinstance(sensor[field], bool) or not isinstance(sensor[field], int) or not low <= sensor[field] <= high:
            raise LabError("CONFIG_INVALID", f"sensor.{field} 超出整数范围")
    if sensor["address"] != 57 or sensor["gain"] not in {f"GAIN_{value}X" for value in (16, 32, 64, 128, 256, 512)} | {"GAIN_0_5X", "GAIN_1X", "GAIN_2X", "GAIN_4X", "GAIN_8X"}:
        raise LabError("CONFIG_INVALID", "AS7341 地址或 gain 不受支持")
    if config["led"] != {"max_channel": 80, "settle_ms": 50}:
        raise LabError("CONFIG_INVALID", "必须与固件固定配置一致：LED 上限 80、稳定等待 50ms")
    calibration = config["calibration"]
    calibration["reference_x"] = list(validate_x(calibration["reference_x"]))
    calibration["target_x"] = list(validate_x(calibration["target_x"]))
    for field, low, high in (("dark_repeats", 2, 20), ("reference_repeats", 1, 20), ("target_repeats", 1, 20), ("stability_repeats", 2, 20)):
        if isinstance(calibration[field], bool) or not isinstance(calibration[field], int) or not low <= calibration[field] <= high:
            raise LabError("CONFIG_INVALID", f"calibration.{field} 超出范围")
    quality = calibration["stability_rms_relative_max"]
    if isinstance(quality, bool) or not isinstance(quality, (int, float)) or not math.isfinite(quality) or not 0 < quality < 1:
        raise LabError("CONFIG_INVALID", "重复性相对阈值必须在 (0,1) 内")
    measurement = config["measurement"]
    for field, low, high in (("budget", 1, 1331), ("initial_candidates", 1, 1331), ("minimum_valid", 1, 1331), ("confirm_repeats", 2, 20)):
        if isinstance(measurement[field], bool) or not isinstance(measurement[field], int) or not low <= measurement[field] <= high:
            raise LabError("CONFIG_INVALID", f"measurement.{field} 超出范围")
    if not 1 <= measurement["initial_candidates"] <= measurement["budget"] or measurement["minimum_valid"] > measurement["budget"]:
        raise LabError("CONFIG_INVALID", "minimum_valid、初始化点数和预算矛盾")
    if not isinstance(config["seed"], int) or isinstance(config["seed"], bool):
        raise LabError("CONFIG_INVALID", "seed 必须是整数")
    for value in config["optimizer"].values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise LabError("CONFIG_INVALID", "GP 超参数必须为有限正数")
    simulation = config.get("simulation", {})
    if config["mode"] == "SIMULATED":
        if not isinstance(simulation.get("seed"), int) or isinstance(simulation.get("seed"), bool):
            raise LabError("CONFIG_INVALID", "simulation.seed 必须是整数")
        noise = simulation.get("noise_sd")
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not math.isfinite(noise) or noise < 0:
            raise LabError("CONFIG_INVALID", "simulation.noise_sd 必须有限且非负")
    return config


def _from_supplied(supplied):
    if not isinstance(supplied, dict) or not isinstance(supplied.get("mode"), str):
        raise LabError("CONFIG_INVALID", "配置顶层必须是包含 mode 的 JSON 对象")
    defaults = copy.deepcopy(DEFAULT_CONFIG)
    if supplied["mode"] == "SIMULATED":
        defaults["simulation"] = {"noise_sd": 2.0, "seed": 20261021}
    return validate_config(_merge(defaults, supplied))


def load_config(value):
    """接收配置文件路径或配置字典；字符串表示路径，不表示 JSON 文本。"""
    if isinstance(value, dict):
        return _from_supplied(value)
    if not isinstance(value, (str, bytes, PathLike)):
        raise LabError("CONFIG_INVALID", "配置必须是路径或包含 mode 的 JSON 对象")
    from pathlib import Path
    try:
        supplied = strict_json(Path(value).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LabError("CONFIG_INVALID", f"无法读取配置：{error}") from error
    return _from_supplied(supplied)


def expected_device_base(config):
    return {
        "mode": config["mode"],
        "protocol_version": config["protocol_version"],
        "address": config["sensor"]["address"],
        "atime": config["sensor"]["atime"],
        "astep": config["sensor"]["astep"],
        "gain": config["sensor"]["gain"],
        "channels_nm": list(CHANNELS_NM),
        "gpio": {"sda": 26, "scl": 27, "rgb": 28},
        "led_max_channel": config["led"]["max_channel"],
        "settle_ms": config["led"]["settle_ms"],
    }


def expected_device_fingerprint(config):
    return digest(expected_device_base(config))


def experiment_fingerprint(config):
    return digest(config)


def validate_device_info(info, config):
    if not isinstance(info, dict):
        raise LabError("PROTOCOL_ERROR", "hello 缺少设备信息")
    base = expected_device_base(config)
    for key, expected in base.items():
        if info.get(key) != expected:
            raise LabError("CONFIG_MISMATCH", f"设备配置 {key}={info.get(key)!r}，期望 {expected!r}")
    if info.get("device_fingerprint") != expected_device_fingerprint(config):
        raise LabError("CONFIG_MISMATCH", "设备配置指纹不匹配")
    if not isinstance(info.get("firmware_version"), str) or not info["firmware_version"]:
        raise LabError("PROTOCOL_ERROR", "缺少固件版本")
    return info
