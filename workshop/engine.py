"""Workshop 实时交互引擎：统一调度设备、实时分光仪、人机擂台与扫码实验队列。

复用 pocket_sdl.core / pocket_sdl.optimize / pocket_sdl.transport / pocket_sdl.simulated，不修改或绕过现有硬件保护。
"""

import copy
import math
import queue
import random
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from pocket_sdl import LabError
from pocket_sdl.config import load_config
from pocket_sdl.core import (
    CHANNELS_NM,
    GRID,
    calibration_stats,
    led_values,
    spectral_loss,
    validate_raw,
    validate_x,
)
from pocket_sdl.optimize import CandidatePlanner, expected_improvement, gp_posterior
from pocket_sdl.workflow import open_device

REPO_ROOT = Path(__file__).resolve().parents[1]


def loss_to_score(loss_value):
    """将无量纲谱差 L(x) 单调映射为外行易懂的 0-100 相似度分值（界面同时展示真实 L(x)）。"""
    if loss_value is None or not math.isfinite(loss_value):
        return 0.0
    return round(100.0 * math.exp(-3.0 * max(0.0, float(loss_value))), 1)


def snap_x(value):
    """将任意 [0,1]^3 输入吸附到 0.1 网格上并通过核心校验。"""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise LabError("INVALID_INPUT", "RGB 必须是长度为 3 的数组")
    snapped = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise LabError("INVALID_INPUT", "RGB 分量必须是有限数值")
        clamped = min(1.0, max(0.0, float(item)))
        snapped.append(round(clamped * 10.0) / 10.0)
    return list(validate_x(snapped))


def _utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _choose_mystery_x(rng):
    """挑选有区分度、非纯黑/纯白的神秘目标 RGB。"""
    candidates = [
        [r / 10.0, g / 10.0, b / 10.0]
        for r in range(1, 10)
        for g in range(1, 10)
        for b in range(1, 10)
        if max(r, g, b) >= 4 and (max(r, g, b) - min(r, g, b)) >= 2
    ]
    return list(rng.choice(candidates))


class WorkshopEngine:
    """线程安全的 Workshop 后端引擎，支持 SIMULATED 与 PHYSICAL 双模式。"""

    def __init__(self, mode="SIMULATED", serial_port=None, config_path=None, sim_delay_s=0.28):
        self.mode = str(mode).upper()
        if self.mode not in ("SIMULATED", "PHYSICAL"):
            raise LabError("CONFIG_INVALID", "mode 只能是 SIMULATED 或 PHYSICAL")
        if config_path is None:
            filename = "physical.json" if self.mode == "PHYSICAL" else "simulated.json"
            config_path = REPO_ROOT / "configs" / filename
        self.config = load_config(config_path)
        if self.config["mode"] != self.mode:
            raise LabError("CONFIG_MISMATCH", f"配置文件 mode={self.config['mode']} 与请求 mode={self.mode} 不一致")
        self.serial_port = serial_port if self.mode == "PHYSICAL" else None
        self.sim_delay_s = max(0.0, float(sim_delay_s)) if self.mode == "SIMULATED" else 0.0

        self._device_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._sub_lock = threading.Lock()
        self._subscribers = []

        self._stop_all = threading.Event()
        self._cancel_job = threading.Event()
        self._live_streaming = threading.Event()
        self._worker_thread = None
        self._live_thread = threading.Thread(target=self._live_loop, name="workshop-live", daemon=True)

        self.device = None
        self.device_connected = False
        self.device_info = None
        self.last_error = None
        self.active_job = None  # None | "calibrate" | "arena_ai" | "queue_job"

        self.live_x = [0.0, 0.0, 0.0]
        self.latest_sample = None
        self.calibration = None

        self._rng = random.Random()
        self.arena = self._empty_arena()
        self.queue_auto = False
        self.task_queue = []
        self.gallery = []
        self.current_queue_task = None

        self.connect()
        self._live_thread.start()

    def _empty_arena(self):
        return {
            "round_id": None,
            "target_kind": None,  # mystery | custom | ambient
            "target_raw": None,
            "target_normalized": None,
            "target_x": None,  # 仅在 revealed=True 时下发给前端
            "revealed": False,
            "seed": self.config["seed"],
            "human": None,
            "leaderboard": [],
            "ai_runs": {},  # strategy -> {strategy, status, budget, rows, best_loss, best_score, best_x, brain}
        }

    # ---------- SSE 订阅与广播 ----------

    def subscribe(self):
        q = queue.Queue(maxsize=128)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event, data):
        payload = {"event": event, "data": data, "ts": _utc_iso()}
        with self._sub_lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    # ---------- 设备连接与安全测量 ----------

    def connect(self):
        with self._device_lock:
            self._close_device_unlocked()
            try:
                self.device = open_device(self.config, self.serial_port)
                self.device_connected = True
                self.device_info = copy.deepcopy(self.device.device_info)
                self.last_error = None
            except BaseException as error:
                self.device = None
                self.device_connected = False
                self.device_info = None
                self._record_error(error)
                raise
        self.publish("state", self.get_state())
        return self.device_info

    def _close_device_unlocked(self):
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
            self.device_connected = False

    def shutdown(self):
        self._stop_all.set()
        self._cancel_job.set()
        self._live_streaming.clear()
        with self._device_lock:
            self._close_device_unlocked()

    def _record_error(self, error):
        code = getattr(error, "code", type(error).__name__)
        message = str(error)
        info = {"code": code, "message": message, "ts": _utc_iso()}
        with self._state_lock:
            self.last_error = info
        self.publish("error", info)
        return info

    def led_off(self):
        self._live_streaming.clear()
        with self._device_lock:
            ok = False
            if self.device is not None:
                ok = bool(self.device.off())
        self.publish("led_off", {"ok": ok})
        self.publish("state", self.get_state())
        return {"ok": ok}

    def _measure_raw(self, x, turn_off_after=False):
        x = snap_x(x)
        threshold = self.config["sensor"]["saturation_raw_exclusive"]
        with self._device_lock:
            if self.device is None or not self.device_connected:
                raise LabError("DISCONNECTED", "设备未连接，请先检查串口并点击重新连接")
            started = time.monotonic()
            try:
                raw = self.device.measure(x)
                raw = validate_raw(raw, threshold)
            except BaseException as error:
                try:
                    self.device.off()
                except Exception:
                    pass
                self._record_error(error)
                raise
            finally:
                if turn_off_after and self.device is not None:
                    try:
                        self.device.off()
                    except Exception:
                        pass
            duration_ms = round((time.monotonic() - started) * 1000.0, 1)
        return x, raw, duration_ms

    def _format_sample(self, x, raw, duration_ms):
        raw_list = [int(round(v)) for v in raw]
        normalized = None
        if self.calibration is not None:
            dark = np.asarray(self.calibration["dark_mean"], dtype=float)
            scale = float(self.calibration["reference_scale"])
            normalized = [round(float(v), 5) for v in (np.asarray(raw, dtype=float) - dark) / scale]
        peak_idx = int(np.argmax(raw_list))
        max_raw = max(raw_list)
        threshold = self.config["sensor"]["saturation_raw_exclusive"]
        return {
            "x": list(x),
            "led_rgb": led_values(x),
            "raw": raw_list,
            "normalized": normalized,
            "channels_nm": list(CHANNELS_NM),
            "peak_nm": CHANNELS_NM[peak_idx],
            "max_raw": max_raw,
            "saturation_ratio": round(max_raw / float(threshold), 4),
            "duration_ms": duration_ms,
            "source": self.mode,
            "ts": _utc_iso(),
        }

    # ---------- 实时分光仪 ----------

    def live_measure_once(self, x):
        self._assert_no_active_job()
        x, raw, duration_ms = self._measure_raw(x, turn_off_after=not self._live_streaming.is_set())
        sample = self._format_sample(x, raw, duration_ms)
        with self._state_lock:
            self.live_x = list(x)
            self.latest_sample = sample
            self.last_error = None
        self.publish("sample", sample)
        return sample

    def set_live_stream(self, enabled, x=None):
        if x is not None:
            snapped = snap_x(x)
            with self._state_lock:
                self.live_x = snapped
        if enabled:
            self._assert_no_active_job()
            self._live_streaming.set()
        else:
            self._live_streaming.clear()
            with self._device_lock:
                if self.device is not None and self.active_job is None:
                    self.device.off()
        state = self.get_state()
        self.publish("state", state)
        return state

    def _live_loop(self):
        while not self._stop_all.is_set():
            if not self._live_streaming.is_set() or self.active_job is not None:
                time.sleep(0.05)
                continue
            with self._state_lock:
                x = list(self.live_x)
            try:
                x_used, raw, duration_ms = self._measure_raw(x, turn_off_after=False)
                sample = self._format_sample(x_used, raw, duration_ms)
                with self._state_lock:
                    self.latest_sample = sample
                    self.last_error = None
                self.publish("sample", sample)
            except BaseException:
                self._live_streaming.clear()
                self.publish("state", self.get_state())
                time.sleep(0.2)
                continue
            # SIMULATED 模式下保持舒适刷新节奏；PHYSICAL 每次采样本身约 0.68s
            pause = 0.22 if self.mode == "SIMULATED" else 0.05
            time.sleep(pause)

    # ---------- 快速校准 ----------

    def _assert_no_active_job(self):
        with self._state_lock:
            if self.active_job is not None:
                raise LabError("BUSY", f"当前正在执行 {self.active_job}，请等待完成或先点击停止")

    def calibrate(self):
        self._assert_no_active_job()
        self._live_streaming.clear()
        with self._state_lock:
            self.active_job = "calibrate"
        self.publish("state", self.get_state())
        try:
            dark_repeats = self.config["calibration"]["dark_repeats"]
            ref_repeats = self.config["calibration"]["reference_repeats"]
            ref_x = self.config["calibration"]["reference_x"]

            dark_rows = []
            for idx in range(dark_repeats):
                _, raw, ms = self._measure_raw([0.0, 0.0, 0.0], turn_off_after=False)
                dark_rows.append(raw)
                self.publish("calibrate_progress", {"step": "dark", "index": idx + 1, "total": dark_repeats})
                if self.sim_delay_s:
                    time.sleep(min(0.12, self.sim_delay_s))

            ref_rows = []
            for idx in range(ref_repeats):
                _, raw, ms = self._measure_raw(ref_x, turn_off_after=False)
                ref_rows.append(raw)
                self.publish("calibrate_progress", {"step": "reference", "index": idx + 1, "total": ref_repeats})
                if self.sim_delay_s:
                    time.sleep(min(0.12, self.sim_delay_s))

            with self._device_lock:
                if self.device is not None:
                    self.device.off()

            stats = calibration_stats(dark_rows, ref_rows)
            cal = {
                "id": "ws-" + uuid.uuid4().hex[:8],
                "created_at": _utc_iso(),
                "source": self.mode,
                "dark_mean": [round(float(v), 3) for v in stats["dark_mean"]],
                "reference_mean": [round(float(v), 3) for v in stats["reference_mean"]],
                "dark_noise": round(float(stats["dark_noise"]), 4),
                "reference_scale": round(float(stats["scale"]), 4),
                "snr": round(float(stats["scale"] / stats["dark_noise"]), 2),
                "channels_nm": list(CHANNELS_NM),
            }
            with self._state_lock:
                self.calibration = cal
                self.last_error = None
            self.publish("calibrated", cal)
            return cal
        finally:
            with self._state_lock:
                self.active_job = None
            self.publish("state", self.get_state())

    def _ensure_calibrated(self):
        if self.calibration is None:
            self.calibrate()

    # ---------- 人机调色擂台 (Arena) ----------

    def arena_new_target(self, kind="mystery", x=None, hide_target=True):
        self._assert_no_active_job()
        self._live_streaming.clear()
        self._ensure_calibrated()

        if kind not in ("mystery", "custom", "ambient"):
            raise LabError("INVALID_INPUT", "target kind 只能是 mystery / custom / ambient")

        if kind == "mystery":
            target_x = _choose_mystery_x(self._rng)
            revealed = False
            measure_x = target_x
        elif kind == "custom":
            target_x = snap_x(x if x is not None else self.config["calibration"]["target_x"])
            revealed = not bool(hide_target)
            measure_x = target_x
        else:  # ambient: 关灯读取真实环境/物体反射光谱
            target_x = None
            revealed = True
            measure_x = [0.0, 0.0, 0.0]

        repeats = self.config["calibration"]["target_repeats"]
        rows = []
        for _ in range(repeats):
            _, raw, _ = self._measure_raw(measure_x, turn_off_after=False)
            rows.append(raw)
        with self._device_lock:
            if self.device is not None:
                self.device.off()

        target_raw = np.mean(np.stack(rows), axis=0)
        dark = np.asarray(self.calibration["dark_mean"], dtype=float)
        scale = float(self.calibration["reference_scale"])
        target_norm = ((target_raw - dark) / scale).tolist()

        with self._state_lock:
            self.arena = self._empty_arena()
            self.arena.update(
                round_id=uuid.uuid4().hex[:8],
                target_kind=kind,
                target_raw=[round(float(v), 3) for v in target_raw.tolist()],
                target_normalized=[round(float(v), 5) for v in target_norm],
                target_x=target_x,
                revealed=revealed,
                seed=self._rng.randint(1, 9999),
            )
            self.last_error = None

        snapshot = self.get_state()
        self.publish("arena_target", snapshot["arena"])
        self.publish("state", snapshot)
        return snapshot["arena"]

    def arena_reveal(self):
        with self._state_lock:
            if self.arena["round_id"] is None:
                raise LabError("INVALID_STATE", "当前还没有建立擂台目标")
            self.arena["revealed"] = True
        snapshot = self.get_state()
        self.publish("state", snapshot)
        return snapshot["arena"]

    def arena_human_start(self, player_name="现场挑战者", budget=10):
        self._assert_no_active_job()
        self._live_streaming.clear()
        with self._state_lock:
            if self.arena["round_id"] is None:
                raise LabError("INVALID_STATE", "请先设定一个目标光谱")
            budget = int(budget)
            if not 3 <= budget <= 24:
                raise LabError("INVALID_INPUT", "人类挑战预算应在 3-24 之间")
            name = str(player_name or "现场挑战者").strip()[:24] or "现场挑战者"
            self.arena["human"] = {
                "player": name,
                "budget": budget,
                "rows": [],
                "best_loss": None,
                "best_score": 0.0,
                "best_x": None,
                "finished": False,
            }
        snapshot = self.get_state()
        self.publish("state", snapshot)
        return snapshot["arena"]

    def arena_human_measure(self, x):
        self._assert_no_active_job()
        self._live_streaming.clear()
        with self._state_lock:
            if self.arena["round_id"] is None or self.calibration is None:
                raise LabError("INVALID_STATE", "请先设定目标光谱")
            if self.arena["human"] is None:
                self.arena["human"] = {
                    "player": "现场挑战者",
                    "budget": 10,
                    "rows": [],
                    "best_loss": None,
                    "best_score": 0.0,
                    "best_x": None,
                    "finished": False,
                }
            human = self.arena["human"]
            if len(human["rows"]) >= human["budget"]:
                raise LabError("BUDGET_EXHAUSTED", "本轮实验预算已用完，可以换下一位挑战者或召唤 AI 对比")
            dark = np.asarray(self.calibration["dark_mean"], dtype=float)
            scale = float(self.calibration["reference_scale"])
            target_raw = np.asarray(self.arena["target_raw"], dtype=float)

        x_used, raw, duration_ms = self._measure_raw(x, turn_off_after=True)
        loss = spectral_loss(raw, dark, scale, target_raw)
        score = loss_to_score(loss)
        sample = self._format_sample(x_used, raw, duration_ms)

        with self._state_lock:
            human = self.arena["human"]
            iteration = len(human["rows"]) + 1
            prev_best = human["best_loss"]
            best_loss = loss if prev_best is None else min(prev_best, loss)
            if prev_best is None or loss < prev_best:
                human["best_loss"] = round(loss, 6)
                human["best_score"] = score
                human["best_x"] = list(x_used)
            row = {
                "iteration": iteration,
                "x": list(x_used),
                "led_rgb": led_values(x_used),
                "raw": sample["raw"],
                "normalized": sample["normalized"],
                "loss": round(loss, 6),
                "best_so_far": round(best_loss, 6),
                "score": score,
                "best_score": human["best_score"],
                "duration_ms": duration_ms,
            }
            human["rows"].append(row)
            human["finished"] = len(human["rows"]) >= human["budget"]
            self.latest_sample = sample

            # 更新排行榜（按同选手保留最佳或记录每次完成/领先成绩）
            board = [item for item in self.arena["leaderboard"] if item["player"] != human["player"]]
            board.append(
                {
                    "player": human["player"],
                    "best_loss": human["best_loss"],
                    "best_score": human["best_score"],
                    "best_x": list(human["best_x"]),
                    "steps": len(human["rows"]),
                }
            )
            board.sort(key=lambda item: item["best_loss"])
            self.arena["leaderboard"] = board[:10]

        self.publish("sample", sample)
        self.publish("human_step", {"row": row, "arena": self._public_arena()})
        self.publish("state", self.get_state())
        return {"row": row, "arena": self._public_arena()}

    def arena_start_ai(self, strategy="bo", budget=24):
        if strategy not in ("bo", "random"):
            raise LabError("CONFIG_INVALID", "strategy 只能是 bo 或 random")
        budget = int(budget)
        if not 6 <= budget <= 36:
            raise LabError("CONFIG_INVALID", "AI 预算应在 6-36 之间")
        self._assert_no_active_job()
        self._live_streaming.clear()
        with self._state_lock:
            if self.arena["round_id"] is None or self.calibration is None:
                raise LabError("INVALID_STATE", "请先设定擂台目标光谱")
            self.active_job = "arena_ai"
            self._cancel_job.clear()
            self.arena["ai_runs"][strategy] = {
                "strategy": strategy,
                "status": "running",
                "budget": budget,
                "rows": [],
                "best_loss": None,
                "best_score": 0.0,
                "best_x": None,
                "brain": None,
            }
        self.publish("state", self.get_state())
        thread = threading.Thread(
            target=self._run_ai_worker,
            args=(strategy, budget),
            name=f"arena-{strategy}",
            daemon=True,
        )
        self._worker_thread = thread
        thread.start()
        return {"started": True, "strategy": strategy, "budget": budget}

    def stop_active_job(self):
        self._cancel_job.set()
        self._live_streaming.clear()
        with self._device_lock:
            if self.device is not None:
                self.device.off()
        self.publish("state", self.get_state())
        return {"stopping": True}

    def _compute_bo_brain(self, observed_x, observed_loss, options):
        used = {
            121 * round(validate_x(pt)[0] * 10) + 11 * round(validate_x(pt)[1] * 10) + round(validate_x(pt)[2] * 10)
            for pt in observed_x
        }
        remaining = np.array([idx for idx in range(len(GRID)) if idx not in used], dtype=int)
        mean, variance, stats = gp_posterior(
            observed_x,
            observed_loss,
            GRID[remaining],
            length_scale=options["length_scale"],
            noise=options["noise"],
        )
        scores = expected_improvement(
            mean,
            variance,
            min(observed_loss),
            y_mean=stats["y_mean"],
            y_scale=stats["y_scale"],
            xi=options["xi"],
        )
        top_order = np.argsort(scores)[::-1][:3]
        top_candidates = []
        for pos in top_order:
            grid_idx = int(remaining[int(pos)])
            pred_loss = stats["y_mean"] + float(mean[int(pos)]) * stats["y_scale"]
            pred_sd = math.sqrt(max(0.0, float(variance[int(pos)]))) * stats["y_scale"]
            top_candidates.append(
                {
                    "x": GRID[grid_idx].tolist(),
                    "pred_loss": round(pred_loss, 4),
                    "pred_sd": round(pred_sd, 4),
                    "ei": round(float(scores[int(pos)]), 5),
                }
            )
        return int(remaining[int(top_order[0])]), top_candidates

    def _run_ai_worker(self, strategy, budget):
        try:
            with self._state_lock:
                cal = copy.deepcopy(self.calibration)
                target_raw = np.asarray(self.arena["target_raw"], dtype=float)
                round_seed = self.arena["seed"]

            dark = np.asarray(cal["dark_mean"], dtype=float)
            scale = float(cal["reference_scale"])

            run_cfg = copy.deepcopy(self.config)
            run_cfg["seed"] = round_seed
            run_cfg["measurement"]["budget"] = budget
            run_cfg["measurement"]["minimum_valid"] = min(run_cfg["measurement"]["minimum_valid"], budget)
            planner = CandidatePlanner(run_cfg, strategy)

            observed_x = []
            observed_loss = []
            best = math.inf
            best_x = None

            for iteration in range(budget):
                if self._cancel_job.is_set() or self._stop_all.is_set():
                    with self._state_lock:
                        self.arena["ai_runs"][strategy]["status"] = "stopped"
                    break

                brain = None
                if strategy == "bo" and iteration >= planner.initial_count:
                    selected, top3 = self._compute_bo_brain(observed_x, observed_loss, run_cfg["optimizer"])
                    brain = {"phase": "GP-EI 自主决策", "top3": top3}
                else:
                    selected = planner.choose(iteration, observed_x, observed_loss)
                    if strategy == "bo":
                        brain = {"phase": f"初始探索 ({iteration + 1}/{planner.initial_count})", "top3": []}
                    else:
                        brain = {"phase": "均匀随机盲猜", "top3": []}

                x = GRID[selected].tolist()
                x_used, raw, duration_ms = self._measure_raw(x, turn_off_after=False)
                loss = spectral_loss(raw, dark, scale, target_raw)
                score = loss_to_score(loss)
                if loss < best:
                    best = loss
                    best_x = list(x_used)

                observed_x.append(x_used)
                observed_loss.append(loss)
                sample = self._format_sample(x_used, raw, duration_ms)
                row = {
                    "iteration": iteration + 1,
                    "candidate_index": selected,
                    "x": list(x_used),
                    "led_rgb": led_values(x_used),
                    "raw": sample["raw"],
                    "normalized": sample["normalized"],
                    "loss": round(loss, 6),
                    "best_so_far": round(best, 6),
                    "score": score,
                    "best_score": loss_to_score(best),
                    "duration_ms": duration_ms,
                }
                with self._state_lock:
                    run_state = self.arena["ai_runs"][strategy]
                    run_state["rows"].append(row)
                    run_state["best_loss"] = round(best, 6)
                    run_state["best_score"] = loss_to_score(best)
                    run_state["best_x"] = list(best_x)
                    run_state["brain"] = brain
                    self.latest_sample = sample

                self.publish("sample", sample)
                self.publish("ai_step", {"strategy": strategy, "row": row, "brain": brain, "arena": self._public_arena()})
                if self.sim_delay_s:
                    time.sleep(self.sim_delay_s)
            else:
                with self._state_lock:
                    self.arena["ai_runs"][strategy]["status"] = "completed"
        except BaseException as error:
            with self._state_lock:
                if strategy in self.arena["ai_runs"]:
                    self.arena["ai_runs"][strategy]["status"] = "error"
            self._record_error(error)
        finally:
            with self._device_lock:
                if self.device is not None:
                    self.device.off()
            with self._state_lock:
                self.active_job = None
            self.publish("state", self.get_state())

    # ---------- 观众扫码出题队列 (Cloud SDL Queue) ----------

    def queue_submit(self, author, color_name, x):
        snapped = snap_x(x)
        clean_author = str(author or "匿名观众").strip()[:20] or "匿名观众"
        clean_color = str(color_name or "自定义色").strip()[:24] or "自定义色"
        task = {
            "id": uuid.uuid4().hex[:8],
            "author": clean_author,
            "color_name": clean_color,
            "target_x": snapped,
            "target_rgb": led_values(snapped),
            "status": "queued",
            "created_at": _utc_iso(),
            "best_x": None,
            "best_loss": None,
            "best_score": None,
            "steps": 0,
            "budget": 16,
            "rows": [],
        }
        with self._state_lock:
            self.task_queue.append(task)
            should_auto = self.queue_auto and self.active_job is None
        self.publish("queue_update", self._public_queue())
        self.publish("state", self.get_state())
        if should_auto:
            self.queue_run_next()
        return copy.deepcopy(task)

    def queue_set_auto(self, enabled):
        with self._state_lock:
            self.queue_auto = bool(enabled)
            should_start = self.queue_auto and self.active_job is None and len(self.task_queue) > 0
        self.publish("state", self.get_state())
        if should_start:
            self.queue_run_next()
        return {"auto": self.queue_auto}

    def queue_run_next(self, budget=16):
        budget = max(8, min(24, int(budget)))
        self._assert_no_active_job()
        self._live_streaming.clear()
        with self._state_lock:
            if not self.task_queue:
                raise LabError("QUEUE_EMPTY", "当前观众队列为空")
            task = self.task_queue.pop(0)
            task["status"] = "running"
            task["budget"] = budget
            self.current_queue_task = task
            self.active_job = "queue_job"
            self._cancel_job.clear()
        self.publish("queue_update", self._public_queue())
        self.publish("state", self.get_state())
        thread = threading.Thread(
            target=self._run_queue_worker,
            args=(task, budget),
            name=f"queue-{task['id']}",
            daemon=True,
        )
        self._worker_thread = thread
        thread.start()
        return copy.deepcopy(task)

    def _run_queue_worker(self, task, budget):
        auto_next = False
        try:
            if self.calibration is None:
                # 内部校准（不重复抢占 active_job）
                dark_rows = [self._measure_raw([0, 0, 0])[1] for _ in range(self.config["calibration"]["dark_repeats"])]
                ref_rows = [
                    self._measure_raw(self.config["calibration"]["reference_x"])[1]
                    for _ in range(self.config["calibration"]["reference_repeats"])
                ]
                stats = calibration_stats(dark_rows, ref_rows)
                with self._state_lock:
                    self.calibration = {
                        "id": "ws-" + uuid.uuid4().hex[:8],
                        "created_at": _utc_iso(),
                        "source": self.mode,
                        "dark_mean": [round(float(v), 3) for v in stats["dark_mean"]],
                        "reference_mean": [round(float(v), 3) for v in stats["reference_mean"]],
                        "dark_noise": round(float(stats["dark_noise"]), 4),
                        "reference_scale": round(float(stats["scale"]), 4),
                        "snr": round(float(stats["scale"] / stats["dark_noise"]), 2),
                        "channels_nm": list(CHANNELS_NM),
                    }

            with self._state_lock:
                cal = copy.deepcopy(self.calibration)
            dark = np.asarray(cal["dark_mean"], dtype=float)
            scale = float(cal["reference_scale"])

            # 采样观众目标颜色 3 次
            target_samples = [self._measure_raw(task["target_x"])[1] for _ in range(3)]
            target_raw = np.mean(np.stack(target_samples), axis=0)
            target_norm = [round(float(v), 5) for v in ((target_raw - dark) / scale).tolist()]

            run_cfg = copy.deepcopy(self.config)
            run_cfg["seed"] = self._rng.randint(1, 9999)
            run_cfg["measurement"]["budget"] = budget
            run_cfg["measurement"]["minimum_valid"] = min(run_cfg["measurement"]["minimum_valid"], budget)
            planner = CandidatePlanner(run_cfg, "bo")

            observed_x = []
            observed_loss = []
            best = math.inf
            best_x = None

            with self._state_lock:
                task["target_normalized"] = target_norm

            for iteration in range(budget):
                if self._cancel_job.is_set() or self._stop_all.is_set():
                    task["status"] = "stopped"
                    break
                selected = planner.choose(iteration, observed_x, observed_loss)
                x = GRID[selected].tolist()
                x_used, raw, duration_ms = self._measure_raw(x, turn_off_after=False)
                loss = spectral_loss(raw, dark, scale, target_raw)
                score = loss_to_score(loss)
                if loss < best:
                    best = loss
                    best_x = list(x_used)
                observed_x.append(x_used)
                observed_loss.append(loss)
                sample = self._format_sample(x_used, raw, duration_ms)
                row = {
                    "iteration": iteration + 1,
                    "x": list(x_used),
                    "loss": round(loss, 6),
                    "best_so_far": round(best, 6),
                    "score": score,
                    "best_score": loss_to_score(best),
                    "normalized": sample["normalized"],
                }
                with self._state_lock:
                    task["rows"].append(row)
                    task["steps"] = iteration + 1
                    task["best_loss"] = round(best, 6)
                    task["best_score"] = loss_to_score(best)
                    task["best_x"] = list(best_x)
                    self.latest_sample = sample
                self.publish("sample", sample)
                self.publish("queue_step", {"task": copy.deepcopy(task), "row": row})
                if self.sim_delay_s:
                    time.sleep(self.sim_delay_s)
            else:
                with self._state_lock:
                    task["status"] = "completed"
                    task["completed_at"] = _utc_iso()
                    self.gallery.insert(0, copy.deepcopy(task))
                    self.gallery = self.gallery[:30]
                    auto_next = self.queue_auto and len(self.task_queue) > 0
        except BaseException as error:
            task["status"] = "error"
            self._record_error(error)
        finally:
            with self._device_lock:
                if self.device is not None:
                    self.device.off()
            with self._state_lock:
                self.current_queue_task = None
                self.active_job = None
            self.publish("queue_update", self._public_queue())
            self.publish("state", self.get_state())
            if auto_next and not self._stop_all.is_set() and not self._cancel_job.is_set():
                try:
                    self.queue_run_next(budget=budget)
                except Exception:
                    pass

    # ---------- 状态快照 ----------

    def _public_arena(self):
        with self._state_lock:
            arena = copy.deepcopy(self.arena)
        if not arena["revealed"]:
            arena["target_x"] = None
        return arena

    def _public_queue(self):
        with self._state_lock:
            return {
                "auto": self.queue_auto,
                "current": copy.deepcopy(self.current_queue_task),
                "pending": copy.deepcopy(self.task_queue),
                "gallery": copy.deepcopy(self.gallery),
            }

    def get_state(self):
        with self._state_lock:
            return {
                "mode": self.mode,
                "serial_port": self.serial_port,
                "connected": self.device_connected,
                "device_info": copy.deepcopy(self.device_info),
                "channels_nm": list(CHANNELS_NM),
                "saturation_threshold": self.config["sensor"]["saturation_raw_exclusive"],
                "live_streaming": self._live_streaming.is_set(),
                "live_x": list(self.live_x),
                "latest_sample": copy.deepcopy(self.latest_sample),
                "calibration": copy.deepcopy(self.calibration),
                "active_job": self.active_job,
                "last_error": copy.deepcopy(self.last_error),
                "arena": self._public_arena(),
                "queue": self._public_queue(),
            }
