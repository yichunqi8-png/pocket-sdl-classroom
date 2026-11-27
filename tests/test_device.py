import ast
import inspect
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pocket_sdl import LabError
from pocket_sdl.config import expected_device_base, expected_device_fingerprint, load_config
from pocket_sdl.device import PicoSpectroDevice, _port_sort_key, discover_pico_port
from pocket_sdl.workflow import read_calibration
from tests.fakes import FailingDevice

REPO_ROOT = Path(__file__).resolve().parents[1]
UNILAB_PKG_ROOT = REPO_ROOT / "integrations" / "unilab" / "pocket-sdl-lab"


class FakePortInfo(SimpleNamespace):
    pass


class DeviceFacadeTests(unittest.TestCase):
    def test_init_does_not_connect_and_full_simulated_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "cal-001"
            dev = PicoSpectroDevice(mode="SIMULATED")
            # 构造期只保存参数，不连接硬件
            self.assertFalse(dev.connected)
            self.assertEqual(dev.status, "Offline")
            self.assertEqual(dev.port, "")
            with self.assertRaisesRegex(LabError, "DISCONNECTED"):
                dev.measure(0.3, 0.6, 0.4)

            conn = dev.connect()
            self.assertTrue(conn["success"])
            self.assertTrue(dev.connected)
            self.assertEqual(dev.status, "Idle")
            self.assertEqual(dev.mode, "SIMULATED")
            self.assertEqual(dev.port, "SIMULATED")
            self.assertFalse(dev.calibrated)
            self.assertEqual(dev.calibration_id, "")
            self.assertEqual(len(dev.device_fingerprint), 64)

            doc = dev.doctor()
            self.assertTrue(doc["success"])
            self.assertEqual(len(doc["data"]["dark_raw"]), 8)
            self.assertTrue(all(type(v) is int for v in doc["data"]["dark_raw"]))

            # 未校准时的单次测量（默认 auto_off=True，避免触发固件 5s 看门狗）
            m1 = dev.measure(0.3, 0.6, 0.4)
            self.assertTrue(m1["success"])
            d1 = m1["data"]
            self.assertEqual(d1["x"], [0.3, 0.6, 0.4])
            self.assertEqual(d1["led_rgb"], [24, 48, 32])
            self.assertTrue(d1["led_off"])
            self.assertFalse(d1["calibrated"])
            self.assertIsNone(d1["loss"])
            self.assertIsNone(d1["normalized"])
            self.assertTrue(all(type(v) is int for v in d1["raw"]))
            self.assertEqual(dev.last_rgb, [0.0, 0.0, 0.0])
            self.assertEqual(dev.sample_count, 1)

            # 严格 JSON 可序列化，无 numpy 类型或 NaN
            json.dumps(m1, allow_nan=False)
            json.dumps(dev.snapshot(), allow_nan=False)

            # 执行完整质量门校准并同时落盘审计
            cal = dev.calibrate(target_r=0.2, target_g=0.7, target_b=0.5, save_dir=str(save_dir))
            self.assertTrue(cal["success"])
            cal_data = cal["data"]
            self.assertTrue(dev.calibrated)
            self.assertEqual(dev.calibration_id, cal_data["calibration_id"])
            self.assertEqual(cal_data["target_x"], [0.2, 0.7, 0.5])
            self.assertTrue((save_dir / "calibration.json").exists())
            audited, _ = read_calibration(save_dir / "calibration.json")
            self.assertEqual(audited["id"], cal_data["calibration_id"])

            # 校准后测目标点，应自动带回 normalized 和接近 0 的 loss
            m2 = dev.measure_rgb([0.2, 0.7, 0.5], auto_off=False)
            self.assertTrue(m2["success"])
            d2 = m2["data"]
            self.assertTrue(d2["calibrated"])
            self.assertFalse(d2["led_off"])
            self.assertEqual(dev.last_rgb, [0.2, 0.7, 0.5])
            self.assertEqual(len(d2["normalized"]), 8)
            self.assertIsInstance(d2["loss"], float)
            self.assertLess(d2["loss"], 0.05)

            stopped = dev.stop()
            self.assertTrue(stopped["success"])
            self.assertTrue(stopped["data"]["led_off"])
            self.assertEqual(dev.last_rgb, [0.0, 0.0, 0.0])

            # 清除并重新从文件加载校准
            cleared = dev.clear_calibration()
            self.assertTrue(cleared["success"])
            self.assertFalse(dev.calibrated)
            reloaded = dev.load_calibration(str(save_dir / "calibration.json"))
            self.assertTrue(reloaded["success"])
            self.assertTrue(dev.calibrated)
            self.assertEqual(reloaded["data"]["calibration_id"], cal_data["calibration_id"])

            disc = dev.disconnect()
            self.assertTrue(disc["success"])
            self.assertFalse(dev.connected)
            self.assertFalse(dev.calibrated)
            self.assertIsNone(dev.last_loss)
            self.assertEqual(dev.status, "Offline")

    def test_grid_validation_and_optional_snap(self):
        with PicoSpectroDevice(mode="SIMULATED", snap_to_grid=False) as dev:
            with self.assertRaisesRegex(LabError, "INVALID_INPUT"):
                dev.measure(0.24, 0.5, 0.5)
            snapped = dev.measure(0.24, 0.56, 0.91, snap_to_grid=True)
            self.assertTrue(snapped["success"])
            self.assertEqual(snapped["data"]["x"], [0.2, 0.6, 0.9])
            for bad in ((-0.1, 0.0, 0.0), (1.1, 0.0, 0.0), (True, 0.0, 0.0), (float("nan"), 0.0, 0.0)):
                with self.subTest(bad=bad), self.assertRaisesRegex(LabError, "INVALID_INPUT"):
                    dev.measure(*bad, snap_to_grid=True)

    def test_simulated_rejects_explicit_port(self):
        with self.assertRaisesRegex(LabError, "CONFIG_INVALID"):
            PicoSpectroDevice(mode="SIMULATED", port="/dev/ttyACM1")

    def test_partial_target_rgb_rejected(self):
        with PicoSpectroDevice(mode="SIMULATED") as dev:
            with self.assertRaisesRegex(LabError, "INVALID_INPUT"):
                dev.calibrate(target_r=0.3)

    def test_saturation_fault_blocks_regular_actions_until_reset(self):
        config = load_config("configs/simulated.json")
        failing = FailingDevice(config, error_code="SATURATED", fail_at=1)
        dev = PicoSpectroDevice(mode="SIMULATED")
        dev._attach_device(failing)
        dev.connect()

        with self.assertRaisesRegex(LabError, "SATURATED"):
            dev.measure(0.5, 0.5, 0.5)
        self.assertEqual(dev.status, "Error")
        self.assertIn("SATURATED", dev.last_error)
        self.assertGreaterEqual(failing.off_count, 1)
        self.assertEqual(dev.sample_count, 0)

        # 故障态阻塞常规动作，但允许 stop 与 reset
        with self.assertRaisesRegex(LabError, "DEVICE_FAULT"):
            dev.measure(0.1, 0.1, 0.1)
        with self.assertRaisesRegex(LabError, "DEVICE_FAULT"):
            dev.doctor()
        self.assertTrue(dev.stop()["success"])
        self.assertEqual(dev.status, "Error")

        rec = dev.reset()
        self.assertTrue(rec["success"])
        self.assertEqual(dev.status, "Idle")
        self.assertEqual(dev.last_error, "")
        dev.close()

    def test_timeout_fault_recovery(self):
        config = load_config("configs/simulated.json")
        failing = FailingDevice(config, error_code="SERIAL_TIMEOUT", fail_at=1)
        dev = PicoSpectroDevice(mode="SIMULATED")
        dev._attach_device(failing)
        dev.connect()

        with self.assertRaisesRegex(LabError, "SERIAL_TIMEOUT"):
            dev.measure(0.3, 0.3, 0.3)
        self.assertEqual(dev.status, "Error")
        self.assertTrue(dev.stop()["success"])
        self.assertTrue(dev.reset()["success"])
        self.assertEqual(dev.status, "Idle")
        dev.close()

    def test_concurrent_state_reads_during_measurements(self):
        with PicoSpectroDevice(mode="SIMULATED") as dev:
            errors = []

            def reader():
                try:
                    for _ in range(30):
                        state = dev.snapshot()
                        self.assertIn(state["status"], ("Idle", "Running", "Error", "Offline", "Unknown"))
                        json.dumps(state, allow_nan=False)
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=reader)
            thread.start()
            for i in range(10):
                val = (i % 10) / 10.0
                dev.measure(val, 0.5, 0.2)
            thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(dev.sample_count, 10)


class PortDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config("configs/physical.json")

    def test_port_sorting_prefers_pico_higher_data_port(self):
        ports = [
            FakePortInfo(device="/dev/ttyUSB0", vid=0x10C4, description="CP2102 UART", hwid="", serial_number="OTHER"),
            FakePortInfo(device="/dev/ttyACM0", vid=0x2E8A, description="CircuitPython Console", hwid="", serial_number="PICO1"),
            FakePortInfo(device="/dev/ttyACM1", vid=0x2E8A, description="CircuitPython CDC", hwid="", serial_number="PICO1"),
            FakePortInfo(device="/dev/ttyACM10", vid=0x2E8A, description="CircuitPython CDC Data", hwid="", serial_number="PICO2"),
        ]
        ordered = [p.device for p in sorted(ports, key=_port_sort_key)]
        self.assertEqual(ordered, ["/dev/ttyACM10", "/dev/ttyACM1", "/dev/ttyACM0", "/dev/ttyUSB0"])

    def test_discover_skips_console_and_finds_data_port(self):
        ports = [
            FakePortInfo(device="/dev/cu.usbmodem14201", vid=0x2E8A, description="Pico", hwid="", serial_number="SN-A"),
            FakePortInfo(device="/dev/cu.usbmodem14203", vid=0x2E8A, description="Pico", hwid="", serial_number="SN-A"),
        ]
        probed = []
        valid_info = expected_device_base(self.config)
        valid_info.update(
            device_fingerprint=expected_device_fingerprint(self.config),
            firmware_version="pico-h-sdl-1.0.0",
        )

        class StubSerialDevice:
            def __init__(self, port, config, hello_timeout=5.0):
                probed.append((port, hello_timeout))
                if port.endswith("14201"):
                    raise LabError("SERIAL_TIMEOUT", "console port does not speak JSON")
                self.port_name = port
                self.device_info = valid_info
                self.closed = False

            def off(self):
                return True

            def close(self):
                self.closed = True

        with (
            patch("serial.tools.list_ports.comports", return_value=ports),
            patch("pocket_sdl.transport.SerialDevice", StubSerialDevice),
        ):
            port, dev = discover_pico_port(self.config, serial_number="SN-A", probe_timeout=1.2)
            self.assertEqual(port, "/dev/cu.usbmodem14203")
            self.assertEqual(probed, [("/dev/cu.usbmodem14203", 1.2)])
            dev.close()

    def test_discover_fails_cleanly_when_no_port_responds(self):
        ports = [
            FakePortInfo(device="/dev/ttyACM0", vid=0x2E8A, description="Pico", hwid="", serial_number="SN-X"),
        ]

        class DeadSerialDevice:
            def __init__(self, port, config, hello_timeout=5.0):
                raise LabError("SERIAL_TIMEOUT", "no reply")

        with (
            patch("serial.tools.list_ports.comports", return_value=ports),
            patch("pocket_sdl.transport.SerialDevice", DeadSerialDevice),
            self.assertRaisesRegex(LabError, "SERIAL_IO"),
        ):
            discover_pico_port(self.config, serial_number="SN-X")


class UniLabPackageContractTests(unittest.TestCase):
    def test_unilab_package_ast_and_graphs_contract(self):
        device_file = UNILAB_PKG_ROOT / "pocket_sdl_lab" / "devices" / "pico_spectro.py"
        tree = ast.parse(device_file.read_text(encoding="utf-8"))

        # 验证顶层导入 unilabos.registry.decorators
        imports = [
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "unilabos.registry.decorators"
        ]
        self.assertEqual(len(imports), 1)
        imported_names = {alias.name for alias in imports[0].names}
        self.assertTrue({"device", "action", "not_action", "topic_config"} <= imported_names)

        # 验证类装饰器与所有公开方法装饰器（不允许遗漏产生意外 auto-* 方法，也不允许无参 get_*）
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual(len(classes), 1)
        cls_node = classes[0]
        self.assertEqual(cls_node.name, "PicoSpectroStation")

        init_params = []
        for item in cls_node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            if item.name == "__init__":
                init_params = [arg.arg for arg in item.args.args if arg.arg != "self"]
                continue
            if item.name.startswith("_"):
                continue
            self.assertFalse(item.name.startswith("get_"), f"应优先使用 @property+@topic_config 而非 {item.name}")
            decorator_names = [
                dec.func.id if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                else (dec.id if isinstance(dec, ast.Name) else "")
                for dec in item.decorator_list
            ]
            if "property" in decorator_names:
                # 规则：@topic_config 必须写在 @property 下方
                self.assertEqual(decorator_names, ["property", "topic_config"])
            else:
                self.assertTrue(
                    "action" in decorator_names or "not_action" in decorator_names,
                    f"公开方法 {item.name} 必须显式标注 @action 或 @not_action",
                )

        # 验证启动图 config 键与 __init__ 参数一一对应
        for graph_name in ("dry-run.json", "production.json"):
            graph = json.loads((UNILAB_PKG_ROOT / "deployment" / "graphs" / graph_name).read_text(encoding="utf-8"))
            self.assertEqual(len(graph["nodes"]), 1)
            node = graph["nodes"][0]
            self.assertEqual(node["class"], "community.pocket_sdl_lab.pico_spectro")
            self.assertEqual(set(node["config"].keys()), set(init_params))

    def test_unilab_station_runtime_with_mocked_decorators(self):
        # 注入最小桩模块，验证 pocket_sdl_lab.devices.PicoSpectroStation 在无 unilabos 环境下也能通过端到端测试
        fake_unilabos = types.ModuleType("unilabos")
        fake_registry = types.ModuleType("unilabos.registry")
        fake_decorators = types.ModuleType("unilabos.registry.decorators")
        fake_decorators.device = lambda **kwargs: (lambda cls: cls)
        fake_decorators.action = lambda **kwargs: (lambda fn: fn)
        fake_decorators.not_action = lambda fn: fn
        fake_decorators.topic_config = lambda **kwargs: (lambda fn: fn)

        with patch.dict(
            sys.modules,
            {
                "unilabos": fake_unilabos,
                "unilabos.registry": fake_registry,
                "unilabos.registry.decorators": fake_decorators,
            },
        ):
            sys.path.insert(0, str(UNILAB_PKG_ROOT))
            try:
                from pocket_sdl_lab.devices import PicoSpectroStation

                station = PicoSpectroStation(mode="SIMULATED")
                self.assertEqual(station.status, "Offline")
                self.assertFalse(station.connected)
                self.assertEqual(station.mode, "SIMULATED")
                self.assertEqual(station.port, "")
                self.assertEqual(station.channels_nm, [415, 445, 480, 515, 555, 590, 630, 680])
                self.assertTrue(station.connect()["success"])
                self.assertEqual(station.status, "Idle")
                res = station.measure(r=0.3, g=0.6, b=0.4)
                self.assertTrue(res["success"])
                self.assertTrue(res["data"]["led_off"])
                self.assertEqual(inspect.signature(PicoSpectroStation.__init__), inspect.signature(PicoSpectroDevice.__init__))
                station.close()
                self.assertEqual(station.status, "Offline")
            finally:
                sys.path.remove(str(UNILAB_PKG_ROOT))
                for mod in list(sys.modules):
                    if mod == "pocket_sdl_lab" or mod.startswith("pocket_sdl_lab."):
                        del sys.modules[mod]


if __name__ == "__main__":
    unittest.main()
