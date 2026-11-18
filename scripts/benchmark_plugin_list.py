"""使用同一批磁盘插件对比改动前后的列表接口，不启动插件或连接真实账号。

uv run --no-sync python scripts/benchmark_plugin_list.py --baseline-dir <包含旧 integration.py 和 management.py 的目录>
通过 TestClient 测量 ASGI HTTP 请求，包含状态汇总、文件读取和 JSON 序列化；不含真实网络延迟。
基线文件应使用二进制复制，避免改变换行；先建立新索引，再交替测量两版查询。
"""

from math import ceil
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List

import argparse
import asyncio
import hashlib
import importlib.util
import json
import logging
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugin_runtime.integration import PluginRuntimeManager
from src.webui.routers.plugin.management import _collect_installed_plugins


def load_baseline(directory: Path, name: str, package: str) -> ModuleType:
    path = directory / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{package}._benchmark_baseline_{name}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法导入基线文件: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def attach_runtime_state(manager: Any, builtin_dirs: List[Path], plugin_dirs: List[Path]) -> None:
    """固定两版运行状态，保留真实目录扫描和分组判定，只隔离 Runner 进程。"""
    manager._builtin_supervisor = SimpleNamespace(
        group_name="builtin",
        _plugin_dirs=builtin_dirs + plugin_dirs,
        _trusted_plugin_dirs=builtin_dirs,
        _plugin_type_filter="trusted_or_adapter",
        is_loading=False,
        startup_load_status="not_loaded",
        startup_load_reason="",
    )
    manager._third_party_supervisor = SimpleNamespace(
        group_name="third_party",
        _plugin_dirs=plugin_dirs,
        _trusted_plugin_dirs=[],
        _plugin_type_filter="not_adapter",
        is_loading=False,
        startup_load_status="not_loaded",
        startup_load_reason="",
    )
    manager._started = True
    manager.get_plugin_load_statuses = lambda: {}
    manager.get_plugin_load_failure_reasons = lambda: {}
    manager.get_plugin_circuit_statuses = lambda: {}
    manager.get_plugin_usage_seconds = lambda: {}


def measure(baseline_dir: Path, rounds: int) -> Dict[str, Any]:
    old_runtime = load_baseline(baseline_dir, "integration", "src.plugin_runtime")
    old_routes = load_baseline(baseline_dir, "management", "src.webui.routers.plugin")
    builtin_dirs = PluginRuntimeManager._get_builtin_plugin_dirs()
    plugin_dirs = PluginRuntimeManager._get_third_party_plugin_dirs()
    before = old_runtime.PluginRuntimeManager()
    after = PluginRuntimeManager()
    for manager in (before, after):
        attach_runtime_state(manager, builtin_dirs, plugin_dirs)

    after._plugin_catalog._roots = {path: True for path in builtin_dirs}
    after._plugin_catalog._roots.update({path: False for path in plugin_dirs})
    started = time.perf_counter()
    asyncio.run(after._plugin_catalog.refresh())
    index_build_ms = (time.perf_counter() - started) * 1000

    app = FastAPI()

    @app.get("/before")
    async def get_before():
        snapshot = await before.get_plugin_state_snapshot()
        return await asyncio.to_thread(old_routes._collect_installed_plugins, snapshot)

    @app.get("/after")
    async def get_after():
        snapshot = await after.get_plugin_state_snapshot()
        return _collect_installed_plugins(snapshot)

    timings: Dict[str, List[float]] = {"before": [], "after": []}
    payloads: Dict[str, Any] = {}
    sizes: Dict[str, int] = {}
    with TestClient(app) as client:
        for index in range(rounds + 1):
            for version in ("before", "after") if index % 2 == 0 else ("after", "before"):
                started = time.perf_counter()
                response = client.get(f"/{version}")
                elapsed = (time.perf_counter() - started) * 1000
                response.raise_for_status()
                if index:
                    timings[version].append(elapsed)
                payloads[version] = response.json()
                sizes[version] = len(response.content)
    # 更新日志已改为详情按需读取；其余列表字段和状态必须保持一致。
    for plugin in payloads["before"]["plugins"]:
        plugin.pop("changelog", None)
    assert payloads["before"] == payloads["after"], "两版列表内容不一致，不能比较速度"
    return {
        "method": "alternating ASGI HTTP requests; first pair excluded; no Runner or real network",
        "rounds": rounds,
        "plugin_count": payloads["after"]["total"],
        "index_build_ms": round(index_build_ms, 3),
        "baseline_sha256": {
            name: hashlib.sha256((baseline_dir / name).read_bytes()).hexdigest()
            for name in ("integration.py", "management.py")
        },
        "response_bytes": sizes,
        "samples_ms": {key: [round(value, 3) for value in values] for key, values in timings.items()},
        "median_ms": {key: round(statistics.median(values), 3) for key, values in timings.items()},
        "p95_ms": {key: round(sorted(values)[ceil(len(values) * 0.95) - 1], 3) for key, values in timings.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 2:
        parser.error("--rounds 至少为 2")
    logging.disable(logging.CRITICAL)
    result = measure(args.baseline_dir, args.rounds)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
