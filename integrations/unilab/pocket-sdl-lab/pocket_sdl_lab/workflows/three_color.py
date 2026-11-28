"""Pocket SDL 三色光谱测量工作流（Uni-Lab OS 静态创作 DSL）。

三组 RGB 都是工作流入口参数，可直接在控制台修改；每次 measure 返回八通道
原始读数，作为工作流输出展示，不再由设备动作写文件。

先使用 SIMULATED 设备验证链路；真机运行前必须关闭其他占用串口的程序，
并把 port 填成 list_ports 查到的数据口（留空则自动探测）。
"""

from typing import Annotated, TypedDict

from pydantic import Field

from pocket_sdl_lab.devices.pico_spectro import PicoSpectroStation
from unilabos.workflow.authoring import device, workflow


class ThreeColorResult(TypedDict):
    success: bool
    message: str


pico: PicoSpectroStation = device("pico_spectro_01")


@workflow(
    workflow_uuid="aad9d030-d6b7-4183-9681-92efb29fcd51",
    displayname="Pocket SDL 三色光谱测量",
    description=(
        "连接 → 自检 → 三组 RGB 测量 → 关灯 → 断开。"
        "三组 RGB 均为入口参数，范围 0–1；每次测完立即关灯。失败不补零。"
    ),
)
def three_color(
    *,
    port: Annotated[
        str,
        Field(title="串口", description="留空或填 auto 自动探测 Pico 数据口"),
    ] = "auto",
    r1: Annotated[float, Field(title="第1组 红", description="红光亮度 0–1", ge=0, le=1)] = 1.0,
    g1: Annotated[float, Field(title="第1组 绿", description="绿光亮度 0–1", ge=0, le=1)] = 0.0,
    b1: Annotated[float, Field(title="第1组 蓝", description="蓝光亮度 0–1", ge=0, le=1)] = 0.0,
    r2: Annotated[float, Field(title="第2组 红", description="红光亮度 0–1", ge=0, le=1)] = 0.0,
    g2: Annotated[float, Field(title="第2组 绿", description="绿光亮度 0–1", ge=0, le=1)] = 1.0,
    b2: Annotated[float, Field(title="第2组 蓝", description="蓝光亮度 0–1", ge=0, le=1)] = 0.0,
    r3: Annotated[float, Field(title="第3组 红", description="红光亮度 0–1", ge=0, le=1)] = 0.0,
    g3: Annotated[float, Field(title="第3组 绿", description="绿光亮度 0–1", ge=0, le=1)] = 0.0,
    b3: Annotated[float, Field(title="第3组 蓝", description="蓝光亮度 0–1", ge=0, le=1)] = 1.0,
) -> ThreeColorResult:
    # [连接] 按 port 建立连接；auto 自动探测 Pico 数据口
    # unilab:node_uuid=bb6e8dfd-6495-4c77-af87-eed8720b1d15
    connected = pico.connect(port=port)
    # [自检] 暗场采集与饱和自检，失败时关灯并断开
    # unilab:node_uuid=cbb6dbef-73fc-4525-921b-22e15fe512f5
    checked = pico.doctor()
    # [测第 1 组] 按入口参数 r1/g1/b1 采集八通道光谱
    # unilab:node_uuid=f3a6b44d-3680-4ee4-a87f-a38e49add353
    first = pico.measure(r=r1, g=g1, b=b1)
    # [测第 2 组] 按入口参数 r2/g2/b2 采集八通道光谱
    # unilab:node_uuid=39f2cc28-e5f4-4fbe-b320-4f334a1ea716
    second = pico.measure(r=r2, g=g2, b=b2)
    # [测第 3 组] 按入口参数 r3/g3/b3 采集八通道光谱
    # unilab:node_uuid=2b413db0-d9a0-4ab3-a6ba-6ea9e147cef3
    third = pico.measure(r=r3, g=g3, b=b3)
    # [关灯] 立即关闭 WS2812B，避免触发固件 5 秒看门狗
    # unilab:node_uuid=b7025eea-fe61-4a2d-94c4-2de2614beaa7
    stopped = pico.stop()
    # [断开] 关闭串口并写入结束状态
    # unilab:node_uuid=d52ad180-124b-434d-a2ad-79d13e006a29
    disconnected = pico.disconnect()
    return {
        "success": third.success,
        "message": third.message,
    }
