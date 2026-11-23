import tempfile
import unittest
from pathlib import Path

from pocket_sdl import LabError
from pocket_sdl.config import load_config


class ConfigTests(unittest.TestCase):
    def test_path_and_dictionary_configs(self):
        physical = load_config("configs/physical.json")
        simulated = load_config("configs/simulated.json")
        self.assertEqual(physical["mode"], "PHYSICAL")
        self.assertNotIn("simulation", physical)
        self.assertEqual(simulated["simulation"]["noise_sd"], 2.0)
        self.assertEqual(load_config({"mode": "SIMULATED"})["simulation"]["seed"], 20261021)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text('{"mode":"SIMULATED"}')
            self.assertEqual(load_config(path)["mode"], "SIMULATED")

    def test_invalid_json_and_unknown_keys(self):
        for value in ({}, {"mode": "INVALID"}, [], {"mode": "PHYSICAL", "simulation": {}}, {"mode": "PHYSICAL", "seed": 7, "new": 1}):
            with self.subTest(value=value), self.assertRaises(LabError):
                load_config(value)


if __name__ == "__main__":
    unittest.main()
