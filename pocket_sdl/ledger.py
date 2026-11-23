"""一次性输出目录、可审计 JSONL/CSV 和原子状态文件。"""

import csv
import json
import os
from pathlib import Path

from . import LabError
from .config import canonical_json


def timestamp():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    try:
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as error:
        try:
            temp.unlink()
        except OSError:
            pass
        raise LabError("LEDGER_IO_ERROR", f"无法写入 {path}：{error}") from error


def append_jsonl(path, row):
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise LabError("LEDGER_IO_ERROR", f"追加日志失败：{error}") from error


def create_output(path):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir(exist_ok=False)
    except OSError as error:
        raise LabError("OUTPUT_EXISTS" if path.exists() else "LEDGER_IO_ERROR", f"输出目录必须使用新路径：{path}；{error}") from error
    return path


class Ledger:
    def __init__(self, out_dir, kind, metadata):
        self.path = create_output(out_dir)
        self.status_path = self.path / "status.json"
        self.metadata = dict(metadata, kind=kind, created_at=timestamp(), status="RUNNING")
        atomic_json(self.status_path, self.metadata)

    def append(self, row):
        append_jsonl(self.path / "measurements.jsonl", row)

    def attempts(self, row):
        append_jsonl(self.path / "attempts.jsonl", row)

    def fail(self, error, context=None):
        data = {
            "code": getattr(error, "code", type(error).__name__),
            "message": getattr(error, "message", str(error)),
            "timestamp_utc": timestamp(),
            "context": context or {},
        }
        try:
            atomic_json(self.path / "error.json", data)
            atomic_json(self.status_path, dict(self.metadata, status="FAILED", ended_at=timestamp(), error=data))
        except LabError:
            # 保留原始故障，避免覆盖真正使实验停止的原因。
            pass
        return data

    def complete(self, summary):
        atomic_json(self.path / "summary.json", summary)
        atomic_json(self.status_path, dict(self.metadata, status="COMPLETED", ended_at=timestamp()))


CSV_FIELDS = [
    "iteration", "x_r", "x_g", "x_b", "led_r", "led_g", "led_b",
    "raw_415", "raw_445", "raw_480", "raw_515", "raw_555", "raw_590", "raw_630", "raw_680",
    "loss", "best_so_far", "timestamp_utc", "duration_ms", "calibration_sha256",
]


def export_csv(jsonl_path, csv_path):
    from .core import led_values

    try:
        with open(jsonl_path, encoding="utf-8") as source, open(csv_path, "x", newline="", encoding="utf-8-sig") as dest:
            writer = csv.DictWriter(dest, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("status") != "ok":
                    continue
                raw = row["raw"]
                rgb = led_values(row["x"])
                output = {
                    "iteration": row["iteration"], "x_r": row["x"][0], "x_g": row["x"][1], "x_b": row["x"][2],
                    "led_r": rgb[0], "led_g": rgb[1], "led_b": rgb[2],
                    "loss": row["loss"], "best_so_far": row["best_so_far"],
                    "timestamp_utc": row["timestamp_utc"], "duration_ms": row["duration_ms"],
                    "calibration_sha256": row["calibration_sha256"],
                }
                output.update({f"raw_{channel}": value for channel, value in zip((415, 445, 480, 515, 555, 590, 630, 680), raw)})
                writer.writerow(output)
    except (OSError, ValueError, KeyError) as error:
        raise LabError("LEDGER_IO_ERROR", f"导出 CSV 失败：{error}") from error
