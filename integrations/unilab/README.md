# UniLabOS 三色测量拓展（适配 UniLabOS.app / 1.1 设备包工作区）

普通课堂工作台不依赖 UniLabOS。拓展任务需要已安装的 **UniLabOS.app**（dmg）；
不要在普通学生启动器环境直接执行。

## 目录性质

`pocket-sdl-lab` 是一个**设备包工作区**，结构对齐 app 自带的 `demo_workspace`：

```text
pocket-sdl-lab/
├── pyproject.toml          # 包身份 + [tool.unilabos.startup]
├── package.yaml            # 工作流登记（workflow_uuid + source）
├── unilab-app.json/.toml   # app 启动契约（schemaVersion 2）
├── deployment/graphs/      # dry-run.json（SIMULATED）/ production.json（PHYSICAL）
├── pocket_sdl_lab/
│   ├── devices/pico_spectro.py
│   ├── workflows/three_color.py
│   └── _vendor/pocket_sdl/ # 内置驱动，免装外部 pocket-sdl 包
└── tests/
```

app 内嵌 runtime 不可写且不传 `PYTHONPATH`，因此驱动以 vendored 形式随包分发，
不需要 `pip install pocket-sdl`。

## 启动

```sh
UNILABOS_WORKSPACE="$PWD/integrations/unilab/pocket-sdl-lab" \
  /Applications/UniLabOS.app/Contents/MacOS/UniLabOS
```

app 的页面没有工作区选择器；不设 `UNILABOS_WORKSPACE` 时它会打开自带的示例工作区。
**先停掉正在运行的 UniLabOS 再切换**，同一串口不能被两个实例同时占用。

## 真机切换

`unilab-app.json` 的 `startup.graph` 默认指向 `deployment/graphs/dry-run.json`。
真机联调前把它改成 `deployment/graphs/production.json`（`mode: "PHYSICAL"`），
并先关闭普通课程工作台释放串口。下次启动时，`deployment/local_config.py` 会自动检测
仿真/真机图切换并清理旧 `inventory.db`（保留 `workflow_history.db`），避免资源图指纹冲突。

## 验证（不启动 app）

```sh
cd integrations/unilab/pocket-sdl-lab
/Applications/UniLabOS.app/Contents/Resources/os/runtime/bin/python -I -B -c "
import sys; sys.path.insert(0,'.')
from unilabos.package_manager import WorkspaceSource, compile_package_source
c = compile_package_source(WorkspaceSource('.'))
print([d.id for d in c.definitions.devices], [w.id for w in c.definitions.workflows])
"
```

期望输出：`['pico_spectro'] ['three_color']`。

## 执行

工作流在 app 启动时由 `package.yaml` 自动注册，不需要再手动实例化模板。
在控制台打开「Pocket SDL 三色光谱测量」直接运行即可；`port` 留 `auto`。也可在另一个终端：

```sh
python integrations/unilab/pocket-sdl-lab/deployment/save_workflow.py
# 加 --run 才会真正下发；--port 可指定具体串口
```

流程：连接 → 自检 → 三组 RGB 测量 → 关灯 → 断开。三组 RGB 都是工作流入口参数，
范围 0–1，可直接在参数表单里改（字段已带中文标题）。

**读数去哪了**：每次测量返回 AS7341 八通道原始计数，出现在任务详情的作业结果里，
设备属性 `last_raw` 也会刷新（保留最后一次）。设备动作**不再写** `three-color-data/`
目录，也不生成 `readings.csv`——动作保持无状态，避免重试或重启后会话错乱。

断电或进程被强制结束时无法保证断开，固件看门狗会执行关灯；这不是正常结束流程。

## 真机联调顺序

1. 用普通工作台先确认 Pico 可以自检和读数，再关闭真机工作台，释放串口。
2. 普通插 USB，不按 BOOTSEL。固定遮光和相对位置。
3. 停掉仿真实例，把 `unilab-app.json` 的 graph 换成 `production.json` 后重启 app。
4. 多设备时先在 `production.json` 填写 `serial_number` 或明确 `port`。
5. 在控制台确认设备 `mode` 为 `PHYSICAL`，先调用 `connect`、`doctor`、`stop`、`disconnect` 验收。
6. 在控制台运行「Pocket SDL 三色光谱测量」，核对 3 组 × 8 通道原始读数、灯依次变化、最后灯灭且断开。
7. 失败时保留日志和部分记录，先查线缆、串口占用、固件，不自动切仿真。

## 不依赖 UniLabOS 的驱动预检

```sh
python -m pocket_sdl.three_color --mode SIMULATED
# 真机须确认串口未被占用
python -m pocket_sdl.three_color --mode PHYSICAL
```

这仅验证驱动与记录逻辑，不能代替 UniLabOS 调度和真机验收。
