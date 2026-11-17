"""QQ 号迁回 OpenID 的完成状态；数据合并仍由本体在线资料迁移接口执行。"""

from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List

import sqlite3
import tomlkit

from src.person_info.identity import load_aliases, resolve_alias
from src.services.person_migration_service import _atomic_write


def migration_candidates(root: Path, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 QQ 号聚合目标，显式保留多目标选择及冲突。"""
    candidates: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row["source"] == "union":
            continue
        source, target = row["id"], row["openid"]
        item = candidates.setdefault(source, {"source": source, "targets": [], "conflict": False})
        if target not in item["targets"]:
            item["targets"].append(target)
        item["conflict"] = item["conflict"] or row["conflict"]
    aliases = load_aliases(root / "data/person_identity_aliases.json")["aliases"].get("qq", {})
    with closing(sqlite3.connect((root / "data/MaiBot.db").resolve().as_uri() + "?mode=ro", uri=True)) as database:
        sources_with_data = {
            record[0]
            for table in ("person_info",)
            for record in database.execute(f"SELECT DISTINCT user_id FROM {table} WHERE platform = ?", ("qq",))
        }
        for source, item in candidates.items():
            canonical = resolve_alias(aliases, source)
            has_data = source in sources_with_data
            item["target"] = canonical if canonical != source else ""
            item["status"] = ("migrated" if canonical in item["targets"] and not has_data else
                              "conflict" if item["conflict"] or canonical != source else
                              "pending" if has_data else "empty")
    return list(candidates.values())


def save_mapping_mode(path: Path, enabled: bool) -> Dict[str, Any]:
    """仅保存用户映射模式，保留 QQ 号关系及静默同步配置。"""
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["qq_official"]["enable_mapping"] = enabled
    _atomic_write(path, tomlkit.dumps(document))
    return document.unwrap()


def disable_mapping(path: Path) -> Dict[str, Any]:
    """迁移完成后关闭用户映射。"""
    return save_mapping_mode(path, False)
