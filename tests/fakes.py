"""测试辅助，不使用真实硬件或真实数据来源。"""

import numpy as np

from pocket_sdl import LabError
from pocket_sdl.simulated import SimDevice


class FailingDevice(SimDevice):
    def __init__(self, config, error_code="SENSOR_IO", fail_at=1):
        self.error_code = error_code
        self.numerical_fail_at = fail_at if error_code == "NUMERICAL_ERROR" else None
        super().__init__(
            config,
            fail_at=fail_at if error_code not in ("SATURATED", "NUMERICAL_ERROR") else None,
            saturated_at=fail_at if error_code == "SATURATED" else None,
        )

    def measure(self, x):
        if self.sample_count + 1 == self.numerical_fail_at:
            from unittest.mock import patch
            with patch("numpy.linalg.cholesky", side_effect=np.linalg.LinAlgError("injected")):
                return super().measure(x)
        return super().measure(x)


class CorruptDevice(SimDevice):
    def measure(self, x):
        return [float("nan")] * 8
