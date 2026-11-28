import math
import unittest

import numpy as np

from pocket_sdl import LabError
from pocket_sdl.config import load_config
from pocket_sdl.core import CHANNELS_NM, GRID, calibration_stats, led_values, relative_stability, spectral_loss, validate_raw, validate_x
from pocket_sdl.simulated import SimDevice


class CoreTests(unittest.TestCase):
    def test_grid_is_stable_and_target_mapping(self):
        self.assertEqual(GRID.shape, (1331, 3))
        self.assertEqual(GRID[0].tolist(), [0, 0, 0])
        self.assertEqual(GRID[665].tolist(), [0.5, 0.5, 0.5])
        self.assertEqual(GRID[1330].tolist(), [1, 1, 1])
        self.assertEqual(led_values([0.3, 0.6, 0.4]), [24, 48, 32])
        self.assertEqual(len({tuple(row) for row in GRID}), 1331)

    def test_invalid_input_is_not_clipped(self):
        for value in ([1.2, 0, 0], [0.33, 0, 0], [float("nan"), 0, 0], [True, 0, 0], [0, 0]):
            with self.subTest(value=value), self.assertRaises(LabError):
                validate_x(value)

    def test_saturation_boundary(self):
        np.testing.assert_array_equal(validate_raw([59999] * 8), [59999] * 8)
        for value in (60000, 65535):
            raw = [0] * 8
            raw[7] = value
            with self.assertRaisesRegex(LabError, "SATURATED"):
                validate_raw(raw)

    def test_loss_keeps_brightness(self):
        dark = np.zeros(8)
        target = np.ones(8)
        self.assertAlmostEqual(spectral_loss(target, dark, 2, target), 0)
        self.assertAlmostEqual(spectral_loss(np.zeros(8), dark, 2, target), math.sqrt(8) / 2)
        self.assertAlmostEqual(spectral_loss(np.full(8, 3), dark, 2, target), math.sqrt(8))
        with self.assertRaises(LabError):
            spectral_loss(np.ones(8), dark, 0, target)

    def test_dark_gate_ddof_and_boundary(self):
        dark = [[0] * 8, [2] * 8]
        # 通道 sd = sqrt(2)，总噪声 = 4；参考-暗场=[40,-1,…,-1]。
        result = calibration_stats(dark, [[41] + [0] * 7])
        self.assertAlmostEqual(result["scale"], math.sqrt(40**2 + 7))
        self.assertAlmostEqual(result["dark_noise"], 4.0)
        with self.assertRaises(LabError):
            calibration_stats(dark, [[40] + [0] * 7])
        self.assertEqual(calibration_stats([[1] * 8] * 3, [[11] + [1] * 7])["dark_noise"], 1)

    def test_repeatability_formula_uses_corrected_vector(self):
        raw = [[100 + delta, 200] + [10] * 6 for delta in (0, 1, 2, 3, 4)]
        result = relative_stability(raw, [0] * 8)
        corrected = np.asarray(raw, dtype=float)
        mean = corrected.mean(axis=0)
        self.assertAlmostEqual(result["rms"], math.sqrt(2))
        self.assertAlmostEqual(result["rms_relative"], math.sqrt(2) / np.linalg.norm(mean))
        zero = relative_stability([[0] * 8, [1] + [0] * 7], [0] * 8)
        self.assertAlmostEqual(zero["rms"], 0.5)

    def test_simulated_rgb_spectral_peaks(self):
        dev = SimDevice(load_config({"mode": "SIMULATED"}))
        try:
            for rgb, expected_nm in (([1, 0, 0], 630), ([0, 1, 0], 515), ([0, 0, 1], 480)):
                raw = dev.measure(rgb)
                self.assertEqual(CHANNELS_NM[int(np.argmax(raw))], expected_nm)
        finally:
            dev.close()


if __name__ == "__main__":
    unittest.main()
