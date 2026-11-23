"""与硬件无关的光谱、网格和质量检查。"""

import math
import numbers

import numpy as np

from . import LabError

CHANNELS_NM = (415, 445, 480, 515, 555, 590, 630, 680)


def make_grid():
    """按 index = 121*r + 11*g + b 生成固定候选网格。"""
    return np.array(
        [(r / 10, g / 10, b / 10) for r in range(11) for g in range(11) for b in range(11)],
        dtype=float,
    )


GRID = make_grid()


def validate_x(value):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != 3:
        raise LabError("INVALID_INPUT", "RGB 必须包含三个有限数值")
    result = []
    for component in value:
        if isinstance(component, (bool, np.bool_)) or not isinstance(component, numbers.Real):
            raise LabError("INVALID_INPUT", "RGB 分量不能是布尔值或其他非数值类型")
        component = float(component)
        if not math.isfinite(component) or not 0.0 <= component <= 1.0:
            raise LabError("INVALID_INPUT", "RGB 分量必须在 [0,1] 内，不能是 NaN/Infinity")
        scaled = component * 10.0
        if abs(scaled - round(scaled)) > 1e-8:
            raise LabError("INVALID_INPUT", "RGB 分量必须位于 0.1 步长的网格上")
        result.append(round(scaled) / 10.0)
    return tuple(result)


def led_values(x):
    x = validate_x(x)
    return [int(round(80 * component)) for component in x]


def raw_vector(value):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != len(CHANNELS_NM):
        raise LabError("SENSOR_IO", "AS7341 必须返回 F1–F8 八个通道")
    vector = []
    for count in value:
        if isinstance(count, (bool, np.bool_)) or not isinstance(count, numbers.Real):
            raise LabError("SENSOR_IO", "通道计数不是数值")
        count = float(count)
        if not math.isfinite(count) or count != int(count) or not 0 <= count <= 65535:
            raise LabError("SENSOR_IO", "通道计数必须是 [0,65535] 的整数")
        vector.append(int(count))
    return np.array(vector, dtype=float)


def validate_raw(value, threshold=60000):
    raw = raw_vector(value)
    if not isinstance(threshold, int) or not 1 <= threshold <= 65536:
        raise LabError("CONFIG_INVALID", "饱和计数阈值必须在 [1,65536] 内")
    if np.any(raw >= threshold):
        raise LabError("SATURATED", f"检测到通道计数 >= {threshold}；停止并重新设置曝光或几何")
    return raw


def validate_repeats(raw_rows, expected, phase):
    if len(raw_rows) != expected:
        raise LabError("MEASUREMENT_INVALID", f"{phase} 应有 {expected} 次读数")
    return np.array([raw_vector(row) for row in raw_rows], dtype=float)


def calibration_stats(dark_rows, reference_rows):
    dark = validate_repeats(dark_rows, len(dark_rows), "dark")
    reference = validate_repeats(reference_rows, len(reference_rows), "reference")
    if len(dark) < 2 or len(reference) < 1:
        raise LabError("QUALITY_GATE_FAILED", "暗场至少两次、参考至少一次")
    dark_mean = np.mean(dark, axis=0)
    channel_sd = np.std(dark, axis=0, ddof=1)
    dark_noise = max(float(np.linalg.norm(channel_sd)), 1.0)
    reference_mean = np.mean(reference, axis=0)
    scale = float(np.linalg.norm(reference_mean - dark_mean))
    if not math.isfinite(scale) or scale < 10.0 * dark_noise:
        raise LabError(
            "QUALITY_GATE_FAILED",
            f"参考/暗场质量门失败：A={scale:.6g}，要求 >= {10.0 * dark_noise:.6g}",
        )
    return {
        "dark_mean": dark_mean,
        "reference_mean": reference_mean,
        "dark_channel_sd": channel_sd,
        "dark_noise": dark_noise,
        "scale": scale,
    }


def spectral_loss(raw, dark_mean, scale, target_raw):
    raw = raw_vector(raw)
    dark_mean = np.asarray(dark_mean, dtype=float)
    target_raw = np.asarray(target_raw, dtype=float)
    if dark_mean.shape != (8,) or target_raw.shape != (8,):
        raise LabError("CALIBRATION_INVALID", "校准暗场或目标必须各有八个通道")
    if not math.isfinite(scale) or scale <= 0:
        raise LabError("CALIBRATION_INVALID", "参考尺度 A 必须为有限正数")
    return float(np.linalg.norm((raw - dark_mean - target_raw + dark_mean) / scale))


def relative_stability(raw_rows, dark_mean):
    """按参考文档，五次扣暗光谱的 RMS 变化除以均值向量范数。"""
    rows = validate_repeats(raw_rows, len(raw_rows), "stability")
    if len(rows) < 2:
        raise LabError("MEASUREMENT_INVALID", "重复性测量至少需要两次")
    corrected = rows - np.asarray(dark_mean, dtype=float)
    center = np.mean(corrected, axis=0)
    center_norm = float(np.linalg.norm(center))
    rms = float(np.sqrt(np.mean(np.linalg.norm(corrected - center, axis=1) ** 2)))
    denominator = max(center_norm, 1e-12)
    return {
        "rms": rms,
        "mean_corrected_norm": center_norm,
        "rms_relative": rms / denominator,
        "mean_corrected": center.tolist(),
    }
