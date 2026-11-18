"""旧保留策略的内存转换；已有公共策略优先，不独立落盘。"""
from pathlib import Path
from typing import Dict

import copy

from src.config.shared_settings import RETENTION_DEFAULTS, RETENTION_PATHS, strip_retention, validate_retention


def plan_retention(doc: dict, documents: Dict[Path, dict]) -> None:
    policy = copy.deepcopy(RETENTION_DEFAULTS)
    sources = []
    for local in documents.values():
        before = local.unwrap()
        candidate = copy.deepcopy(RETENTION_DEFAULTS)
        for section, (parent, key) in RETENTION_PATHS.items():
            candidate[section].update(before.get(parent, {}).get(key, {}))
        candidate["logs"].update({key: value for key, value in before.get("log", {}).items() if key in candidate["logs"]})
        validate_retention(candidate)
        sources.append(before)
        strip_retention(local)
    if "retention" not in doc:
        for section, defaults in RETENTION_DEFAULTS.items():
            for key in defaults:
                values = []
                for source in sources:
                    data = source.get("log", {}) if section == "logs" else source.get(RETENTION_PATHS[section][0], {}).get(RETENTION_PATHS[section][1], {})
                    if key in data:
                        values.append(data[key])
                if values:
                    # 关闭清理优先；保留时长和容量取较大值，不因合并而提前删数据。
                    policy[section][key] = all(values) if key == "enabled" else max(values)
        doc["retention"] = validate_retention(policy)
    else:
        validate_retention(doc["retention"])
