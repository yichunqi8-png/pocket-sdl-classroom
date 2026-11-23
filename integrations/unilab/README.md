# 接入 Uni-Lab OS 指南

本目录下的 [`pocket-sdl-lab/`](pocket-sdl-lab/) 是一个完整的极薄 **Uni-Lab OS 设备包**：
- 所有硬件通信、USB CDC data 串口自动发现、5 秒看门狗关灯保护、线程锁、饱和校验与校准质量门均留在底层包 `pocket-sdl`（[`pocket_sdl/device.py`](../../pocket_sdl/device.py)）中；
- 设备包内的 [`pocket_sdl_lab/devices/pico_spectro.py`](pocket-sdl-lab/pocket_sdl_lab/devices/pico_spectro.py) 仅保留供 Uni-Lab OS 静态扫描所需的 `@device`、`@action`、`@topic_config` 与签名声明，方法体单行委托给 `PicoSpectroDevice`；
- 独立分包可避免与本仓库根目录下并存的 `workshop/`、`firmware/` 冲突，完整满足 Uni-Lab OS 单包目录与四道验证门要求。

## 1. 安装底层驱动包与设备包

在已安装 Uni-Lab OS 的 Python 环境中执行：

```bash
# 1) 安装底层硬件包（本仓库根目录）
python -m pip install -e /path/to/pocket-sdl

# 2) 进入设备包目录安装并自检
export DEVICE_PACKAGE_ROOT="/path/to/pocket-sdl/integrations/unilab/pocket-sdl-lab"
cd "$DEVICE_PACKAGE_ROOT"
python -m pip install -e '.[dev]'
python -m pip check
python -m compileall pocket_sdl_lab
python -m pytest -q
```

## 2. 按顺序通过验证门

```bash
# 验证门二：静态检查设备类型、动作、状态与构造参数
# 确认输出中出现类型 community.pocket_sdl_lab.pico_spectro
unilab package inspect \
  --path "$DEVICE_PACKAGE_ROOT" \
  --out "$DEVICE_PACKAGE_ROOT/dist/inspect"

# 验证门三：构建交付物
unilab package build \
  --path "$DEVICE_PACKAGE_ROOT" \
  --out "$DEVICE_PACKAGE_ROOT/dist/build"

# 验证门四：先以 dry-run 启动检查加载（不执行真实动作）
unilab workspace start \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --graph "$DEVICE_PACKAGE_ROOT/deployment/graphs/dry-run.json" \
  --runtime-mode dry-run \
  --wait 300

unilab workspace status --workspace "$DEVICE_PACKAGE_ROOT" --json
unilab workspace stop --workspace "$DEVICE_PACKAGE_ROOT"
```

## 3. 切换仿真与真机运行

- **仿真测试**：使用 `deployment/graphs/dry-run.json` 搭配 `--runtime-mode normal` 执行真实仿真逻辑（`mode="SIMULATED"`）。
- **真机运行**：先完成现场安全检查，再使用 `deployment/graphs/production.json`（`mode="PHYSICAL"`、`port="auto"`；多台设备时填写 `serial_number`），启动后按 `connect -> doctor -> stop` 做首轮实机验收。

## 4. Linux 工控机固定串口（可选，多设备推荐）

如不希望启动时扫描探测串口，可在 Linux 下添加 `/etc/udev/rules.d/99-pocket-sdl.rules`（替换实际 `ATTRS{serial}`；CircuitPython 双 CDC 的 data 接口号通常为 `02`）：

```udev
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", ATTRS{serial}=="YOUR_SERIAL_HERE", ENV{ID_USB_INTERFACE_NUM}=="02", SYMLINK+="pocket-sdl-data"
```

随后在 `deployment/graphs/production.json` 中将 `port` 改为 `"/dev/pocket-sdl-data"`。
