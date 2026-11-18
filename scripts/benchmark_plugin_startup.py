"""对比旧版 Runner 与当前 Runner 的完整插件加载路径，不连接真实账号或消息平台。

uv run --no-sync python scripts/benchmark_plugin_startup.py --baseline-runner <旧 runner_main.py> --output <结果.json>
使用临时插件、真实扫描/导入/配置/初始化代码；仅将 Host RPC 替换为本地应答。
结果是合成负载的进程内暖启动耗时，不包含 Python 起进程、依赖安装或真实网络延迟。
"""

from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Tuple, Type

import argparse
import asyncio
import hashlib
import importlib.util
import json
import platform
import statistics
import sys
import tempfile
import time

# 支持直接运行 scripts/ 下的脚本。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.plugin_runtime.protocol.envelope import Envelope, MessageType, RunnerReadyPayload
from src.plugin_runtime.runner.runner_main import PluginRunner


class LocalHost:
    """本地应答 RPC；保留每个请求的异步调度边界。"""

    def __init__(self, runner: Any) -> None:
        self.runner = runner
        self.ready: RunnerReadyPayload | None = None

    async def connect_and_handshake(self) -> bool:
        return True

    def register_method(self, method: str, handler: Any) -> None:
        pass

    async def send_event(self, method: str, **kwargs: Any) -> None:
        await asyncio.sleep(0)

    async def send_request(self, method: str, **kwargs: Any) -> Envelope:
        await asyncio.sleep(0)
        if method == "runner.ready":
            self.ready = RunnerReadyPayload.model_validate(kwargs["payload"])
            self.runner._shutting_down = True
        return Envelope(request_id=1, message_type=MessageType.RESPONSE, method=method, payload={"accepted": True})

    async def disconnect(self) -> None:
        pass


def load_baseline(path: Path) -> Type[Any]:
    spec = importlib.util.spec_from_file_location("_plugin_startup_baseline", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法导入基线 Runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.PluginRunner


def write_plugins(root: Path, case: str) -> int:
    """创建固定工作量，依赖插件的 on_load 必须先成功结束。"""
    count = 24 if case in {"independent_io", "four_chains", "single_chain"} else 12
    for index in range(count):
        plugin_id = f"bench.p{index:02d}"
        dependencies = []
        if case == "four_chains" and index >= 4:
            dependencies = [f"bench.p{index - 4:02d}"]
        elif case == "single_chain" and index:
            dependencies = [f"bench.p{index - 1:02d}"]
        plugin_dir = root / plugin_id
        plugin_dir.mkdir()
        manifest = {
            "manifest_version": 2,
            "id": plugin_id,
            "version": "1.0.0",
            "name": plugin_id,
            "description": "startup benchmark",
            "author": {"name": "benchmark", "url": "https://example.com"},
            "license": "GPL-v3.0-or-later",
            "urls": {"repository": "https://example.com/repo"},
            "host_application": {"min_version": "1.0.0", "max_version": "9.9.9"},
            "sdk": {"min_version": "2.0.0", "max_version": "9.9.9"},
            "capabilities": [],
            "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
            "dependencies": [{"type": "plugin", "id": dep, "version_spec": ">=1.0.0"} for dep in dependencies],
        }
        (plugin_dir / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        source = (
            "import asyncio\nimport time\nimport _startup_benchmark_state as state\n"
            + ("time.sleep(0.01)\n" if case == "import_bound" else "")
            + "class Plugin:\n"
            "    async def on_load(self):\n"
            f"        assert all(dep in state.finished for dep in {dependencies!r})\n"
            "        state.active += 1\n"
            "        state.peak = max(state.peak, state.active)\n"
            + (
                "        await asyncio.sleep(0.03)\n"
                if case in {"independent_io", "four_chains", "single_chain"}
                else ""
            )
            + (
                "        until = time.perf_counter() + 0.005\n        while time.perf_counter() < until: pass\n"
                if case == "cpu_bound"
                else ""
            )
            + "        state.active -= 1\n"
            f"        state.finished.add({plugin_id!r})\n"
            "    async def on_unload(self): pass\n"
            "def create_plugin(): return Plugin()\n"
        )
        (plugin_dir / "plugin.py").write_text(source, encoding="utf-8")
    return count


async def measure(runner_class: Type[Any], root: Path, expected: int) -> Tuple[float, int]:
    state = ModuleType("_startup_benchmark_state")
    state.active, state.peak, state.finished = 0, 0, set()
    sys.modules[state.__name__] = state
    runner = runner_class("unused", "benchmark", [str(root)])
    host = LocalHost(runner)
    runner._rpc_client = host
    runner._install_log_handler = lambda: None

    async def uninstall_log_handler() -> None:
        pass

    runner._uninstall_log_handler = uninstall_log_handler
    started = time.perf_counter()
    try:
        await runner.run()
        elapsed = time.perf_counter() - started
        assert host.ready is not None
        assert not host.ready.failed_plugins, host.ready.failed_plugin_reasons
        assert len(host.ready.loaded_plugins) == expected
        assert len(state.finished) == expected
        return elapsed, state.peak
    finally:
        for plugin_id in runner._loader.list_plugins():
            meta = runner._loader.get_plugin(plugin_id)
            # 固定夹具仅生成入口模块，没有私有子模块；清理不计入启动耗时。
            sys.modules.pop(meta.module_name, None)
        sys.modules.pop(state.__name__, None)


async def benchmark(baseline_path: Path, repeats: int) -> Dict[str, Any]:
    baseline = load_baseline(baseline_path)
    results: Dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeats": repeats,
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "current_sha256": hashlib.sha256(Path(sys.modules[PluginRunner.__module__].__file__).read_bytes()).hexdigest(),
        "scope": "in-process warm startup; real discovery/import/config/activation; local Host RPC; synthetic plugins",
        "cases": {},
    }
    for case in ("independent_io", "four_chains", "single_chain", "import_bound", "cpu_bound", "no_op"):
        with tempfile.TemporaryDirectory(prefix="maibot-startup-bench-") as directory:
            root = Path(directory)
            count = write_plugins(root, case)
            samples: Dict[str, List[float]] = {"before": [], "after": []}
            peaks: Dict[str, List[int]] = {"before": [], "after": []}
            # 各预热一次，并交替测量顺序，降低首轮导入和缓存顺序对结果的偏差。
            await measure(baseline, root, count)
            await measure(PluginRunner, root, count)
            for index in range(repeats):
                variants = [("before", baseline), ("after", PluginRunner)]
                if index % 2:
                    variants.reverse()
                for label, runner_class in variants:
                    elapsed, peak = await measure(runner_class, root, count)
                    samples[label].append(elapsed)
                    peaks[label].append(peak)
            before, after = statistics.median(samples["before"]), statistics.median(samples["after"])
            results["cases"][case] = {
                "plugins": count,
                "before_sec": before,
                "after_sec": after,
                "speedup": before / after,
                "samples_sec": samples,
                "peak_active_on_load": peaks,
            }
            print(f"{case}: {before:.3f}s -> {after:.3f}s ({before / after:.2f}x)", flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-runner", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats 必须大于 0")
    results = asyncio.run(benchmark(args.baseline_runner, args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results: {args.output}")


if __name__ == "__main__":
    main()
