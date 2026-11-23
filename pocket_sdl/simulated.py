"""明确标记的软件响应模型；仅用于程序测试，不代表真实传感器。"""

import numpy as np

from . import LabError
from .config import expected_device_base, expected_device_fingerprint
from .core import validate_x


# 单位亮度下八个重叠通道的简化响应；仅用于验证接口、优化与账本。
BASIS = np.array(
    [
        [260.0, 500.0, 1050.0, 700.0, 100.0, 35.0, 15.0, 10.0],
        [80.0, 260.0, 450.0, 1150.0, 850.0, 180.0, 40.0, 15.0],
        [15.0, 20.0, 50.0, 200.0, 520.0, 1100.0, 1400.0, 1250.0],
    ]
)
DARK = np.array([90.0, 95.0, 100.0, 110.0, 120.0, 130.0, 140.0, 145.0])


class SimDevice:
    source = "SIMULATED"

    def __init__(self, config, fail_at=None, saturated_at=None):
        if config["mode"] != "SIMULATED":
            raise LabError("CONFIG_MISMATCH", "SimDevice 只接受 SIMULATED 配置")
        self.config = config
        self.fail_at = fail_at
        self.saturated_at = saturated_at
        self.closed = False
        self.sample_count = 0
        self.rgb = [0, 0, 0]
        self.x = [0.0, 0.0, 0.0]
        self.off_count = 0
        self.measurements = []
        self.rng = np.random.default_rng(config.get("simulation", {}).get("seed", 20261021))
        self.hello()

    def hello(self):
        result = expected_device_base(self.config)
        result.update(
            device_fingerprint=expected_device_fingerprint(self.config),
            firmware_version="simulation-1.0.0",
        )
        self.device_info = result
        return result

    def measure(self, x):
        if self.closed:
            raise LabError("DISCONNECTED", "模拟设备已关闭")
        x = list(validate_x(x))
        self.sample_count += 1
        self.off_count = 0
        if self.fail_at == self.sample_count:
            raise LabError("SERIAL_TIMEOUT", f"模拟第 {self.sample_count} 次测量超时")
        if self.saturated_at == self.sample_count:
            return np.full(8, 65535, dtype=float)
        self.x = x
        self.rgb = [round(80 * component) for component in x]
        self.measurements.append(x)
        value = DARK + np.asarray(x) @ BASIS
        value += self.rng.normal(0.0, self.config.get("simulation", {}).get("noise_sd", 2.0), size=8)
        # 16 位传感器的非负边界是响应模型的一部分，饱和另由质检拒绝。
        return np.clip(np.rint(value), 0, 65535).astype(float)

    def off(self):
        self.off_count += 1
        self.rgb = [0, 0, 0]
        return True

    def close(self):
        if not self.closed:
            self.off()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
