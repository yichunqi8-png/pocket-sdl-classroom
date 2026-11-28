"""固定超参数的随机搜索与 NumPy GP-EI。"""

import math

import numpy as np

from . import LabError
from .core import GRID, validate_x


def rbf(a, b, length_scale):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    delta = a[:, None, :] - b[None, :, :]
    return np.exp(-np.sum(delta * delta, axis=2) / (2.0 * length_scale**2))


def gp_posterior(x_train, y_train, candidates, length_scale=0.35, noise=0.0025):
    """返回标准化空间的预测均值、latent 方差和训练尺度。"""
    x_train = np.asarray(x_train, dtype=float)
    y_train = np.asarray(y_train, dtype=float)
    candidates = np.asarray(candidates, dtype=float)
    if not 0 < length_scale or not math.isfinite(length_scale) or not 0 < noise or not math.isfinite(noise):
        raise LabError("CONFIG_INVALID", "RBF length_scale 和 noise 必须为有限正数")
    if x_train.ndim != 2 or x_train.shape[1] != 3 or y_train.shape != (len(x_train),):
        raise LabError("NUMERICAL_ERROR", "GP 训练数组形状不一致")
    if not len(x_train) or not np.all(np.isfinite(x_train)) or not np.all(np.isfinite(y_train)):
        raise LabError("NUMERICAL_ERROR", "GP 训练集为空或含有非有限数值")
    y_mean = float(np.mean(y_train))
    observed_sd = float(np.std(y_train, ddof=0))
    y_scale = observed_sd if observed_sd >= 1e-12 else 1.0
    y_standard = (y_train - y_mean) / y_scale
    if not np.all(np.isfinite(candidates)):
        raise LabError("NUMERICAL_ERROR", "候选数组含有非有限数值")
    covariance = rbf(x_train, x_train, length_scale)
    covariance[np.diag_indices_from(covariance)] += noise
    cross = rbf(x_train, candidates, length_scale)
    try:
        chol = np.linalg.cholesky(covariance)
        alpha = np.linalg.solve(chol.T, np.linalg.solve(chol, y_standard))
        latent = np.linalg.solve(chol, cross)
    except np.linalg.LinAlgError as error:
        raise LabError("NUMERICAL_ERROR", f"GP Cholesky/求解失败：{error}") from error
    mean = cross.T @ alpha
    variance = np.clip(1.0 - np.sum(latent * latent, axis=0), 0.0, 1.0)
    return mean, variance, {"y_mean": y_mean, "y_scale": y_scale, "constant_loss": observed_sd < 1e-12}


def normal_cdf(x):
    return 0.5 * math.erfc(-float(x) / math.sqrt(2.0))


def expected_improvement(mean, variance, best, y_mean, y_scale, xi=0.01):
    """最小化目标的 EI；均返回非负值。"""
    mean = np.asarray(mean, dtype=float)
    variance = np.asarray(variance, dtype=float)
    improvement = (float(best) - y_mean) / y_scale - mean - xi
    ei = np.zeros_like(mean)
    nonzero = variance > 1e-24
    if np.any(nonzero):
        sigma = np.sqrt(variance[nonzero])
        z = improvement[nonzero] / sigma
        cdf = np.array([normal_cdf(value) for value in z])
        pdf = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        ei[nonzero] = improvement[nonzero] * cdf + sigma * pdf
    deterministic = ~nonzero
    ei[deterministic] = np.maximum(improvement[deterministic], 0.0)
    return np.clip(ei, 0.0, None)


class CandidatePlanner:
    """共享 seed 的前四次随机初始化；后续 random 或 BO 不重复选点。"""

    def __init__(self, config, strategy=None):
        if strategy not in (None, "random", "bo"):
            raise LabError("CONFIG_INVALID", "strategy 只能是 random 或 bo")
        self.config = config
        self.strategy = strategy
        self.budget = config["measurement"]["budget"]
        self.initial_count = config["measurement"]["initial_candidates"]
        self.seed = config["seed"]
        if not 1 <= self.initial_count <= self.budget <= len(GRID):
            raise LabError("CONFIG_INVALID", "初始化点数或预算不合理")
        self.random_order = np.random.default_rng(self.seed).choice(
            len(GRID), size=self.budget, replace=False
        ).tolist()

    def choose(self, iteration, observed_x, observed_loss):
        if len(observed_x) != len(observed_loss) or iteration != len(observed_x):
            raise LabError("OPTIMIZER_STATE", "候选序号、已测输入和有效 loss 数量不一致")
        if iteration < self.initial_count:
            return self.random_order[iteration]
        if self.strategy == "random":
            return self.random_order[iteration]
        if self.strategy != "bo" or len(observed_loss) < self.initial_count:
            raise LabError("OPTIMIZER_STATE", "BO 必须先完成随机初始化")
        used = {121 * round(validate_x(x)[0] * 10) + 11 * round(validate_x(x)[1] * 10) + round(validate_x(x)[2] * 10) for x in observed_x}
        if len(used) != len(observed_x):
            raise LabError("OPTIMIZER_STATE", "优化器收到重复候选")
        remaining = np.array([index for index in range(len(GRID)) if index not in used], dtype=int)
        options = self.config["optimizer"]
        mean, variance, stats = gp_posterior(
            observed_x, observed_loss, GRID[remaining],
            length_scale=options["length_scale"], noise=options["noise"],
        )
        scores = expected_improvement(
            mean, variance, min(observed_loss),
            y_mean=stats["y_mean"], y_scale=stats["y_scale"], xi=options["xi"],
        )
        return int(remaining[int(np.argmax(scores))])
