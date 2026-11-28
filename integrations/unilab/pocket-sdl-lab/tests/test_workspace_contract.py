"""工作区身份、启动文件、设备与工作流合同的静态验证。"""

import importlib.util
import json
import sqlite3
from pathlib import Path

from pocket_sdl_lab.devices.pico_spectro import PicoSpectroStation
from unilabos.package_manager import (
    WorkspaceSource,
    compile_package_source,
    compile_workspace_startup,
)


WORKSPACE = Path(__file__).parents[1]


def _load_local_config():
    spec = importlib.util.spec_from_file_location(
        "test_pocket_sdl_local_config", WORKSPACE / "deployment" / "local_config.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_inventory_db(runtime_dir: Path, source_name: str) -> Path:
    db_path = runtime_dir / "inventory.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE lab_meta (id INTEGER PRIMARY KEY, resource_graph_bootstrap_source TEXT)"
        )
        conn.execute(
            "INSERT INTO lab_meta (id, resource_graph_bootstrap_source) VALUES (1, ?)",
            (source_name,),
        )
        conn.commit()
    finally:
        conn.close()
    Path(f"{db_path}-wal").write_text("wal", encoding="utf-8")
    Path(f"{db_path}-shm").write_text("shm", encoding="utf-8")
    return db_path


def test_workspace_identity_and_startup_files() -> None:
    source = WorkspaceSource(WORKSPACE)
    plan = compile_workspace_startup(source)
    assert plan.distribution_name == "pocket-sdl-lab"
    assert plan.import_package == "pocket_sdl_lab"
    assert plan.workflow_source_count == 1

    graph = json.loads(plan.resolve_graph(plan.default_graph).read_text(encoding="utf-8"))
    assert graph["nodes"][0]["id"] == "pico_spectro_01"
    assert graph["nodes"][0]["class"] == "community.pocket_sdl_lab.pico_spectro"
    assert graph["links"] == []

    catalog = compile_package_source(source)
    assert [item.id for item in catalog.definitions.devices] == ["pico_spectro"]
    assert [item.id for item in catalog.definitions.workflows] == ["three_color"]


def test_simulated_device_is_constructible_without_hardware() -> None:
    station = PicoSpectroStation(mode="SIMULATED")
    assert station.mode == "SIMULATED"
    assert station.connected is False


def test_inventory_reset_only_when_switching_managed_graphs(tmp_path: Path) -> None:
    local_config = _load_local_config()
    (tmp_path / "unilab-app.json").write_text(
        json.dumps({"startup": {"graph": "deployment/graphs/dry-run.json"}}),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "local-domain"
    runtime_dir.mkdir()
    history_db = runtime_dir / "workflow_history.db"
    history_db.write_text("keep", encoding="utf-8")

    # 同图不清理
    db = _make_inventory_db(runtime_dir, "dry-run.json")
    assert local_config._reset_stale_inventory_on_graph_switch(tmp_path, runtime_dir, []) is False
    assert db.exists()

    # 跨图（production.json -> dry-run.json）自动清理 inventory.db*，保留 workflow_history.db
    db.unlink()
    db = _make_inventory_db(runtime_dir, "production.json")
    assert local_config._reset_stale_inventory_on_graph_switch(tmp_path, runtime_dir, []) is True
    assert not db.exists()
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()
    assert history_db.read_text(encoding="utf-8") == "keep"

    # CLI --graph 优先于 unilab-app.json；非受管图名不清理
    db = _make_inventory_db(runtime_dir, "custom.json")
    assert (
        local_config._reset_stale_inventory_on_graph_switch(
            tmp_path, runtime_dir, ["--graph", "deployment/graphs/production.json"]
        )
        is False
    )
    assert db.exists()
