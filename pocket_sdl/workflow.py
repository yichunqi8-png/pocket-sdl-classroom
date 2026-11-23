"""校准、优化和确认的同步事务式流程。"""

import contextlib
import json
import math
import shutil
import time
from pathlib import Path

import numpy as np

from . import LabError
from .config import (
    experiment_fingerprint,
    file_digest,
    load_config,
    strict_json,
    validate_device_info,
)
from .core import (
    CHANNELS_NM,
    GRID,
    calibration_stats,
    led_values,
    relative_stability,
    spectral_loss,
    validate_raw,
)
from .ledger import Ledger, append_jsonl, atomic_json, export_csv, timestamp
from .optimize import CandidatePlanner


def open_device(config, port=None):
    if config["mode"] == "PHYSICAL":
        from .transport import SerialDevice

        return SerialDevice(port, config)
    if port is not None:
        raise LabError("CONFIG_INVALID", "SIMULATED 模式不能指定 --port")
    from .simulated import SimDevice

    return SimDevice(config)


@contextlib.contextmanager
def device_session(out_dir, kind, config, port, metadata, device=None):
    ledger = Ledger(out_dir, kind, metadata)
    dev = None
    try:
        dev = device if device is not None else open_device(config, port)
        validate_device_info(dev.device_info, config)
        yield dev, ledger
    except BaseException as error:
        cleanup = None
        try:
            if dev is not None:
                dev.close()
        except BaseException as close_error:
            cleanup = {"code": type(close_error).__name__, "message": str(close_error)}
        ledger.fail(error, {"operation": kind, "cleanup_error": cleanup})
        raise
    else:
        try:
            dev.close()
        except BaseException as error:
            ledger.fail(error, {"operation": kind, "phase": "LED_OFF_AND_CLOSE"})
            raise
        if not ledger.status_path.exists():
            raise LabError("LEDGER_IO_ERROR", "实验状态文件缺失")


def read_json(path):
    try:
        data = strict_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LabError("CALIBRATION_INVALID", f"无法读取严格 JSON：{path}；{error}") from error
    if not isinstance(data, dict):
        raise LabError("CALIBRATION_INVALID", "JSON 顶层必须是对象")
    return data


def read_calibration(path):
    try:
        raw = strict_json(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise LabError("CALIBRATION_INVALID", f"无法读取校准文件：{path}；{error}") from error
    data = raw
    required = {
        "schema_version", "mode", "status", "id", "config", "device_info", "device_fingerprint",
        "experiment_fingerprint", "target_raw", "reference_scale", "dark_mean", "raw_samples", "stability",
    }
    if not isinstance(data, dict) or not required <= set(data):
        raise LabError("CALIBRATION_INVALID", "校准缺少必要字段")
    if data["schema_version"] != 1 or data["status"] != "COMPLETED":
        raise LabError("CALIBRATION_INVALID", "只接受完整、成功的校准")
    status_path = Path(path).parent / "status.json"
    if read_json(status_path).get("status") != "COMPLETED":
        raise LabError("CALIBRATION_INVALID", "校准目录状态不是 COMPLETED")
    config = load_config(data["config"])
    if data["mode"] != config["mode"] or data["experiment_fingerprint"] != experiment_fingerprint(config):
        raise LabError("CALIBRATION_INVALID", "校准的 mode 或配置指纹不一致")
    if data["device_fingerprint"] != digest_device(config):
        raise LabError("CONFIG_MISMATCH", "校准设备指纹不是当前配置的计算值")
    validate_device_info(data["device_info"], config)
    target = np.asarray(data["target_raw"], dtype=float)
    dark = np.asarray(data["dark_mean"], dtype=float)
    if target.shape != (8,) or dark.shape != (8,) or not np.all(np.isfinite(target)) or not np.all(np.isfinite(dark)):
        raise LabError("CALIBRATION_INVALID", "校准均值必须为八个有限计数值")
    if not isinstance(data["reference_scale"], (int, float)) or isinstance(data["reference_scale"], bool) or not math.isfinite(data["reference_scale"]) or data["reference_scale"] <= 0:
        raise LabError("CALIBRATION_INVALID", "校准参考尺度非法")
    raw_samples = data["raw_samples"]
    for phase, expected in (
        ("dark", config["calibration"]["dark_repeats"]),
        ("reference", config["calibration"]["reference_repeats"]),
        ("target", config["calibration"]["target_repeats"]),
        ("stability", config["calibration"]["stability_repeats"]),
    ):
        if not isinstance(raw_samples.get(phase), list) or len(raw_samples[phase]) != expected:
            raise LabError("CALIBRATION_INVALID", f"缺少完整的 {phase} 原始读数")
        for raw in raw_samples[phase]:
            validate_raw(raw, config["sensor"]["saturation_raw_exclusive"])
    # 独立的重算审计，防止校准字段与保存的原始记录相矛盾。
    stats = calibration_stats(raw_samples["dark"], raw_samples["reference"])
    if abs(stats["scale"] - data["reference_scale"]) > 1e-9 or not np.allclose(stats["dark_mean"], dark, rtol=0, atol=1e-10):
        raise LabError("CALIBRATION_INVALID", "参考尺度或暗场均值无法由原始样本重算")
    expected_target = np.mean(np.asarray(raw_samples["target"], dtype=float), axis=0)
    if not np.allclose(target, expected_target, rtol=0, atol=1e-10):
        raise LabError("CALIBRATION_INVALID", "目标均值无法由原始样本重算")
    quality = config["calibration"]["stability_rms_relative_max"]
    stability = relative_stability(raw_samples["stability"], dark)
    if stability["rms_relative"] >= quality:
        raise LabError("CALIBRATION_INVALID", "原始稳定性数据不满足当前配置质量门")
    return data, config


def digest_device(config):
    from .config import expected_device_fingerprint

    return expected_device_fingerprint(config)


def measure_once(dev, ledger, x, phase, iteration=None):
    x = list(x)
    started = time.monotonic()
    attempt = {"timestamp_utc": timestamp(), "phase": phase, "x": x, "iteration": iteration, "status": "error", "raw": None}
    try:
        raw = dev.measure(x)
        raw = validate_raw(raw, dev.config["sensor"]["saturation_raw_exclusive"])
    except BaseException as error:
        if isinstance(error, KeyboardInterrupt):
            raise
        attempt.update(error={"code": getattr(error, "code", type(error).__name__), "message": str(error)})
        attempt["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        ledger.attempts(attempt)
        raise
    attempt.update(status="ok", raw=raw.tolist(), duration_ms=round((time.monotonic() - started) * 1000, 3))
    ledger.attempts(attempt)
    return raw


def collect_samples(dev, ledger, phase, x, repeats):
    return [measure_once(dev, ledger, x, phase) for _ in range(repeats)]


def prepare(config, out_dir, port=None, device=None):
    config = load_config(config)
    metadata = {"mode": config["mode"], "experiment_fingerprint": experiment_fingerprint(config)}
    with device_session(out_dir, "prepare", config, port, metadata, device) as (dev, ledger):
        ledger.device_info = dev.device_info
        settings = config["calibration"]
        dark = collect_samples(dev, ledger, "dark", (0, 0, 0), settings["dark_repeats"])
        reference = collect_samples(dev, ledger, "reference", settings["reference_x"], settings["reference_repeats"])
        stats = calibration_stats(dark, reference)
        target = collect_samples(dev, ledger, "target", settings["target_x"], settings["target_repeats"])
        target_raw = np.mean(np.stack(target), axis=0)
        stability = collect_samples(dev, ledger, "stability", settings["target_x"], settings["stability_repeats"])
        quality = relative_stability(stability, stats["dark_mean"])
        quality.update(
            limit=settings["stability_rms_relative_max"],
            passed=quality["rms_relative"] < settings["stability_rms_relative_max"],
        )
        if not quality["passed"]:
            raise LabError(
                "QUALITY_GATE_FAILED",
                f"固定点相对波动 {quality['rms_relative']:.3%} 未小于 {settings['stability_rms_relative_max']:.3%}；请先检查几何和遮光",
            )
        calibration = {
            "schema_version": 1,
            "mode": config["mode"],
            "status": "COMPLETED",
            "id": experiment_fingerprint(config)[:12] + "-" + timestamp().replace(":", "").replace("-", ""),
            "created_at": timestamp(),
            "channels_nm": list(CHANNELS_NM),
            "config": config,
            "device_info": dev.device_info,
            "device_fingerprint": digest_device(config),
            "experiment_fingerprint": metadata["experiment_fingerprint"],
            "raw_samples": {
                "dark": np.stack(dark).astype(int).tolist(),
                "reference": np.stack(reference).astype(int).tolist(),
                "target": np.stack(target).astype(int).tolist(),
                "stability": np.stack(stability).astype(int).tolist(),
            },
            "dark_mean": stats["dark_mean"].tolist(),
            "reference_mean": stats["reference_mean"].tolist(),
            "dark_channel_sd": stats["dark_channel_sd"].tolist(),
            "dark_noise": stats["dark_noise"],
            "reference_scale": stats["scale"],
            "target_raw": target_raw.tolist(),
            "target_normalized": ((target_raw - stats["dark_mean"]) / stats["scale"]).tolist(),
            "quality_gate": {"scale_min": 10 * stats["dark_noise"], "scale_passed": True, "saturation_passed": True},
            "stability": quality,
        }
        atomic_json(ledger.path / "calibration.json", calibration)
        result = {key: calibration[key] for key in ("mode", "id", "reference_scale", "dark_noise", "target_raw")}
        result["stability_rms_relative"] = quality["rms_relative"]
        result["out"] = str(ledger.path)
        ledger.complete({"operation": "prepare", "status": "ok", "calibration_id": calibration["id"]})
        return result


def run(calibration_path, strategy, out_dir, port=None, device=None):
    calibration, config = read_calibration(calibration_path)
    original = Path(calibration_path).resolve()
    threshold = config["sensor"]["saturation_raw_exclusive"]
    sha256 = file_digest(original.read_bytes())
    planner = CandidatePlanner(config, strategy)
    dark = np.asarray(calibration["dark_mean"])
    scale = calibration["reference_scale"]
    target = np.asarray(calibration["target_raw"])
    metadata = {
        "mode": config["mode"], "strategy": strategy, "seed": config["seed"],
        "budget": config["measurement"]["budget"], "calibration_id": calibration["id"],
        "calibration_sha256": sha256, "experiment_fingerprint": experiment_fingerprint(config),
    }
    with device_session(out_dir, "run", config, port, metadata, device) as (dev, ledger):
        ledger.device_info = dev.device_info
        # 从原始字节复制，不重新序列化，确保运行快照与源校准摘要相同。
        shutil.copyfile(original, ledger.path / "calibration.json")
        best = math.inf
        best_row = None
        records = []
        started = time.monotonic()
        for iteration in range(config["measurement"]["budget"]):
            selected = planner.choose(iteration, [r["x"] for r in records], [r["loss"] for r in records])
            x = GRID[selected].tolist()
            sample_start = time.monotonic()
            raw = None
            try:
                raw = dev.measure(x)
                raw = validate_raw(raw, threshold)
            except BaseException as error:
                if not isinstance(error, KeyboardInterrupt):
                    raw_value = None
                    if isinstance(error, LabError) and error.code == "SATURATED" and raw is not None:
                        raw_value = raw.astype(int).tolist()
                    ledger.attempts({
                        "iteration": iteration + 1, "x": x, "candidate_index": selected,
                        "status": "error", "raw": raw_value,
                        "error": {"code": getattr(error, "code", type(error).__name__), "message": str(error)},
                    })
                raise
            loss = spectral_loss(raw, dark, scale, target)
            best = min(best, loss)
            row = {
                "schema_version": 1, "run_id": ledger.metadata["created_at"], "iteration": iteration + 1,
                "candidate_index": selected, "x": x, "led_rgb": led_values(x), "raw": raw.tolist(),
                "loss": loss, "best_so_far": best, "status": "ok", "error": None,
                "timestamp_utc": timestamp(), "duration_ms": round((time.monotonic() - sample_start) * 1000, 3),
                "source": config["mode"], "calibration_id": calibration["id"], "calibration_sha256": sha256,
                "device_fingerprint": digest_device(config), "experiment_fingerprint": experiment_fingerprint(config),
                "strategy": strategy, "seed": config["seed"], "firmware_version": dev.device_info["firmware_version"],
            }
            ledger.append(row)
            # 只有有效且已成功落盘的记录才能进入下一轮决策。
            records.append({"x": x, "loss": loss})
            if best == loss:
                best_row = row
        if len(records) < config["measurement"]["minimum_valid"]:
            raise LabError("QUALITY_GATE_FAILED", "有效测量低于 minimum_valid")
        export_csv(ledger.path / "measurements.jsonl", ledger.path / "trials.csv")
        summary = {
            "operation": "run", "status": "ok", "source": config["mode"], "strategy": strategy,
            "seed": config["seed"], "valid_measurements": len(records),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "calibration_id": calibration["id"], "calibration_sha256": sha256,
            "best_observed": best_row, "out": str(ledger.path),
        }
        ledger.complete(summary)
        return {"out": str(ledger.path), "valid_measurements": len(records), "best_x": best_row["x"], "best_observed_loss": best}


def read_completed_run(path):
    path = Path(path)
    status = read_json(path / "status.json")
    summary = read_json(path / "summary.json")
    if status.get("kind") != "run" or status.get("status") != "COMPLETED" or summary.get("status") != "ok":
        raise LabError("RUN_INVALID", "只允许确认已完成的优化运行")
    calibration, config = read_calibration(path / "calibration.json")
    if calibration_path_sha(path / "calibration.json") != status.get("calibration_sha256"):
        raise LabError("RUN_INVALID", "运行内校准快照的摘要已改变")
    rows = []
    try:
        with open(path / "measurements.jsonl", encoding="utf-8") as handle:
            for line in handle:
                row = strict_json(line)
                if row.get("status") != "ok":
                    raise LabError("RUN_INVALID", "运行日志中存在非正常 training 行")
                raw = validate_raw(row["raw"], config["sensor"]["saturation_raw_exclusive"])
                loss = spectral_loss(raw, calibration["dark_mean"], calibration["reference_scale"], calibration["target_raw"])
                if not math.isclose(loss, row["loss"], rel_tol=0, abs_tol=1e-12):
                    raise LabError("RUN_INVALID", f"第 {row.get('iteration')} 行 loss 无法重算")
                rows.append(row)
    except OSError as error:
        raise LabError("RUN_INVALID", f"无法读取运行日志：{error}") from error
    if len(rows) != config["measurement"]["budget"] or [r["iteration"] for r in rows] != list(range(1, len(rows) + 1)):
        raise LabError("RUN_INVALID", "运行日志的预算或序号不连续")
    best = min(rows, key=lambda row: row["loss"])
    if best != summary["best_observed"]:
        raise LabError("RUN_INVALID", "摘要中的最佳记录与逐次日志不一致")
    return best, calibration, config, rows


def calibration_path_sha(path):
    return file_digest(Path(path).read_bytes())


def confirm(run_dir, out_dir, port=None, device=None):
    best, calibration, config, rows = read_completed_run(run_dir)
    metadata = {
        "mode": config["mode"], "source_run_manifest": {
            "created_at": read_json(Path(run_dir) / "status.json").get("created_at"),
            "calibration_sha256": calibration_path_sha(Path(run_dir) / "calibration.json"),
        },
        "calibration_id": calibration["id"], "calibration_sha256": calibration_path_sha(Path(run_dir) / "calibration.json"),
        "x": best["x"], "experiment_fingerprint": experiment_fingerprint(config),
    }
    with device_session(out_dir, "confirm", config, port, metadata, device) as (dev, ledger):
        ledger.device_info = dev.device_info
        from . import __version__
        metadata["source_run_manifest"]["software_version"] = __version__
        atomic_json(ledger.path / "status.json", ledger.metadata)
        losses = []
        started = time.monotonic()
        for repeat in range(config["measurement"]["confirm_repeats"]):
            raw = measure_once(dev, ledger, best["x"], "confirm", repeat + 1)
            loss = spectral_loss(raw, calibration["dark_mean"], calibration["reference_scale"], calibration["target_raw"])
            row = {
                "repeat": repeat + 1, "x": best["x"], "led_rgb": led_values(best["x"]), "raw": raw.tolist(),
                "normalized": ((raw - np.asarray(calibration["dark_mean"])) / calibration["reference_scale"]).tolist(),
                "loss": loss, "source": config["mode"], "calibration_id": calibration["id"],
                "calibration_sha256": metadata["calibration_sha256"], "part_of_optimizer_training": False,
                "part_of_strategy_budget": False, "timestamp_utc": timestamp(),
            }
            append_jsonl(ledger.path / "confirmations.jsonl", row)
            losses.append(loss)
        summary = {
            "operation": "confirm", "status": "ok", "source": config["mode"], "x": best["x"],
            "best_observed_loss": best["loss"], "confirmation_losses": losses,
            "mean_confirmation_loss": float(np.mean(losses)),
            "sample_sd_confirmation_loss": float(np.std(losses, ddof=1)),
            "repeat_count": len(losses), "calibration_id": calibration["id"],
            "calibration_sha256": metadata["calibration_sha256"],
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "included_in_strategy_budget": False, "out": str(ledger.path),
        }
        ledger.complete(summary)
        return {key: summary[key] for key in ("out", "x", "best_observed_loss", "mean_confirmation_loss", "sample_sd_confirmation_loss")}


def doctor(config, port):
    config = load_config(config)
    if config["mode"] != "PHYSICAL":
        raise LabError("CONFIG_INVALID", "doctor 只用于真实设备，不能验证模拟器")
    dev = open_device(config, port)
    try:
        raw = validate_raw(dev.measure((0, 0, 0)), config["sensor"]["saturation_raw_exclusive"])
        return {"port": port, "device": dev.device_info, "dark_raw": raw.tolist(), "real_hardware_test": "PASSED_SINGLE_SAMPLE_NOT_FULL_CALIBRATION"}
    finally:
        dev.close()
