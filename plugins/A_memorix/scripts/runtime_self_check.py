#!/usr/bin/env python3
"""Run A_Memorix runtime self-check against real embedding/runtime configuration."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import tomlkit


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A_Memorix runtime self-check")
    parser.add_argument(
        "--config",
        default=str(PLUGIN_ROOT / "config.toml"),
        help="config.toml path (default: plugins/A_memorix/config.toml)",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="optional data dir override; default resolved from config.storage.data_dir",
    )
    parser.add_argument(
        "--use-config-data-dir",
        action="store_true",
        help="use config.storage.data_dir directly instead of an isolated temp dir",
    )
    parser.add_argument(
        "--sample-text",
        default="A_Memorix runtime self check",
        help="sample text used for real embedding probe",
    )
    parser.add_argument("--json", action="store_true", help="print JSON report")
    return parser


if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    raise SystemExit(0)

from core.runtime.lifecycle_orchestrator import initialize_storage_async
from core.utils.runtime_self_check import run_embedding_runtime_self_check


def _load_config(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        raw = tomlkit.load(f)
    return dict(raw) if isinstance(raw, dict) else {}


def _nested_get(config: dict[str, Any], key: str, default: Any = None) -> Any:
    current: Any = config
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


class _PluginStub:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.vector_store = None
        self.graph_store = None
        self.metadata_store = None
        self.embedding_manager = None
        self.sparse_index = None
        self.relation_write_service = None

    def get_config(self, key: str, default: Any = None) -> Any:
        return _nested_get(self.config, key, default)


async def _main_async(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        return 2

    config = _load_config(config_path)
    temp_dir_ctx = None
    if args.data_dir:
        storage_dir = str(Path(args.data_dir).resolve())
    elif args.use_config_data_dir:
        raw_data_dir = str(_nested_get(config, "storage.data_dir", "./data") or "./data").strip()
        if raw_data_dir.startswith("."):
            storage_dir = str((config_path.parent / raw_data_dir).resolve())
        else:
            storage_dir = str(Path(raw_data_dir).resolve())
    else:
        temp_dir_ctx = tempfile.TemporaryDirectory(prefix="memorix-runtime-self-check-")
        storage_dir = temp_dir_ctx.name

    storage_cfg = config.setdefault("storage", {})
    storage_cfg["data_dir"] = storage_dir

    plugin = _PluginStub(config)
    try:
        await initialize_storage_async(plugin)
        report = await run_embedding_runtime_self_check(
            config=config,
            vector_store=plugin.vector_store,
            embedding_manager=plugin.embedding_manager,
            sample_text=str(args.sample_text or "A_Memorix runtime self check"),
        )
        report["data_dir"] = storage_dir
        report["isolated_data_dir"] = temp_dir_ctx is not None
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("A_Memorix Runtime Self-Check")
            print(f"ok: {report.get('ok')}")
            print(f"code: {report.get('code')}")
            print(f"message: {report.get('message')}")
            print(f"configured_dimension: {report.get('configured_dimension')}")
            print(f"vector_store_dimension: {report.get('vector_store_dimension')}")
            print(f"detected_dimension: {report.get('detected_dimension')}")
            print(f"encoded_dimension: {report.get('encoded_dimension')}")
            print(f"elapsed_ms: {float(report.get('elapsed_ms', 0.0)):.2f}")
        return 0 if bool(report.get("ok")) else 1
    finally:
        if plugin.metadata_store is not None:
            try:
                plugin.metadata_store.close()
            except Exception:
                pass
        if temp_dir_ctx is not None:
            temp_dir_ctx.cleanup()


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
