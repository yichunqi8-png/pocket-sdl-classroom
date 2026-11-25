#!/bin/bash
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
PYTHON=""
for candidate in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,10))' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo '请先安装 Python 3.10+（推荐 3.12），再双击本文件。下载：https://www.python.org/downloads/'
else
    "$PYTHON" launcher/start.py --mode simulated --port 8772 --audience-port 0 "$@"
fi
read -r -p '按回车关闭此窗口……'
