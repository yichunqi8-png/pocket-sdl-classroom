"""用已安装的 UniLabOS.app 打开本设备包工作区。

启动权归 app：本脚本只设置 UNILABOS_WORKSPACE 并拉起 app，不再自行启动
backend，也不再手写 community_devices/manifest.json。
"""

import os
import subprocess
from pathlib import Path

APP = Path("/Applications/UniLabOS.app/Contents/MacOS/UniLabOS")
WORKSPACE = Path(__file__).resolve().parents[1]

if not APP.is_file():
    raise SystemExit("未找到 UniLabOS.app，请先安装 dmg。")
if not (WORKSPACE / "unilab-app.json").is_file():
    raise SystemExit(f"工作区不完整：{WORKSPACE}")

environment = dict(os.environ, UNILABOS_WORKSPACE=str(WORKSPACE))
raise SystemExit(subprocess.call([str(APP)], env=environment))
