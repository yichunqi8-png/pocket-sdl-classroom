# SDL Workshop 实时交互台与教学 Notebook

面向非技术/跨学科观众的 **Raspberry Pi Pico H + AS7341 + WS2812B** 现场互动套件，**零新增第三方依赖**（仅复用仓库原有的 `numpy`、`pyserial` 与 Python 标准库），支持 **真机（`PHYSICAL`）** 与 **仿真彩排（`SIMULATED`）** 双模式。

## 1. 快速启动本地 Web 实时交互台

先激活已安装依赖的 Python 环境（例如按根目录说明创建的 `.venv`），在仓库根目录执行：

```bash
# 方式 A：零硬件彩排 / 仿真演示
python workshop/server.py --mode simulated --port 8765 --open

# 方式 B：真机现场演示（先用 python -m pocket_sdl list-ports 核对 CDC data 串口，并将 XXXX 替换为实际编号）
python workshop/server.py --mode physical --serial /dev/cu.usbmodemXXXX --host 0.0.0.0 --port 8765 --open
```

- **大屏主控台**：`http://127.0.0.1:8765/`
- **观众扫码页**：加 `--host 0.0.0.0` 启动后，切到 Tab 3 即可显示离线二维码，同一 Wi-Fi 下手机扫码打开 `/mobile` 即可给 Pico 实验室出题。
- **硬件安全**：单次测量后自动关灯；连续实时流每 ~0.7s 持续采样，自动兼容固件 5 秒看门狗；遇到饱和（`>=60000`）或异常立即关灯并弹出提示，按 `Ctrl-C` 退出时自动安全关灯。

## 2. 配套 Jupyter Notebook

直接用 JupyterLab 或 VS Code（需安装 Jupyter 扩展 / `ipykernel`）打开 [SDL_Workshop.ipynb](SDL_Workshop.ipynb)：
- 顶部 `MODE = "SIMULATED"` 默认零硬件可跑通全部代码与内联彩虹光谱/收敛曲线图表（无需安装 matplotlib）。
- 改为 `MODE = "PHYSICAL"` + 你的串口号即可在 Notebook 内直接驱动真实 Pico H。

## 3. 建议的 25 分钟 Workshop 节奏

1. **0–5 min · 破冰「彩虹透视眼」（Tab 1 实时分光仪）**
   - 点 `关灯/环境` + `开启实时连续采样`，打开盒盖让观众把手机白屏、荧光笔、饮料瓶靠近 AS7341；点击「锁定对比虚线」对比手机白光三尖峰与自然/台灯连续谱。
   - 再拖动 RGB 滑块，让观众直观看到 LED 变色与 `415–680nm` 8 根柱子的联动。
2. **5–15 min · 高潮「人机调色擂台」（Tab 2 人机擂台）**
   - 盖紧暗盒，点击顶部「快速校准」→「随机神秘盲盒目标」。
   - 邀请 1–2 位观众上台用 10 次预算盲调 R/G/B 滑块，感受高维调配方的难度。
   - 同题一键启动 `Random` 与 `BO (GP-EI)`，大屏同步画出三条收敛曲线并打开「BO 大脑透视」讲解探索与开发，最后点击「揭晓谜底」。
3. **15–22 min · 全场互动「观众扫码出题」（Tab 3 云端实验队列）**
   - 观众扫码选色提交到队列，开启「自动接单」，每 11 秒左右复刻完一位观众的颜色并登上成果墙。
4. **22–25 min · 彩蛋「给 AI 捣乱」与 Notebook 带回家**
   - 故意掀起暗盒盖漏光观察曲线波动/饱和报警，顺势引出真实实验室的校准与质量门；把 `SDL_Workshop.ipynb` 发给有兴趣动手跑代码的观众。
