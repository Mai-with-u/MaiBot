"""已发布单实例配置直接迁入最终工作区结构：统一校验、备份和事务提交。"""
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

import json
import tomlkit

from src.common.logger import get_logger
from src.common.process_lease import ProcessLease
from src.config.shared_models import CATALOG_FILENAME, CATALOG_VERSION

from .config_migration import plan_host_settings
from .config_transaction import commit_config_files, recover_config_transaction, write_private
from .model_migration import plan_model_catalog
from .registry import BotRecord
from .retention_migration import plan_retention


logger = get_logger("host.workspace_migration")


def migrate_workspace_config(root: Path, bots: List[BotRecord]) -> None:
    host = root / "host"
    with ProcessLease(host / "model-config.lock"):
        recover_config_transaction(host)
        host_path = host / "config.toml"
        catalog_path = host / CATALOG_FILENAME
        originals: Dict[Path, str] = {}
        documents = {}
        paths = [host_path, catalog_path]
        snapshots: List[Path] = []
        for bot in bots:
            if bot.initialization == "initialized":
                paths.extend(bot.directory / "config" / name for name in ("bot_config.toml", "model_config.toml"))
                snapshots.extend(sorted((bot.directory / "config" / "versions" / "model").glob("*.toml")))
        for path in paths:
            if path.exists():
                try:
                    originals[path] = path.read_bytes().decode("utf-8")
                    documents[path] = tomlkit.parse(originals[path])
                except (UnicodeError, tomlkit.exceptions.ParseError) as exc:
                    raise ValueError(f"当前配置无法解析：{path}") from exc
            elif path == catalog_path:
                documents[path] = tomlkit.document()
            else:
                raise ValueError(f"实例配置缺失: {path}")
        for path in snapshots:
            try:
                content = path.read_bytes().decode("utf-8")
                document = tomlkit.parse(content)
            except (OSError, UnicodeError, tomlkit.exceptions.ParseError):
                logger.exception(f"历史模型配置无法读取，已跳过迁移：{path}")
                continue
            originals[path] = content
            documents[path] = document
        if catalog_path in originals and documents[catalog_path].get("inner", {}).get("version") != CATALOG_VERSION:
            raise ValueError("不支持的公共模型配置版本")
        local_bots = {path: doc for path, doc in documents.items() if path.name == "bot_config.toml"}
        plan_host_settings(host_path, documents[host_path], local_bots)
        plan_retention(documents[host_path], local_bots)
        plan_model_catalog(documents[catalog_path], bots, documents)
        changes = {
            path: tomlkit.dumps(doc) for path, doc in documents.items()
            if path not in originals or doc.unwrap() != tomlkit.parse(originals[path]).unwrap()
        }
        if not changes:
            return
        # 全部转换与序列化成功后才备份；同一批旧文件对应一个恢复清单。
        backup = host / "backups" / ("workspace-migration-" + uuid4().hex)
        manifest = []
        for index, path in enumerate(changes):
            existed = path in originals
            if existed:
                write_private(backup / f"{index}.toml", originals[path])
            manifest.append({"path": str(path.resolve()), "backup": f"{index}.toml" if existed else None})
        write_private(backup / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        commit_config_files(host, changes)
