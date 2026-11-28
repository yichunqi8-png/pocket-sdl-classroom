"""在工作区已启动的 UniLabOS 后端上创建一次性三色测量任务。

工作流由包清单在启动时自动注册（不再有模板实例化步骤），因此本脚本只做两件事：
按名称找到已注册工作流，再按需创建任务。``--run`` 必须显式给出才会真正下发。
"""

import argparse
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_NAME = "Pocket SDL 三色光谱测量"


def discover_backend_url(workspace: Path) -> str:
    """从最新后端日志里读取本次启动的实际端口；日志缺失时给出明确错误。"""

    logs = sorted(
        (workspace / ".unilabos" / "runtime" / "workbench" / "backend" / "local-domain" / "logs").glob("*.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for log in logs:
        match = re.search(r"http://127\.0\.0\.1:\d+", log.read_text(encoding="utf-8", errors="ignore"))
        if match:
            return match.group(0)
    raise SystemExit("未找到运行中的后端日志，请先在 app 里启动工作区，或用 --url 手动指定地址。")


def call(base: str, path: str, data=None):
    """调用公共接口并统一处理业务错误码。"""

    request = Request(
        f"{base}/api/v1{path}",
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("code") != 0:
        raise SystemExit(f"接口失败 {path}: {result}")
    return result["data"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按名称找到已注册的三色工作流并可选地创建任务。",
        epilog="示例：python deployment/save_workflow.py --run --port auto",
    )
    parser.add_argument("--url", default=None, help="后端地址；省略时从最新后端日志自动读取")
    parser.add_argument("--name", default=DEFAULT_NAME, help="工作流展示名")
    parser.add_argument("--port", default="auto", help="串口；auto 表示自动探测 Pico 数据口")
    parser.add_argument("--run", action="store_true", help="立即创建并下发任务（默认只打印工作流信息）")
    args = parser.parse_args()

    workspace = Path(__file__).resolve().parents[1]
    base = args.url or discover_backend_url(workspace)

    workflows = call(base, "/workflows?page=1&page_size=100")["items"]
    matched = next((item for item in workflows if item.get("name") == args.name), None)
    if matched is None:
        names = ", ".join(sorted(item.get("name", "") for item in workflows)) or "（无）"
        raise SystemExit(f"未找到工作流 {args.name!r}；当前可用：{names}")

    print(json.dumps({"workflow_uuid": matched["uuid"], "name": matched["name"]}, ensure_ascii=False))
    if not args.run:
        return 0

    task = call(
        base,
        "/workflow-tasks",
        {"workflow_uuid": matched["uuid"], "run_mode": "normal", "input": {"port": args.port}},
    )
    print(json.dumps(task, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
