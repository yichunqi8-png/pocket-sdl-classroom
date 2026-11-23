"""端到端 CLI 测试，使用明确的模拟配置和新临时目录。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_full_simulated_cli_and_failure_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def cli(*args, expected=0):
                process = subprocess.run(
                    [sys.executable, "-m", "pocket_sdl", *args], cwd=ROOT,
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(process.returncode, expected, process.stderr + process.stdout)
                return process

            cal = root / "cal"
            prepared = json.loads(cli("prepare", "--config", "configs/simulated.json", "--out", str(cal)).stdout)
            self.assertEqual(prepared["mode"], "SIMULATED")
            runs = {}
            for strategy in ("random", "bo"):
                out = root / strategy
                runs[strategy] = out
                result = json.loads(cli("run", "--calibration", str(cal / "calibration.json"), "--strategy", strategy, "--out", str(out)).stdout)
                self.assertEqual(result["valid_measurements"], 24)
                confirmation = json.loads(cli("confirm", "--run", str(out), "--out", str(root / (strategy + "-confirm"))).stdout)
                self.assertEqual(confirmation["x"], result["best_x"])
            first = [json.loads(line)["candidate_index"] for line in (runs["random"] / "measurements.jsonl").read_text().splitlines()[:4]]
            second = [json.loads(line)["candidate_index"] for line in (runs["bo"] / "measurements.jsonl").read_text().splitlines()[:4]]
            self.assertEqual(first, second)
            cli("run", "--calibration", str(cal / "calibration.json"), "--strategy", "bo", "--out", str(runs["bo"]), expected=1)


if __name__ == "__main__":
    unittest.main()
