#!/usr/bin/env python3
"""Episode source 级重建工具。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
WORKSPACE_ROOT = PLUGIN_ROOT.parent
MAIBOT_ROOT = WORKSPACE_ROOT / "MaiBot"
for path in (WORKSPACE_ROOT, MAIBOT_ROOT, PLUGIN_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

try:
    import tomlkit  # type: ignore
except Exception:  # pragma: no cover
    tomlkit = None

from A_memorix.core.storage import MetadataStore
from A_memorix.core.utils.episode_service import EpisodeService


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild A_Memorix episodes by source")
    parser.add_argument("--data-dir", default=str(PLUGIN_ROOT / "data"), help="插件数据目录")
    parser.add_argument("--source", type=str, help="指定单个 source 入队/重建")
    parser.add_argument("--all", action="store_true", help="对所有 source 入队/重建")
    parser.add_argument("--wait", action="store_true", help="在脚本内同步执行重建")
    return parser


if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    raise SystemExit(0)


def _load_plugin_config() -> Dict[str, Any]:
    config_path = PLUGIN_ROOT / "config.toml"
    if tomlkit is None or not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            parsed = tomlkit.load(handle)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _resolve_sources(store: MetadataStore, *, source: str | None, rebuild_all: bool) -> List[str]:
    if rebuild_all:
        return list(store.list_episode_sources_for_rebuild())
    token = str(source or "").strip()
    if not token:
        raise ValueError("必须提供 --source 或 --all")
    return [token]


async def _run_rebuilds(store: MetadataStore, plugin_config: Dict[str, Any], sources: List[str]) -> int:
    service = EpisodeService(metadata_store=store, plugin_config=plugin_config)
    failures: List[str] = []
    for source in sources:
        started = store.mark_episode_source_running(source)
        if not started:
            failures.append(f"{source}: unable_to_mark_running")
            continue
        try:
            result = await service.rebuild_source(source)
            store.mark_episode_source_done(source)
            print(
                "rebuilt"
                f" source={source}"
                f" paragraphs={int(result.get('paragraph_count') or 0)}"
                f" groups={int(result.get('group_count') or 0)}"
                f" episodes={int(result.get('episode_count') or 0)}"
                f" fallback={int(result.get('fallback_count') or 0)}"
            )
        except Exception as exc:
            err = str(exc)[:500]
            store.mark_episode_source_failed(source, err)
            failures.append(f"{source}: {err}")
            print(f"failed source={source} error={err}")

    if failures:
        for item in failures:
            print(item)
        return 1
    return 0


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if bool(args.all) == bool(args.source):
        parser.error("必须且只能选择一个：--source 或 --all")

    store = MetadataStore(data_dir=Path(args.data_dir) / "metadata")
    store.connect()
    try:
        sources = _resolve_sources(store, source=args.source, rebuild_all=bool(args.all))
        if not sources:
            print("no sources to rebuild")
            return 0

        enqueued = 0
        reason = "script_rebuild_all" if args.all else "script_rebuild_source"
        for source in sources:
            enqueued += int(store.enqueue_episode_source_rebuild(source, reason=reason))
        print(f"enqueued={enqueued} sources={len(sources)}")

        if not args.wait:
            return 0

        plugin_config = _load_plugin_config()
        return asyncio.run(_run_rebuilds(store, plugin_config, sources))
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
