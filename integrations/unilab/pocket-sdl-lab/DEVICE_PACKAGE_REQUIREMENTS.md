# 设备包需求卡：Pico AS7341 光谱闭环模块

## 1. 设备信息
- **设备类型 ID**：`pico_spectro`（完整类型名 `community.pocket_sdl_lab.pico_spectro`）
- **硬件组成**：Raspberry Pi Pico H (RP2040) + AS7341 8 通道光谱传感器（I²C `0x39`，`F1–F8 = [415, 445, 480, 515, 555, 590, 630, 680] nm`）+ WS2812B 单灯模块（GP28）
- **底层驱动包**：内置 vendored `pocket_sdl`（`pocket_sdl_lab._vendor.pocket_sdl.device.PicoSpectroDevice`，与根包 `pocket-sdl` 同步）

## 2. 通信方式
- **物理接口**：USB CDC 独立 data 串口（`115200` baud，跳过 REPL Console 口），逐行 JSON 协议（含请求 `id` 与总超时控制）
- **端口发现**：支持 `port="auto"` 自动探测（按 Pico VID 优先高编号非 Console 口并 `hello` 握手），多台设备通过 USB `serial_number` 或固定串口路径区分
- **仿真模式**：`mode="SIMULATED"` 使用内置线性光谱响应与固定种子噪声模型，严格禁止与 `PHYSICAL` 混用

## 3. 动作与参数范围
| 动作 | 参数与单位 / 允许范围 | 返回 |
| --- | --- | --- |
| `list_ports` | 无（只读枚举本机串口，不打开串口） | `success`, `message`, `data`（含 `mode` 与排序后的 `ports` 列表） |
| `connect` | `port` 可选（留空或 `auto` 自动探测；`PHYSICAL` 模式可指定具体串口，切换前须先断开） | `success`, `message`, `data`（含 `device_info` 与配置指纹） |
| `disconnect` | 无（故障态可调） | `success`, `message`, `data` |
| `stop` | 无（故障态可调，立即关灯） | `success`, `message`, `data` |
| `reset` | 无（故障态可调，重连握手清错） | `success`, `message`, `data` |
| `doctor` | 无（单次暗场采集与饱和自检） | `success`, `message`, `data`（含 `dark_raw`） |
| `measure` | `r, g, b` ∈ `[0.0, 1.0]`（默认 `0.1` 步长，对应 LED `0–80`）；`auto_off` 默认 `true`；`snap_to_grid` 默认 `false` | `success`, `message`, `data`（含 8 通道 `raw`，已校准时含 `normalized` 与 `loss`） |
| `calibrate` | `target_r, target_g, target_b` ∈ `[0.0, 1.0]`（同时提供或同时省略）；`save_dir` 可选 | `success`, `message`, `data`（含质量门指标） |
| `load_calibration` | `calibration_path`（已有 `calibration.json` 路径） | `success`, `message`, `data` |
| `clear_calibration` | 无 | `success`, `message`, `data` |

## 4. 状态与发布周期
- `status`（2s）：`Offline` / `Idle` / `Running` / `Error` / `Unknown`
- `connected`（2s）、`last_rgb`（2s）、`last_raw`（2s）、`last_loss`（2s）、`last_error`（2s）
- `calibrated`（5s）、`calibration_id`（5s）、`sample_count`（5s）、`mode`（5s）、`port`（5s）
- `device_fingerprint`（10s）、`firmware_version`（10s）、`channels_nm`（10s）

## 5. 异常与安全边界
- 构造期仅保存配置，不连接硬件。
- 固件自带 5 秒看门狗；默认每次 `measure` 完成后自动发 `off()` 关灯。
- 任意通道原始计数 `>= 60000` 立即报 `SATURATED` 并关灯、转入 `Error` 态；不静默重试、不补零。
- 故障态下阻塞常规 `measure` / `calibrate` / `doctor`，仅允许 `stop` / `reset` / `disconnect`。

## 6. 验收要求
- 通过 `WorkspaceSource` / `compile_package_source` 静态工作区与工作流契约校验，以及 `dry-run.json` 仿真启动验证。
- 覆盖正常、超时、饱和故障、`stop` / `reset` 恢复、串口枚举/切换与跨图 `inventory.db` 清理单元测试。
- 切换到 `production.json` 运行真机前，先完成 `connect -> doctor -> stop -> disconnect` 实机验收。
