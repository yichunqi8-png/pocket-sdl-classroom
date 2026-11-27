# Pocket SDL Classroom · 独立课程版

**维护与联系**：[Qqqyc（yichunqi8-png）](https://github.com/yichunqi8-png)

**先读 [学生与教师使用说明](学生使用说明.md) 与 [七任务操作卡](任务操作卡.md)**。本仓库基于 `ZiWei09/pocket-sdl`（见 [UPSTREAM.md](UPSTREAM.md)，保留原 MIT 许可证），增加七任务课程工作台、稳定性准入、人类独立预算、独立复测、持久归档、多种子真机／仿真对比、测量可信度对照以及 UniLabOS 三色工作流拓展。

- **环境要求**：先安装 Python 3.12（支持 3.10+）；Windows 安装时勾选 `Add Python to PATH`。首次启动会创建独立 `.student-venv` 并联网安装依赖，之后可本地离线使用。
- **真机启动**：Mac 双击 `Start Pocket SDL.command`；Windows 双击 `Start Pocket SDL.bat`（监听 `http://127.0.0.1:8770/`）。设备固件由助教提前配置，自动按序列号选择与暗场自检，**失败不会自动切仿真**。
- **无设备仿真**：Mac 双击 `Start Simulation.command`；Windows 双击 `Start Simulation.bat`（监听 `http://127.0.0.1:8772/`，界面明确标注 `SIMULATED`）。如 Mac 禁止直接打开，在终端运行 `bash "Start Simulation.command"`。
- **可选局域网观众端口**：双击启动器默认仅绑定本机回环（`--audience-port 0`，关闭局域网入口）；如需在课堂启用手机端出题提交，可通过命令行开启：
  `python -m workshop.course_server --mode simulated --port 8772 --audience-port 8773`（真机为 `--port 8770 --audience-port 8771`），手机端仅允许访问 `/mobile` 出题与读取状态。
- **七任务与数据保存**：页面包含七任务、图文接线、人类 6/12/18 次独立预算、Random 和 BO 共同 12/16/20/24 次；讨论记录选填并保存在浏览器本地。实验数据默认写入 `classroom-data/`（原始采样 CSV、完整历史 JSONL、最新快照 JSON），新启动不会沿用旧校准。
- **接线与拓展**：实物接线以 [最新接线说明](docs/最新接线说明.md) 为准；UniLabOS 三色拓展需独立环境，见 [integrations/unilab/README.md](integrations/unilab/README.md)。下方保留上游技术参考说明。

---

# Pocket SDL — Pico H 光谱自主实验闭环

面向教学与科研演示的“口袋自主实验室（Pocket Self-Driving Lab）”，基于 **Raspberry Pi Pico H (RP2040) + AS7341 光谱传感器 + WS2812B 单灯模块**。电脑负责 GP-EI/随机候选与审计日志，Pico 负责 LED 执行和 AS7341 采样，二者通过独立 USB CDC data 串口交换逐行 JSON。无需网络或 LLM API。

- 1331 个 `[0, 0.1, …, 1]³` 候选，LED 上限 80。
- 暗场、亮参考、目标分别独立采样，8 通道 `F1–F8 = [415,445,480,515,555,590,630,680] nm`。
- 随机搜索与固定超参数 GP-EI，各 24 次有效观测；前四候选相同。
- 失败立即停止，不补零、不静默重试、不覆盖故障目录。
- 每个最佳点独立确认 3 次，输出均值和样本标准差，不占用优化预算。
- `SIMULATED` 只验证软件流程；`PHYSICAL` 才是实际硬件观测，二者不能混用。

## 目录结构

```text
configs/
  physical.json     # 真机默认配置
  simulated.json    # 无硬件仿真配置
docs/
  setup-overview.jpg  # 桌面搭建与遮光盒剖面实景示意图
  wiring-diagram.svg  # 40 针精确引脚实物接线图
firmware/
  boot.py           # 启用独立 USB CDC data 串口
  code.py           # Pico H 薄执行器固件（WS2812B + AS7341）
  README.md         # CircuitPython 刷机、装库与部署说明
pocket_sdl/         # 上位机闭环包与设备门面（list-ports / doctor / prepare / run / confirm / device）
workshop/           # Workshop 实时 Web 交互台、观众扫码页与教学 Jupyter Notebook
integrations/
  unilab/           # Uni-Lab OS 独立设备包（pocket-sdl-lab）与四道验证门部署指南
tools/
  rgb_test.py       # 真机红/绿/蓝/白单灯与暗场自检脚本
tests/              # 单元测试与无硬件全链路测试
pyproject.toml      # 打包配置与 pocket-sdl 命令行入口
requirements.txt    # 上位机 Python 依赖（numpy、pyserial）
```

## 1. 接线与搭建

**断电接线。** 两个模块均由 Pico H 的 `3V3 OUT` 供电并共地，USB 口朝上时只需连接 Pico H 右上侧 5 根针（从右上角 Pin 40 往下数第 **3、5、7、9、10** 针）：

![Pico H + AS7341 + WS2812B 实物接线图](docs/wiring-diagram.svg)

| 模块    | 模块引脚 | Pico H          | 说明                                      |
| ------- | -------- | --------------- | ----------------------------------------- |
| AS7341  | VCC      | pin 36, 3V3 OUT | 3.3V 供电（与 WS2812B 经面包板共排）      |
| AS7341  | GND      | pin 38, GND     | 共地                                      |
| AS7341  | SDA      | pin 31, GP26    | I²C1 SDA，100kHz，地址 `0x39`          |
| AS7341  | SCL      | pin 32, GP27    | I²C1 SCL                                 |
| AS7341  | INT/GPIO | 不接（悬空）    | 仅用于硬件中断/外部同步触发，本方案不使用 |
| WS2812B | VCC      | pin 36, 3V3 OUT | 3.3V 供电                                 |
| WS2812B | GND      | Pico GND        | 与 AS7341 共地                            |
| WS2812B | DIN      | pin 34, GP28    | 数据输入直连                              |

将 LED 与传感器正对固定在不透光小盒内，实验期间保持距离、朝向和遮光不变（LED 关闭时测得的是含环境背景的暗场）。整体桌面搭建与盒内放置方式示意如下：

![桌面搭建与遮光盒剖面示意图](docs/setup-overview.jpg)

## 2. 配置与指标

默认配置文件：`configs/physical.json`。

| 参数               | 默认值                                                                          |
| ------------------ | ------------------------------------------------------------------------------- |
| AS7341             | 地址 `0x39`，`atime=100`，`astep=999`，`GAIN_16X`                       |
| 单次积分时间       | `(100+1) × (999+1) × 2.78 µs ≈ 281 ms`（完整 F1–F8 含双 SMUX bank 采样） |
| LED                | 每通道 0–80，映射 `round(80 × x)`                                           |
| LED 稳定等待       | 50ms                                                                            |
| 默认目标 RGB 输入  | `[0.3,0.6,0.4]`，对应 `[24,48,32]`                                          |
| 暗场 / 参考 / 目标 | 各 3 次完整 F1–F8 读数                                                         |
| 稳定性复测         | 目标点额外 5 次                                                                 |
| raw 防护线         | 所有通道 `<60000`                                                             |
| 优化               | seed=7，4 次随机初始化，24 次有效观测                                           |
| BO                 | RBF length_scale=0.35，noise=0.0025，xi=0.01                                    |

暗场均值 `d`、参考均值 `w`、`A=||w-d||₂`，观测 `z=(raw-d)/A`，目标 `z*=(target_raw-d)/A`：

```text
L(x) = ||z(x) - z*||₂
```

不逐次归一化光谱，保留亮度差；loss 是无量纲谱差，不是绝对光谱定标或色度 ΔE。`prepare` 强制：

- `A >= 10 × max(||暗场各通道样本标准差||₂, 1)`；
- 暗场、参考、目标、复测每个 raw 都 `<60000`；
- 对五次扣暗向量 `zᵢ`，`sqrt(mean(||zᵢ-z̄||₂²)) / max(||z̄||₂, 1e-12) < 0.05`。

饱和或质量门失败时，先检查遮光、固定位置、接线或调整配置中的曝光/增益，不自动放宽阈值。**任何配置或光学位置改变后，必须新建校准，并让两种策略共用同一校准。**

## 3. Pico 部署

在 `firmware/README.md` 查看完整刷机、库安装与部署说明。核心是将 `firmware/boot.py` 和 `firmware/code.py` 复制到 `CIRCUITPY`，同时安装匹配 CircuitPython 主版本的 Adafruit AS7341/NeoPixel 库。

本方案使用 **CircuitPython**（而非 MicroPython），需确保所装 Adafruit 库与 Pico 上的 CircuitPython 主版本一致。

## 4. 电脑安装与仿真

支持 Python 3.10+（推荐 Python 3.12）。macOS / Linux，在本仓库根目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pocket_sdl list-ports
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m pocket_sdl list-ports
```

（执行 `pip install -e .` 后，除 `python -m pocket_sdl` 外，也可直接使用 `.venv/bin/pocket-sdl` 或 Windows 下的 `.venv\Scripts\pocket-sdl.exe`。）

不连接硬件也可以完整验证软件：

```bash
.venv/bin/python -m pocket_sdl prepare \
  --config configs/simulated.json \
  --out artifacts/sim-cal-001

.venv/bin/python -m pocket_sdl run \
  --calibration artifacts/sim-cal-001/calibration.json \
  --strategy random \
  --out artifacts/sim-random-001

.venv/bin/python -m pocket_sdl run \
  --calibration artifacts/sim-cal-001/calibration.json \
  --strategy bo \
  --out artifacts/sim-bo-001

.venv/bin/python -m pocket_sdl confirm \
  --run artifacts/sim-random-001 \
  --out artifacts/sim-random-confirm-001

.venv/bin/python -m pocket_sdl confirm \
  --run artifacts/sim-bo-001 \
  --out artifacts/sim-bo-confirm-001
```

模拟设备使用简化 RGB→通道线性响应、暗场和固定 seed 高斯噪声；其数据只用于测试，不证明真实灯珠光谱、饱和特性、稳定性或 BO 优势。`SIMULATED` 模式禁止 `--port`。

## 5. 真机闭环

`list-ports` 会输出 device、description、hwid、VID/PID、serial number。Pico 启用 CDC data 后通常会出现两个端口；选择 **data 接口**（通常是编号较高的 `usbmodem` 接口），不要选 REPL console。以下 `usbmodemXXXX` 必须换成实际输出，不可照抄。

```bash
.venv/bin/python -m pocket_sdl doctor \
  --config configs/physical.json \
  --port /dev/cu.usbmodemXXXX

# 先独立看红/绿/蓝/白，同时检查 3 次光谱读数与暗场差值
.venv/bin/python tools/rgb_test.py --port /dev/cu.usbmodemXXXX

.venv/bin/python -m pocket_sdl prepare \
  --config configs/physical.json \
  --port /dev/cu.usbmodemXXXX \
  --out artifacts/cal-001
```

检查校准输出中的 `reference_scale`、`dark_noise`、`stability_rms_relative` 和原始读数，再让随机搜索和 BO 使用同一校准：

```bash
.venv/bin/python -m pocket_sdl run \
  --calibration artifacts/cal-001/calibration.json \
  --strategy random \
  --port /dev/cu.usbmodemXXXX \
  --out artifacts/random-001

.venv/bin/python -m pocket_sdl run \
  --calibration artifacts/cal-001/calibration.json \
  --strategy bo \
  --port /dev/cu.usbmodemXXXX \
  --out artifacts/bo-001

.venv/bin/python -m pocket_sdl confirm \
  --run artifacts/random-001 \
  --port /dev/cu.usbmodemXXXX \
  --out artifacts/random-confirm-001

.venv/bin/python -m pocket_sdl confirm \
  --run artifacts/bo-001 \
  --port /dev/cu.usbmodemXXXX \
  --out artifacts/bo-confirm-001
```

Windows 将端口替换为实际 `COMn`、将 Python 替换为 `.venv\Scripts\python.exe`。CLI 输出最优输入、观测 loss；确认输出 `mean_confirmation_loss` 与 `sample_sd_confirmation_loss`。比较时注意重复测量噪声，不能预设 BO 一定优于 Random。

修改默认目标时，在**新配置文件**中只修改 `calibration.target_x`（例如 `[0.2,0.8,0.5]`），然后新目录重新 `prepare`。同一个校准目标必须供两个策略共享；不要直接改校准 JSON 中的目标均值。

## 6. 日志与恢复

所有 `--out` 都是**新的叶目录**，已存在即拒绝，父目录可自动创建。没有覆盖或断点续跑功能；`artifacts/` 默认被 Git 忽略。

```text
calibration-dir/
  status.json               # COMPLETED 才可被 run 使用
  calibration.json          # 原始暗场/参考/目标/复测、配置、设备指纹、质量门
  attempts.jsonl            # 每个实际采样及请求上下文
  summary.json              # 校准完成摘要

run-dir/
  status.json
  calibration.json          # 源校准的原始字节快照
  measurements.jsonl        # 每次有效候选，一行一次观测
  attempts.jsonl            # 仅在采样失败时写入，记录失败原始值（若有）和故障上下文
  trials.csv                # Excel/表格可直接打开的扁平数据
  summary.json              # 最佳观测、预算、用时

confirm-dir/
  status.json               # 来源运行标识及校准摘要
  confirmations.jsonl       # 独立三次测量，不回写 GP
  attempts.jsonl            # 每次确认采样记录
  summary.json              # 均值、样本标准差与耗时
```

每条有效运行记录含 `iteration`、`candidate_index`、`x`、`led_rgb`、8 通道 raw、`loss`、`best_so_far`、时间戳、耗时、来源、校准 SHA-256 和配置指纹。raw 成功通过质检并写入日志后才用于 GP；故障行 `raw=null` 或保存饱和数组，`loss` 不会伪造成 0。

常见故障：

| 错误                               | 处理方式                                                             |
| ---------------------------------- | -------------------------------------------------------------------- |
| `SERIAL_IO` / `SERIAL_TIMEOUT` | 关闭串口助手/REPL 占用，核对 data 端口、USB 数据线和复位状态         |
| `SENSOR_IO`                      | 断电检查 `0x39`、SDA/SCL、VCC/GND，再重新 `doctor`               |
| `No pull up found on SDA or SCL` | 检查 AS7341 模块供电、共地及 GP26→SDA、GP27→SCL 接线是否松动或反接 |
| `SATURATED`                      | 改光学几何/曝光/增益/亮度上限，随后新目录重新校准                    |
| `QUALITY_GATE_FAILED`            | 检查固定距离、遮光、供电与热漂移，不先换优化算法                     |
| `CONFIG_MISMATCH`                | 重新复制对应配置固件；核对实际积分、增益、通道和 LED 配置            |
| `NUMERICAL_ERROR`                | 保留故障日志，检查输入和固定 GP 参数，不静默添加 jitter              |

发生错误时电脑尽力发送 `off` 并关闭串口，固件也尽力关灯。LED 非零且 5 秒没有有效命令会触发固件看门狗；CDC 断连时关灯。底层 I²C/C 调用若不可中断，纯软件看门狗不保证即时恢复，应断电复位排查。

Ctrl-C 同样保留故障目录；恢复必须排查原因、重新自检并使用**新目录**，不从原 run 继续。

## 7. 验证边界

测试包含网格/loss/暗场质量门、GP 独立数值与最小化 EI、候选初始化、串口帧校验/拆包/超时、固件 API 桩、坏数据/饱和/GP 故障、模拟 CLI 全链路和日志不可覆盖。

运行：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

软件单元测试使用模拟器和固件桩，仅验证上位机与协议逻辑；实际硬件联调以 `doctor → prepare → random/bo → confirm` 为准。

## 8. Workshop 实时交互台与教学 Notebook

除命令行闭环工具外，仓库还在 [workshop/](workshop/README.md) 提供了零新增依赖的现场互动与教学套件（支持 `SIMULATED` 彩排与 `PHYSICAL` 真机双模式）：

- **本地实时 Web 控制台**（`workshop/server.py`）：包含实时分光仪、人机调色擂台（Human vs. Random vs. GP-EI）和同 Wi-Fi 观众扫码出题队列。
- **交互式教学 Notebook**（[workshop/SDL_Workshop.ipynb](workshop/SDL_Workshop.ipynb)）：内置彩虹光谱与收敛曲线可视化，零硬件即可在 JupyterLab 或 VS Code（需安装 Jupyter 扩展 / `ipykernel`）中运行全部演示。

零硬件快速启动本地交互台：

```bash
.venv/bin/python workshop/server.py --mode simulated --port 8765 --open
```

真机启动参数、扫码配置与完整 25 分钟演示节奏见 [workshop/README.md](workshop/README.md)。

## 9. 接入 Uni-Lab OS 等上层调度器

仓库内置常驻设备门面 [pocket_sdl/device.py](pocket_sdl/device.py)（`PicoSpectroDevice`），已收口串口自动发现、看门狗防护（测完自动关灯）、线程锁、内存状态缓存与校准质量门；独立的 Uni-Lab OS 设备包 [integrations/unilab/pocket-sdl-lab/](integrations/unilab/pocket-sdl-lab/) 与安装验证指南见 [integrations/unilab/README.md](integrations/unilab/README.md)。

## 参考

- [Adafruit AS7341 CircuitPython 驱动](https://github.com/adafruit/Adafruit_CircuitPython_AS7341)
- [CircuitPython Pico 固件下载](https://circuitpython.org/board/raspberry_pi_pico/)
- [CircuitPython USB CDC 说明](https://docs.circuitpython.org/projects/circuitpython/en/latest/docs/essentials/usb.html)

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
