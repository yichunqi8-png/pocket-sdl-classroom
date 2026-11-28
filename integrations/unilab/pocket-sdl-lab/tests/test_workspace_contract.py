"""工作区身份、启动文件、设备与工作流合同的静态验证。"""

import json
from pathlib import Path

from pocket_sdl_lab.devices.pico_spectro import PicoSpectroStation
from unilabos.package_manager import (
    WorkspaceSource,
    compile_package_source,
    compile_workspace_startup,
)


WORKSPACE = Path(__file__).parents[1]


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
