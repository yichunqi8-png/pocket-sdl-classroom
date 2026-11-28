"""Pico H + AS7341 + WS2812B 设备类型声明（极薄委托壳）。

所有硬件协议、串口自动发现、5 秒看门狗关灯、线程锁、饱和保护与校准质量门均在
底层包 `pocket-sdl`（`pocket_sdl.device.PicoSpectroDevice`）中实现；
本文件仅保留供 Uni-Lab OS 静态扫描所需的装饰器、属性和签名。
"""

from typing import Any, Dict, List, Optional, TypedDict

from pocket_sdl_lab._vendor.pocket_sdl.device import PicoSpectroDevice
from unilabos.registry.decorators import action, device, not_action, topic_config


class ActionResult(TypedDict):
    """设备动作的稳定返回契约。

    工作流按这里的字段名解析动作连接点（例如 `node.success`）；字段改名或删除会
    让已发布工作流无法绑定，因此保持 `success` 与 `message` 始终存在。
    """

    success: bool
    message: str


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
        """设备状态：Offline / Idle / Running / Error / Unknown。"""

        return self._driver.status

    @property
    @topic_config(period=2.0)
    def connected(self) -> bool:
        """是否已连接：true 表示串口或仿真会话已建立。"""

        return self._driver.connected

    @property
    @topic_config(period=5.0)
    def mode(self) -> str:
        """运行模式：PHYSICAL 接真实硬件，SIMULATED 为软件仿真。"""

        return self._driver.mode

    @property
    @topic_config(period=5.0)
    def port(self) -> str:
        """当前使用的串口设备名；仿真模式显示 SIMULATED。"""

        return self._driver.port

    @property
    @topic_config(period=10.0)
    def channels_nm(self) -> List[int]:
        """八通道对应波长（nm）：415/445/480/515/555/590/630/680。"""

        return self._driver.channels_nm

    @property
    @topic_config(period=5.0)
    def calibrated(self) -> bool:
        """是否已加载有效校准。"""

        return self._driver.calibrated

    @property
    @topic_config(period=5.0)
    def calibration_id(self) -> str:
        """当前校准标识；未校准时为空字符串。"""

        return self._driver.calibration_id

    @property
    @topic_config(period=10.0)
    def device_fingerprint(self) -> str:
        """设备与配置指纹，用于确认连到的是同一台设备。"""

        return self._driver.device_fingerprint

    @property
    @topic_config(period=10.0)
    def firmware_version(self) -> str:
        """Pico 固件版本号。"""

        return self._driver.firmware_version

    @property
    @topic_config(period=2.0)
    def last_rgb(self) -> List[float]:
        """最近一次测量的归一化 RGB 输入。"""

        return self._driver.last_rgb

    @property
    @topic_config(period=2.0)
    def last_raw(self) -> List[int]:
        """最近一次测量的八通道原始计数，顺序同 channels_nm。"""

        return self._driver.last_raw

    @property
    @topic_config(period=2.0)
    def last_loss(self) -> Optional[float]:
        """最近一次测量的相对损耗；未校准时为 null。"""

        return self._driver.last_loss

    @property
    @topic_config(period=2.0)
    def last_error(self) -> str:
        """最近一次错误的分类码与说明；无错误时为空字符串。"""

        return self._driver.last_error

    @property
    @topic_config(period=5.0)
    def sample_count(self) -> int:
        """本次连接以来已完成的测量次数。"""

        return self._driver.sample_count

    # ---- 对外动作 ----

    @action(
        displayname="查看串口",
        description=(
            "列出本机可用串口及其描述、厂商与序列号，供选择真实硬件端口；"
            "不会打开或占用任何串口。"
        ),
    )
    def list_ports(self) -> ActionResult:
        from pocket_sdl_lab._vendor.pocket_sdl.device import list_serial_ports

        ports = list_serial_ports()
        return {
            "success": True,
            "message": f"发现 {len(ports)} 个串口",
            "data": {"mode": self._driver.mode, "ports": ports},
        }

    @action(
        displayname="连接设备",
        description=(
            "建立与 Pico 光谱模块的硬件或仿真连接并校验配置指纹。"
            "port 可选：留空或填 auto 表示自动探测 Pico 数据口；"
            "PHYSICAL 模式下也可填具体串口设备名（先用「查看串口」确认）。"
            "必须先断开才能切换串口。"
        ),
    )
    def connect(self, port: Optional[str] = None) -> ActionResult:
        """建立与 Pico 光谱模块的连接。

        Args:
            port[串口]: 留空或填 auto 自动探测 Pico 数据口；也可填具体设备名（先用 list_ports 查看），PHYSICAL 模式才允许指定。
        """

        if port not in (None, "", "auto"):
            self._driver.set_port(port)
        return self._driver.connect()

    @action(displayname="断开设备", description="安全关闭 LED 并断开设备连接；故障态下仍可调用。")
    def disconnect(self) -> ActionResult:
        return self._driver.disconnect()

    @action(displayname="关灯", description="安全停止动作：立即关闭 WS2812B LED；故障态下仍可调用。")
    def stop(self) -> ActionResult:
        return self._driver.stop()

    @action(displayname="故障复位", description="故障复位并重新握手恢复设备（清除 Error 态）；故障态下仍可调用。")
    def reset(self) -> ActionResult:
        return self._driver.reset()

    @action(displayname="暗场自检", description="执行单次暗场采集与饱和自检（LED 全关，检查 AS7341 F1-F8 计数 < 60000）。")
    def doctor(self) -> ActionResult:
        return self._driver.doctor()

    @action(
        displayname="测量光谱",
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
    ) -> ActionResult:
        """点亮指定颜色的 LED 并采集 AS7341 八通道光谱。

        Args:
            r[红色亮度]: 红光归一化亮度，0 到 1（0.1 一档，对应 LED 0 到 80）。
            g[绿色亮度]: 绿光归一化亮度，0 到 1（0.1 一档，对应 LED 0 到 80）。
            b[蓝色亮度]: 蓝光归一化亮度，0 到 1（0.1 一档，对应 LED 0 到 80）。
            auto_off[测完关灯]: 为 true 时采集后立即关灯，避免触发固件 5 秒看门狗；默认 true。
            snap_to_grid[吸附到 0.1 档]: 为 true 时把亮度吸附到最近 0.1 网格；默认 false。
        """

        return self._driver.measure(r=r, g=g, b=b, auto_off=auto_off, snap_to_grid=snap_to_grid)

    @action(
        displayname="校准",
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
    ) -> ActionResult:
        """执行暗场、亮参考、目标与稳定性复测校准。

        Args:
            target_r[目标红色]: 目标颜色红光分量，0 到 1；三个目标分量必须同时提供或同时省略。
            target_g[目标绿色]: 目标颜色绿光分量，0 到 1。
            target_b[目标蓝色]: 目标颜色蓝光分量，0 到 1。
            save_dir[落盘目录]: 可选新建目录路径；提供时同步写出完整审计账本。
            snap_to_grid[吸附到 0.1 档]: 为 true 时把目标分量吸附到最近 0.1 网格。
        """

        return self._driver.calibrate(
            target_r=target_r,
            target_g=target_g,
            target_b=target_b,
            save_dir=save_dir,
            snap_to_grid=snap_to_grid,
        )

    @action(displayname="加载校准", description="从磁盘加载并审计已有校准文件到当前设备校准态。")
    def load_calibration(self, calibration_path: str) -> ActionResult:
        """从磁盘加载并审计已有校准文件。

        Args:
            calibration_path[校准文件]: 已有 calibration.json 文件的完整路径。
        """

        return self._driver.load_calibration(calibration_path=calibration_path)

    @action(displayname="清除校准", description="清除设备内存中已加载的校准态。")
    def clear_calibration(self) -> ActionResult:
        return self._driver.clear_calibration()

    @not_action
    def close(self) -> None:
        self._driver.close()
