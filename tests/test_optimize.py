import math
import unittest
from unittest.mock import patch

import numpy as np

from pocket_sdl import LabError
from pocket_sdl.config import load_config
from pocket_sdl.optimize import CandidatePlanner, expected_improvement, gp_posterior, normal_cdf, rbf


class OptimizerTests(unittest.TestCase):
    def test_rbf_exact_values(self):
        kernel = rbf([[0, 0, 0]], [[0, 0, 0], [0.35, 0, 0]], 0.35)
        np.testing.assert_allclose(kernel, [[1, math.exp(-0.5)]])

    def test_one_point_posterior(self):
        mean, variance, stats = gp_posterior([[0.1, 0.2, 0.3]], [2.5], [[0.1, 0.2, 0.3], [0.4, 0.2, 0.3]])
        np.testing.assert_allclose(mean, [0, 0])
        # test 点恰与训练点重合；另一候选的距离为 0.3，latent 方差不等于 1。
        kernel = math.exp(-(0.3**2) / (2 * 0.35**2))
        expected_remote = 1.0 - kernel**2 / (1.0 + 0.0025)
        np.testing.assert_allclose(variance, [0.0025 / 1.0025, expected_remote])
        self.assertTrue(stats["constant_loss"])

    def test_independent_small_posterior(self):
        # 独立标量参考：K=[[1.0025,k],[k,1.0025]]，alpha=(K^-1)y。
        x = [[0.0, 0, 0], [0.2, 0, 0]]
        y = [0.0, 1.0]
        k = math.exp(-0.04 / (2 * 0.35**2))
        inverse = np.linalg.inv(np.array([[1.0025, k], [k, 1.0025]]))
        alpha = inverse @ (np.array(y) - 0.5)
        candidate = [[0.1, 0, 0]]
        cross = np.array([math.exp(-0.01 / (2 * 0.35**2))] * 2)
        mean, variance, stats = gp_posterior(x, y, candidate)
        self.assertAlmostEqual(mean[0], cross @ alpha)
        self.assertAlmostEqual(variance[0], 1 - cross @ inverse @ cross)
        self.assertEqual(stats["y_scale"], 0.5)

    def test_ei_is_minimization_not_maximization(self):
        # best 标准化为 -1；候选均值为 -1.5，改善为 0.49。反向则为 0。
        ei = expected_improvement(np.array([-1.5]), np.array([0.0]), best=0.5, y_mean=1.5, y_scale=1, xi=0.01)
        self.assertAlmostEqual(ei[0], 0.49)
        self.assertEqual(expected_improvement([0], [0], 0, 0, 1, 0.01)[0], 0)

    def test_ei_normal_and_zero_variance(self):
        ei = expected_improvement(np.array([0.0]), np.array([1.0]), best=0, y_mean=0, y_scale=1, xi=0.01)
        sigma_z = -0.01
        expected = sigma_z * normal_cdf(sigma_z) + math.exp(-0.5 * sigma_z**2) / math.sqrt(2 * math.pi)
        self.assertAlmostEqual(ei[0], expected)
        self.assertTrue(np.isfinite(ei).all())

    def test_bad_cholesky_and_invalid_options(self):
        with self.assertRaisesRegex(LabError, "CONFIG_INVALID"):
            gp_posterior([[0, 0, 0]], [0], [[0, 0, 0]], noise=-1)
        with self.assertRaises(LabError):
            gp_posterior([[0, 0, 0]], [math.inf], [[0, 0, 0]])
        with self.assertRaisesRegex(LabError, "NUMERICAL_ERROR"), patch("numpy.linalg.cholesky", side_effect=np.linalg.LinAlgError("singular")):
            gp_posterior([[0, 0, 0]], [0], [[0, 0, 0]])

    def test_shared_initialization_and_no_repeats(self):
        random = CandidatePlanner(load_config("configs/simulated.json"), "random")
        bo = CandidatePlanner(load_config("configs/simulated.json"), "bo")
        shared_x, shared_y, chosen_indices = [], [], []
        for index in range(24):
            random_index = random.choose(index, shared_x, shared_y)
            selected = bo.choose(index, shared_x, shared_y)
            if index < 4:
                self.assertEqual(random_index, selected)
            self.assertNotIn(selected, chosen_indices)
            chosen_indices.append(selected)
            from pocket_sdl.core import GRID
            shared_x.append(GRID[selected].tolist())
            shared_y.append(float(index))
        with self.assertRaises(LabError):
            bo.choose(8, shared_x[:4] * 2, shared_y)

    @patch("pocket_sdl.optimize.expected_improvement", return_value=np.zeros(1327))
    @patch("pocket_sdl.optimize.gp_posterior", return_value=(np.zeros(1327), np.ones(1327), {"y_mean": 0.6, "y_scale": 1.0, "constant_loss": False}))
    def test_ei_ties_select_lowest_unused_index(self, *_):
        config = load_config("configs/simulated.json")
        planner = CandidatePlanner(config, "bo")
        x = [[0, 0, 0], [1, 1, 1], [0, 1, 1], [1, 0, 0]]
        y = [1.0, 0.8, 0.4, 0.1]
        # 最小网格索引 0 已测量，因此在所有 EI 并列时只能选索引 1。
        self.assertEqual(planner.choose(4, x, y), 1)


if __name__ == "__main__":
    unittest.main()
