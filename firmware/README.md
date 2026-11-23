# Pico H CircuitPython 部署

以下操作针对 **RP2040 Pico / Pico H**，不刷 Pico W 固件。部署到 CIRCUITPY 会改变 Pico 当前程序；如盘上已有需要的文件，先自行备份。不要在尚未断电核对接线时连接外部 LED。

## 1. 安装 CircuitPython

1. 从[官方 Pico 下载页](https://circuitpython.org/board/raspberry_pi_pico/)下载稳定版 Pico UF2，记录实际版本。
2. 按住 Pico 的 **BOOTSEL**，插入 USB 后释放。挂载为 `RPI-RP2`。
3. 将 UF2 复制到 `RPI-RP2`，设备会自动重启并挂载为 `CIRCUITPY`。
4. 若没有出现 CIRCUITPY，停止并核对 UF2 是否属于 Pico / Pico H，而不是 Pico W 或其他 RP2040 板。

## 2. 安装库

从 [Adafruit CircuitPython Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases) 下载**与你所装 CircuitPython 主版本相同**的 `mpy` 包，复制到 `CIRCUITPY/lib/`：

```text
adafruit_as7341.mpy
adafruit_bus_device/
adafruit_register/
neopixel.mpy
```

其中 `adafruit_bus_device` 与 `adafruit_register` 是 AS7341 驱动的依赖，需保留完整目录；不能只复制 AS7341 一个文件。

## 3. 部署项目固件

macOS / Linux，在仓库根目录执行（请先确认 `/Volumes/CIRCUITPY` 确实是刚刷好的 Pico）：

```bash
mkdir -p /Volumes/CIRCUITPY/lib
# 将上述匹配版本的库复制到 lib 后：
cp -X firmware/boot.py /Volumes/CIRCUITPY/boot.py
cp -X firmware/code.py /Volumes/CIRCUITPY/code.py
sync
```

`boot.py` 启用两个独立串口：

```python
import usb_cdc
usb_cdc.enable(console=True, data=True)
```

更新 `boot.py` 后必须**安全弹出、断电重插或复位**。boot 阶段设置只在启动时生效。Windows 用文件管理器复制到 `CIRCUITPY` 根目录并安全弹出。

固件上电先关闭 NeoPixel；仅 `hello` 成功后允许采样。请求和响应在 data 串口，不混用 REPL console。协议固定：

```json
{"v":1,"id":"唯一ID","op":"hello","args":{"mode":"PHYSICAL"}}
{"v":1,"id":"唯一ID","op":"measure","args":{"rgb":[24,48,32]}}
{"v":1,"id":"唯一ID","op":"off","args":{}}
```

成功响应：

```json
{"v":1,"id":"同一ID","ok":true,"result":{"raw":[1,2,3,4,5,6,7,8],"sample_seq":1}}
```

`hello` 会关灯、扫描 `0x39`、初始化固定参数并返回实际配置和 SHA-256 指纹。`measure` 设置一颗 LED、等待 50ms，然后调用官方 `all_channels` 完成两个 SMUX bank 的 F1–F8 读取。电脑每次测量发出一次 `measure`，一次完整 F1–F8 读数即一次观测。

`off` 在任何故障状态下均尽力关灯。硬件断连、非法命令、测量异常、LED 非零 5 秒未收到有效命令均停止正常工作并尽力关灯。若底层 I²C/C 调用卡死，软件无法承诺即时恢复，应断电复位。

## 4. 连接 data 接口

电脑执行：

```bash
.venv/bin/python -m pocket_sdl list-ports
```

选择 CDC **data** 接口，而非 console/REPL。macOS/Linux 一般是较后的 `usbmodem`/`ttyACM` 接口，Windows 是较后的 COM 号，但以实际枚举与握手为准。关闭 Mu、串口助手、其他 Notebook 对 Pico 的占用。

```bash
.venv/bin/python -m pocket_sdl doctor \
  --config configs/physical.json \
  --port /dev/cu.usbmodem实际接口
```

只有真机 `doctor` 输出 `PASSED_SINGLE_SAMPLE_NOT_FULL_CALIBRATION`，才说明握手与暗场读取通过；随后可按仓库根目录 `README.md` 继续校准与闭环实验。
