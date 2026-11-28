"""本地运行配置（仅包含非敏感设置；密钥须通过环境变量注入，不得写入代码）。"""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Sequence

_MANAGED_GRAPHS = frozenset({"dry-run.json", "production.json"})


class BasicConfig:
    disable_browser = True
    no_update_feedback = True
    log_level = "INFO"


LOG_LEVEL = "INFO"
AUTO_OPEN_BROWSER = False
AK = ""
SK = ""


def _arg_value(argv: Sequence[str], flag: str) -> Optional[str]:
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    return None


def _resolve_target_graph(workspace: Path, argv: Sequence[str]) -> Optional[str]:
    cli_graph = _arg_value(argv, "--graph") or _arg_value(argv, "-g")
    if cli_graph:
        return Path(cli_graph).name
    app_config = workspace / "unilab-app.json"
    if not app_config.is_file():
        return None
    try:
        data = json.loads(app_config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    graph = (data.get("startup") or {}).get("graph")
    return Path(graph).name if isinstance(graph, str) and graph.strip() else None


def _read_bootstrap_source(inventory_db: Path) -> Optional[str]:
    try:
        conn = sqlite3.connect(f"file:{inventory_db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT resource_graph_bootstrap_source FROM lab_meta WHERE id = 1"
            ).fetchone()
            return str(row[0]).strip() if row and row[0] else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _reset_stale_inventory_on_graph_switch(
    workspace: Optional[Path] = None,
    runtime_dir: Optional[Path] = None,
    argv: Optional[Sequence[str]] = None,
) -> bool:
    """在仿真图（dry-run.json）与真机图（production.json）切换时自动清理冲突的 inventory.db。

    UniLabOS 的库存自举会拒绝不同资源图来源覆盖既有 ``inventory.db``；此处仅在确认
    ``lab_meta`` 记录的来源与本次启动图同属受管图且名称不同时删除 ``inventory.db*``，
    保留 ``workflow_history.db`` 等历史运行记录。
    """

    root = workspace or Path(__file__).resolve().parents[1]
    args = list(sys.argv[1:] if argv is None else argv)
    target_graph = _resolve_target_graph(root, args)
    if target_graph not in _MANAGED_GRAPHS:
        return False

    if runtime_dir is None:
        cli_workdir = _arg_value(args, "--working_dir")
        if cli_workdir:
            runtime_dir = Path(cli_workdir)
        else:
            runtime_dir = (
                root / ".unilabos" / "runtime" / "workbench" / "backend" / "local-domain"
            )

    inventory_db = runtime_dir / "inventory.db"
    if not inventory_db.is_file():
        return False

    stored_source = _read_bootstrap_source(inventory_db)
    if stored_source not in _MANAGED_GRAPHS or stored_source == target_graph:
        return False

    for suffix in ("", "-wal", "-shm"):
        Path(f"{inventory_db}{suffix}").unlink(missing_ok=True)
    print(
        f"[INFO] 检测到资源图切换 ({stored_source} -> {target_graph})，"
        f"已自动清理旧库存库以重建投影: {inventory_db}",
        flush=True,
    )
    return True


if __name__ == "lab_local_config":
    _reset_stale_inventory_on_graph_switch()
