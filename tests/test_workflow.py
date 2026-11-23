import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pocket_sdl import LabError
from pocket_sdl.config import load_config
from pocket_sdl.simulated import SimDevice
from pocket_sdl.workflow import confirm, prepare, run

from tests.fakes import CorruptDevice, FailingDevice


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = load_config("configs/simulated.json")
        self.cal_path = self.root / "cal" / "calibration.json"
        prepare(self.config, self.root / "cal")

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_and_existing_output_rejected(self):
        calibration = json.loads(self.cal_path.read_text())
        self.assertEqual(calibration["mode"], "SIMULATED")
        self.assertEqual(len(calibration["raw_samples"]["dark"]), 3)
        self.assertEqual(len(calibration["raw_samples"]["stability"]), 5)
        self.assertLess(calibration["stability"]["rms_relative"], 0.05)
        with self.assertRaisesRegex(LabError, "OUTPUT_EXISTS"):
            prepare(self.config, self.root / "cal")

    def test_equal_initial_candidates_and_complete_ledgers(self):
        random_result = run(self.cal_path, "random", self.root / "random")
        bo_result = run(self.cal_path, "bo", self.root / "bo")
        rows = []
        for name in ("random", "bo"):
            path = self.root / name
            data = [json.loads(line) for line in (path / "measurements.jsonl").read_text().splitlines()]
            self.assertEqual(len(data), 24)
            self.assertTrue((path / "trials.csv").exists())
            self.assertFalse((path / "error.json").exists())
            self.assertEqual(json.loads((path / "status.json").read_text())["status"], "COMPLETED")
            self.assertEqual((path / "calibration.json").read_bytes(), self.cal_path.read_bytes())
            self.assertEqual(len({row["candidate_index"] for row in data}), 24)
            rows.append(data)
        self.assertEqual([r["candidate_index"] for r in rows[0][:4]], [r["candidate_index"] for r in rows[1][:4]])
        self.assertEqual([r["x"] for r in rows[0][:4]], [r["x"] for r in rows[1][:4]])
        self.assertEqual([r["raw"] for r in rows[0][:4]], [r["raw"] for r in rows[1][:4]])
        confirmation = confirm(self.root / "bo", self.root / "confirm")
        self.assertEqual(confirmation["x"], bo_result["best_x"])
        lines = (self.root / "confirm" / "confirmations.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(not json.loads(line)["part_of_strategy_budget"] for line in lines))
        self.assertEqual(len([json.loads(line) for line in (self.root / "bo" / "measurements.jsonl").read_text().splitlines()]), 24)
        with self.assertRaises(LabError):
            confirm(self.root / "bo", self.root / "confirm")

    def test_failure_stops_and_does_not_continue_training(self):
        dev = FailingDevice(self.config, fail_at=5)
        with self.assertRaises(LabError):
            run(self.cal_path, "bo", self.root / "failed", device=dev)
        self.assertTrue(dev.closed)
        self.assertGreaterEqual(dev.off_count, 1)
        attempts = [json.loads(line) for line in (self.root / "failed" / "attempts.jsonl").read_text().splitlines()]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["iteration"], 5)
        valid = [json.loads(line) for line in (self.root / "failed" / "measurements.jsonl").read_text().splitlines()]
        self.assertEqual(len(valid), 4)
        self.assertEqual(len(dev.measurements), 4)
        self.assertFalse((self.root / "failed" / "summary.json").exists())
        with self.assertRaises(LabError):
            confirm(self.root / "failed", self.root / "failed-confirm")

    def test_saturation_is_not_zero_or_training_sample(self):
        dev = FailingDevice(self.config, error_code="SATURATED", fail_at=2)
        with self.assertRaisesRegex(LabError, "SATURATED"):
            run(self.cal_path, "bo", self.root / "saturated", device=dev)
        valid = [json.loads(line) for line in (self.root / "saturated" / "measurements.jsonl").read_text().splitlines()]
        self.assertEqual(len(valid), 1)
        self.assertEqual(json.loads((self.root / "saturated" / "error.json").read_text())["code"], "SATURATED")

    def test_corrupt_data(self):
        with self.assertRaisesRegex(LabError, "SENSOR_IO"):
            run(self.cal_path, "bo", self.root / "corrupt", device=CorruptDevice(self.config))
        self.assertFalse((self.root / "corrupt" / "measurements.jsonl").exists())

    def test_numerical_failure_after_valid_measure_stops_training(self):
        # 故障要覆盖第 5 次 valid measurement 之后的 GP 拟合阶段。
        dev = SimDevice(self.config)
        with patch("numpy.linalg.cholesky", side_effect=np.linalg.LinAlgError("injected")):
            with self.assertRaisesRegex(LabError, "NUMERICAL_ERROR"):
                run(self.cal_path, "bo", self.root / "numerical", device=dev)
        valid = [json.loads(line) for line in (self.root / "numerical" / "measurements.jsonl").read_text().splitlines()]
        self.assertEqual(len(valid), 4)
        self.assertTrue(dev.closed)

    def test_physical_simulated_cannot_mix(self):
        with self.assertRaises(LabError):
            run(self.cal_path, "bo", self.root / "physical", port="TEST")


if __name__ == "__main__":
    unittest.main()
