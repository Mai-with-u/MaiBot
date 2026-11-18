"""复制聊天流的精确配置规则，保留 TOML 注释与其他聊天流配置。"""

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import os

import tomlkit

from .chat_migration_store import digest


def identity(session: Dict[str, Any]) -> Dict[str, str]:
    return {
        "platform": session["platform"],
        "item_id": session["group_id"] or session["user_id"],
        "type": "group" if session["group_id"] else "private",
    }


def matches(rule: Any, target: Dict[str, str]) -> bool:
    return (
        isinstance(rule, dict)
        and rule.get("platform") == target["platform"]
        and rule.get("item_id") == target["item_id"]
        and rule.get("type", rule.get("rule_type")) == target["type"]
    )


def rule_schema(parts: List[Any]) -> Dict[str, Any]:
    from src.config.config import Config

    schema = Config.model_json_schema()
    node = schema
    for part in [*parts, 0]:
        if "$ref" in node:
            node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        node = node.get("items", {}) if isinstance(part, int) else node.get("properties", {}).get(part, {})
    if "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def uses_defaults(
    rules: List[Dict[str, Any]],
    all_rules: List[Any],
    target: Dict[str, str],
    schema: Dict[str, Any],
    incoming: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    if not rules:
        return True
    identity_keys = {"platform", "item_id", "type", "rule_type"}
    defaults = {
        key: field["default"]
        for key, field in schema.get("properties", {}).items()
        if key not in identity_keys and "default" in field
    }
    # 精确规则继承当前全局/平台默认，不能把用户调整过的默认误当成冲突。
    inherited = [
        rule
        for rule in all_rules
        if isinstance(rule, dict)
        and rule.get("item_id", "") in ("", "*")
        and rule.get("platform", "") in ("", "*", target["platform"])
        and rule.get("type", rule.get("rule_type")) == target["type"]
    ]
    for rule in sorted(inherited, key=lambda item: item.get("platform") == target["platform"]):
        defaults.update({key: value for key, value in rule.items() if key not in identity_keys})
    for index, rule in enumerate(rules):
        replacement = incoming[index] if incoming is not None and index < len(incoming) else {}
        for key, value in rule.items():
            if key in identity_keys or (key in replacement and replacement[key] == value):
                continue
            if key not in defaults or value != defaults[key]:
                return False
    return bool(defaults)


def snapshot(path: Path, source: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    document = tomlkit.parse(raw).unwrap()
    source_id, target_id = identity(source), identity(target)
    sections: List[Dict[str, Any]] = []

    def walk(value: Any, parts: List[Any]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, [*parts, key])
        elif isinstance(value, list):
            incoming = []
            existing = [item for item in value if matches(item, target_id)]
            for item in value:
                if matches(item, source_id):
                    rule = deepcopy(item)
                    rule.update(platform=target_id["platform"], item_id=target_id["item_id"])
                    rule["rule_type" if "rule_type" in rule else "type"] = target_id["type"]
                    incoming.append(rule)
            if incoming and incoming != existing and source_id != target_id:
                schema = rule_schema(parts)
                sections.append(
                    {
                        "id": "config:" + ".".join(map(str, parts)),
                        "kind": "config",
                        "label": ".".join(map(str, parts)),
                        "path": parts,
                        "automatic": uses_defaults(existing, value, target_id, schema, incoming),
                        "field_labels": {
                            key: field.get("label", {}).get("zh_CN", key)
                            if isinstance(field.get("label"), dict)
                            else field.get("label", key)
                            for key, field in schema.get("properties", {}).items()
                        },
                        "source": incoming,
                        "target": existing,
                        "choices": ["source", "target"],
                    }
                )
            for index, child in enumerate(value):
                if not matches(child, source_id) and not matches(child, target_id):
                    walk(child, [*parts, index])

    walk(document, [])
    return {"fingerprint": digest(raw), "raw": raw, "sections": sections, "target_identity": target_id}


def render(state: Dict[str, Any], decisions: Dict[str, str]) -> str:
    document = tomlkit.parse(state["raw"])
    for section in state["sections"]:
        choice = "source" if section.get("automatic", not section["target"]) else decisions.get(section["id"], "")
        if choice not in section["choices"]:
            raise ValueError("存在未处理的配置冲突")
        if choice == "target":
            continue
        parent = document
        for key in section["path"][:-1]:
            parent = parent[key]
        field = section["path"][-1]
        parent[field] = [item for item in parent[field] if not matches(item, state["target_identity"])] + section[
            "source"
        ]
    return tomlkit.dumps(document)


def replace(path: Path, expected: str, content: str) -> None:
    if digest(path.read_text(encoding="utf-8")) != expected:
        raise ValueError("配置在预览后发生变化，请重新预览")
    temporary = path.with_name(path.name + ".chat-migration.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
