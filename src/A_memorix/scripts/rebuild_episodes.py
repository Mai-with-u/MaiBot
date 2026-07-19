#!/usr/bin/env python3
"""Episode source 级重建工具。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Dict, List

from _bootstrap import DEFAULT_CONFIG_PATH, DEFAULT_DATA_DIR, resolve_repo_path

try:
    import tomlkit  # type: ignore
except Exception:  # pragma: no cover
    tomlkit = None

from A_memorix.core.storage import MetadataStore
from A_memorix.core.utils.episode_service import EpisodeService


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild A_Memorix episodes by source")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="插件数据目录")
    parser.add_argument("--source", type=str, help="指定单个 source 入队/重建")
    parser.add_argument("--all", action="store_true", help="对所有 source 入队/重建")
    parser.add_argument("--wait", action="store_true", help="在脚本内同步执行重建")
    return parser


if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    raise SystemExit(0)


def _load_plugin_config() -> Dict[str, Any]:
    config_path = DEFAULT_CONFIG_PATH
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


async def _maintain_source_lease(
    store: MetadataStore,
    *,
    source: str,
    lease_token: str,
    claimed_revision: int,
    generation_hash: str,
    lease_seconds: float,
    stop_event: asyncio.Event,
) -> bool:
    heartbeat_interval = max(0.1, lease_seconds / 3.0)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_interval)
        except asyncio.TimeoutError:
            if not store.renew_episode_source_rebuild_lease(
                source,
                lease_token=lease_token,
                claimed_revision=claimed_revision,
                generation_hash=generation_hash,
                lease_seconds=lease_seconds,
            ):
                return False
    return True


async def _run_rebuilds(store: MetadataStore, plugin_config: Dict[str, Any], sources: List[str]) -> int:
    service = EpisodeService(metadata_store=store, plugin_config=plugin_config)
    failures_by_source: Dict[str, str] = {}
    completed_sources = set()
    remaining_sources = list(dict.fromkeys(sources))
    generation = service.generation_signature()
    generation_hash = service.generation_hash(generation)
    lease_seconds = 1800.0
    for _ in remaining_sources.copy():
        claims = store.claim_episode_source_rebuild_batch(
            generation_hash=generation_hash,
            sources=remaining_sources,
            limit=1,
            max_retry=3,
            lease_seconds=lease_seconds,
            max_wait_seconds=0.0,
        )
        if not claims:
            break
        claim = claims[0]
        source = str(claim.get("source", "") or "").strip()
        if source in remaining_sources:
            remaining_sources.remove(source)
        lease_token = str(claim.get("lease_token", "") or "").strip()
        claimed_revision = int(claim.get("claimed_revision", 0) or 0)
        try:
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                _maintain_source_lease(
                    store,
                    source=source,
                    lease_token=lease_token,
                    claimed_revision=claimed_revision,
                    generation_hash=generation_hash,
                    lease_seconds=lease_seconds,
                    stop_event=heartbeat_stop,
                )
            )
            try:
                plan = await service.plan_source_rebuild(source, segmentation_generation=generation)
            finally:
                heartbeat_stop.set()
                await heartbeat_task
            result = store.publish_episode_source_rebuild(
                source,
                lease_token=lease_token,
                claimed_revision=claimed_revision,
                generation_hash=generation_hash,
                episodes_payloads=list(plan.get("payloads") or []),
            )
            if not bool(result.get("published")):
                reason = "superseded" if bool(result.get("superseded")) else "lease_lost_or_claim_mismatch"
                failures_by_source[source] = reason
                print(f"unfinished source={source} reason={reason}")
                continue
            completed_sources.add(source)
            print(
                "rebuilt"
                f" source={source}"
                f" paragraphs={int(plan.get('paragraph_count') or 0)}"
                f" groups={int(plan.get('group_count') or 0)}"
                f" episodes={int(result.get('episode_count') or 0)}"
                f" fallback={int(plan.get('fallback_count') or 0)}"
            )
        except Exception as exc:
            err = str(exc)[:500]
            store.fail_episode_source_rebuild(
                source,
                lease_token=lease_token,
                claimed_revision=claimed_revision,
                error=err,
            )
            failures_by_source[source] = err
            print(f"failed source={source} error={err}")

    for source in sources:
        if source not in completed_sources and source not in failures_by_source:
            failures_by_source[source] = "not_claimed"

    if failures_by_source:
        for source, error in failures_by_source.items():
            print(f"{source}: {error}")
        return 1
    return 0


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if bool(args.all) == bool(args.source):
        parser.error("必须且只能选择一个：--source 或 --all")

    store = MetadataStore(data_dir=resolve_repo_path(args.data_dir, fallback=DEFAULT_DATA_DIR) / "metadata")
    store.connect()
    try:
        sources = _resolve_sources(store, source=args.source, rebuild_all=bool(args.all))
        if not sources:
            print("no sources to rebuild")
            return 0

        enqueued = 0
        reason = "script_rebuild_all" if args.all else "script_rebuild_source"
        for source in sources:
            enqueued += int(
                store.enqueue_episode_source_rebuild(
                    source,
                    reason=reason,
                    debounce_seconds=0.0 if args.wait else 5.0,
                )
            )
        print(f"enqueued={enqueued} sources={len(sources)}")

        if not args.wait:
            return 0

        plugin_config = _load_plugin_config()
        return asyncio.run(_run_rebuilds(store, plugin_config, sources))
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
