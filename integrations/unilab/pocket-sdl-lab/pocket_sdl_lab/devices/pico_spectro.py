"""Pico H + AS7341 + WS2812B 设备类型声明（极薄委托壳）。

所有硬件协议、串口自动发现、5 秒看门狗关灯、线程锁、饱和保护与校准质量门均在
底层包 `pocket-sdl`（`pocket_sdl.device.PicoSpectroDevice`）中实现；
本文件仅保留供 Uni-Lab OS 静态扫描所需的装饰器、属性和签名。
"""

from typing import Any, Dict, List, Optional

from pocket_sdl.device import PicoSpectroDevice
from unilabos.registry.decorators import action, device, not_action, topic_config


@device(
    id="pico_spectro",
    category=["spectrometer", "optical", "self_driving_lab"],
    displayname="Pico AS7341 光谱闭环模块",
    description="基于 Raspberry Pi Pico H、WS2812B LED 与 AS7341 八通道光谱传感器的自主光学测量设备。",
    version="1.0.0",
)
class PicoSpectroStation:
    """Uni-Lab OS 设备类型：构造时仅保存参数，动作全部委托给 PicoSpectroDevice。"""

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
        self._mode_param = mode
        self._port_param = port
        self.serial_number = serial_number
        self.config_path = config_path
        self.auto_off_after_measure = auto_off_after_measure
        self.snap_to_grid = snap_to_grid
        self.probe_timeout = probe_timeout
        self._driver = PicoSpectroDevice(
            mode=mode,
            port=port,
            serial_number=serial_number,
            config_path=config_path,
            auto_off_after_measure=auto_off_after_measure,
            snap_to_grid=snap_to_grid,
            probe_timeout=probe_timeout,
        )

    # ---- 状态属性（只读内存缓存，不触发串口 I/O）----

    @property
    @topic_config(period=2.0)
    def status(self) -> str:
        return self._driver.status

    @property
    @topic_config(period=2.0)
    def connected(self) -> bool:
        return self._driver.connected

    @property
    @topic_config(period=5.0)
    def mode(self) -> str:
        return self._driver.mode

    @property
    @topic_config(period=5.0)
    def port(self) -> str:
        return self._driver.port

    @property
    @topic_config(period=10.0)
    def channels_nm(self) -> List[int]:
        return self._driver.channels_nm

    @property
    @topic_config(period=5.0)
    def calibrated(self) -> bool:
        return self._driver.calibrated

    @property
    @topic_config(period=5.0)
    def calibration_id(self) -> str:
        return self._driver.calibration_id

    @property
    @topic_config(period=10.0)
    def device_fingerprint(self) -> str:
        return self._driver.device_fingerprint

    @property
    @topic_config(period=10.0)
    def firmware_version(self) -> str:
        return self._driver.firmware_version

    @property
    @topic_config(period=2.0)
    def last_rgb(self) -> List[float]:
        return self._driver.last_rgb

    @property
    @topic_config(period=2.0)
    def last_raw(self) -> List[int]:
        return self._driver.last_raw

    @property
    @topic_config(period=2.0)
    def last_loss(self) -> Optional[float]:
        return self._driver.last_loss

    @property
    @topic_config(period=2.0)
    def last_error(self) -> str:
        return self._driver.last_error

    @property
    @topic_config(period=5.0)
    def sample_count(self) -> int:
        return self._driver.sample_count

    # ---- 对外动作 ----

    @action(description="建立与 Pico 光谱模块的硬件或仿真连接并校验配置指纹；无需参数。")
    def connect(self) -> Dict[str, Any]:
        return self._driver.connect()

    @action(description="安全关闭 LED 并断开设备连接；故障态下仍可调用。")
    def disconnect(self) -> Dict[str, Any]:
        return self._driver.disconnect()

    @action(description="安全停止动作：立即关闭 WS2812B LED；故障态下仍可调用。")
    def stop(self) -> Dict[str, Any]:
        return self._driver.stop()

    @action(description="故障复位并重新握手恢复设备（清除 Error 态）；故障态下仍可调用。")
    def reset(self) -> Dict[str, Any]:
        return self._driver.reset()

    @action(description="执行单次暗场采集与饱和自检（LED 全关，检查 AS7341 F1-F8 计数 < 60000）。")
    def doctor(self) -> Dict[str, Any]:
        return self._driver.doctor()

    @action(
        description=(
            "设置归一化 RGB 亮度并采集 AS7341 F1-F8 八通道原始光谱。"
            "参数 r, g, b 为无量纲归一化亮度，允许范围 0.0 到 1.0（默认步长 0.1，对应 LED 0 到 80）；"
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
        return self._driver.measure(r=r, g=g, b=b, auto_off=auto_off, snap_to_grid=snap_to_grid)

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
        return self._driver.calibrate(
            target_r=target_r,
            target_g=target_g,
            target_b=target_b,
            save_dir=save_dir,
            snap_to_grid=snap_to_grid,
        )

    @action(description="从磁盘加载并审计已有 calibration.json 文件到当前设备校准态；calibration_path 为文件路径。")
    def load_calibration(self, calibration_path: str) -> Dict[str, Any]:
        return self._driver.load_calibration(calibration_path=calibration_path)

    @action(description="清除设备内存中已加载的校准态。")
    def clear_calibration(self) -> Dict[str, Any]:
        return self._driver.clear_calibration()

    @not_action
    def close(self) -> None:
        self._driver.close()
