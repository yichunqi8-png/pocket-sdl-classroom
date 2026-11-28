# Pocket SDL 离线课堂包构建与分发说明

面向无法访问 GitHub 或不便现场安装 Python 的 Windows / Mac 课堂电脑。

## 学生使用方式

完整解压对应平台 ZIP，双击 `Start Simulation`（无设备仿真）或 `Start Real Device`（连接真实 Pico）。包内已内置 Python、NumPy、pyserial、界面与接线图，无需安装 Git、Python 或 pip，运行时不联网。详细指引见包内随附的 [离线包使用说明.txt](离线包使用说明.txt)（打包后位于解压根目录的 `先读我.txt`）。

- `Windows-x64`：Windows 10/11 x64
- `Mac-Apple`：Apple 芯片（M 系列）
- `Mac-Intel`：Intel 芯片

> 真机仍需助教预先刷好 Pico 固件。UniLabOS.app 拓展环境不包含在离线包中。

## 打包与自动验证

工作流 [.github/workflows/offline.yml](../.github/workflows/offline.yml) 在三个平台分别构建（Python 3.12、NumPy 2.2.6、pyserial 3.5、PyInstaller 6.16.0）：

- `packaging/build_offline.py` 生成独立运行目录、双击入口、SHA-256 校验文件和 ZIP（macOS ZIP 保留框架符号链接，仅打包运行时所需的配置、页面与三张实物接线 PNG）。
- `packaging/smoke_offline.py` 在含中文和空格的临时目录中运行冻结程序，清空 `PATH` / `PYTHONPATH`，验证页面、接线图、三色仿真测量与数据落盘。

## 数据保存位置

- Windows：`%LOCALAPPDATA%\Pocket SDL\classroom-data`
- macOS：`~/Library/Application Support/Pocket SDL/classroom-data`

程序升级不会删除上述数据目录，学生也可在任务七导出 JSON / JSONL / CSV 记录。
