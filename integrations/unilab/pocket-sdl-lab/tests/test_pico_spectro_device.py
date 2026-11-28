"""设备包契约与异常测试：覆盖正常执行、通信超时和饱和故障及恢复。"""

import json
from pathlib import Path

import pytest

from pocket_sdl_lab._vendor.pocket_sdl import LabError
from pocket_sdl_lab._vendor.pocket_sdl.config import load_config
from pocket_sdl_lab._vendor.pocket_sdl.simulated import SimDevice

from pocket_sdl_lab.devices import PicoSpectroStation


class TimeoutDevice(SimDevice):
    def __init__(self, config):
        super().__init__(config, fail_at=1)


class SaturatedDevice(SimDevice):
    def __init__(self, config):
        super().__init__(config, saturated_at=1)


def test_construct_does_not_connect_and_normal_flow():
    station = PicoSpectroStation(mode="SIMULATED")
    # 构造时不连接硬件
    assert station.connected is False
    assert station.status == "Offline"

    res_conn = station.connect()
    assert res_conn["success"] is True
    assert station.connected is True
    assert station.status == "Idle"

    res_doc = station.doctor()
    assert res_doc["success"] is True
    assert len(res_doc["data"]["dark_raw"]) == 8

    res_cal = station.calibrate(target_r=0.3, target_g=0.6, target_b=0.4)
    assert res_cal["success"] is True
    assert station.calibrated is True

    res_meas = station.measure(r=0.3, g=0.6, b=0.4)
    assert res_meas["success"] is True
    assert res_meas["data"]["led_off"] is True
    assert res_meas["data"]["loss"] < 0.05
    json.dumps(res_meas, allow_nan=False)

    res_disc = station.disconnect()
    assert res_disc["success"] is True
    assert station.status == "Offline"


def test_timeout_enters_error_and_allows_stop_and_reset():
    station = PicoSpectroStation(mode="SIMULATED")
    cfg = load_config({"mode": "SIMULATED"})
    timeout_dev = TimeoutDevice(cfg)
    station._driver._attach_device(timeout_dev)

    station.connect()
    with pytest.raises(LabError, match="SERIAL_TIMEOUT"):
        station.measure(r=0.5, g=0.5, b=0.5)

    assert station.status == "Error"
    assert "SERIAL_TIMEOUT" in station.last_error
    # 故障态阻塞常规测量
    with pytest.raises(LabError, match="DEVICE_FAULT"):
        station.measure(r=0.1, g=0.1, b=0.1)

    # 停止与复位在故障态下仍可调用
    assert station.stop()["success"] is True
    assert station.reset()["success"] is True
    assert station.status == "Idle"
    station.close()


def test_saturation_fault_propagates_and_turns_off_led():
    station = PicoSpectroStation(mode="SIMULATED")
    cfg = load_config({"mode": "SIMULATED"})
    sat_dev = SaturatedDevice(cfg)
    station._driver._attach_device(sat_dev)

    station.connect()
    with pytest.raises(LabError, match="SATURATED"):
        station.measure(r=1.0, g=1.0, b=1.0)

    assert station.status == "Error"
    assert sat_dev.off_count >= 1
    assert station.sample_count == 0
    station.close()


def test_graphs_match_device_class_and_init_params():
    root = Path(__file__).resolve().parents[1]
    for name in ("dry-run.json", "production.json"):
        graph = json.loads((root / "deployment" / "graphs" / name).read_text(encoding="utf-8"))
        assert len(graph["nodes"]) == 1
        node = graph["nodes"][0]
        assert node["class"] == "community.pocket_sdl_lab.pico_spectro"
        # 验证启动图 config 可直接实例化且不会连接硬件
        station = PicoSpectroStation(**node["config"])
        assert station.status == "Offline"
        assert station.connected is False
