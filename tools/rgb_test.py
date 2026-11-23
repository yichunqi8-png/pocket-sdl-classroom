"""只测试单颗 RGB 的低亮度显示，不启动优化或完整校准。"""

import argparse
import time
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pocket_sdl.config import load_config
from pocket_sdl.core import validate_raw
from pocket_sdl.transport import SerialDevice


def main():
    parser = argparse.ArgumentParser(description="红→绿→蓝→白，结束自动关灯")
    parser.add_argument("--port", required=True)
    parser.add_argument("--hold", type=float, default=2.0, help="每色保持秒数，范围 0.5–3")
    args = parser.parse_args()
    if not 0.5 <= args.hold <= 3.0:
        parser.error("--hold 必须在 0.5–3.0 秒之间")
    config = load_config(Path(__file__).resolve().parents[1] / "configs" / "physical.json")
    device = SerialDevice(args.port, config)
    threshold = config["sensor"]["saturation_raw_exclusive"]
    try:
        print("先测暗场 3 次，LED 保持关闭", flush=True)
        dark_samples = [validate_raw(device.measure((0, 0, 0)), threshold) for _ in range(3)]
        dark_mean = np.mean(dark_samples, axis=0)
        dark_rms = float(np.linalg.norm(np.std(dark_samples, axis=0, ddof=1)))
        print("暗场均值:", np.rint(dark_mean).astype(int).tolist(), "波动尺度:", round(dark_rms, 3), flush=True)
        for name, x in (("红色", (1, 0, 0)), ("绿色", (0, 1, 0)), ("蓝色", (0, 0, 1)), ("白色", (1, 1, 1))):
            print(f"{name}，连续采样 3 次，保持 {args.hold:g}s", flush=True)
            samples = [validate_raw(device.measure(x), threshold) for _ in range(3)]
            for repeat, raw in enumerate(samples, 1):
                print(f"  第{repeat}次 F1–F8:", raw.astype(int).tolist(), flush=True)
            mean = np.mean(samples, axis=0)
            delta = mean - dark_mean
            print("  均值:", np.rint(mean).astype(int).tolist(), flush=True)
            print("  相对暗场变化:", np.rint(delta).astype(int).tolist(), flush=True)
            print("  光谱变化幅度:", round(float(np.linalg.norm(delta)), 3), flush=True)
            time.sleep(args.hold)
        print("测试完成，LED 已关闭。以上是测量记录，不自动判定亮灯/遮光是否合格。", flush=True)
    finally:
        device.close()


if __name__ == "__main__":
    main()
