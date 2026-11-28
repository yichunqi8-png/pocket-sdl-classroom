@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 scripts\start_classroom.py %*
    goto done
)
where python >nul 2>nul
if not errorlevel 1 (
    python scripts\start_classroom.py %*
    goto done
)
echo 请先安装 Python 3.10+，推荐 3.12：https://www.python.org/downloads/
:done
pause
