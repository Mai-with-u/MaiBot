from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .official_configs import ChatConfig


ConfigUpgradeHookCallable = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class ConfigUpgradeHook:
    """配置升级钩子，在跨过指定版本时执行一次。"""

    target_version: str
    config_names: tuple[str, ...]
    migrate: ConfigUpgradeHookCallable


@dataclass
class ConfigUpgradeHookResult:
    data: dict[str, Any]
    migrated: bool
    reason: str = ""


def _parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _version_in_upgrade_range(old_ver: str, target_ver: str, new_ver: str) -> bool:
    old_parts = _parse_version(old_ver)
    target_parts = _parse_version(target_ver)
    new_parts = _parse_version(new_ver)
    return old_parts < target_parts <= new_parts


def set_nested_config_value(data: dict[str, Any], path: tuple[str, ...], value: Any, force: bool = True) -> bool:
    """设置嵌套配置值，返回是否实际发生变化。"""

    if not path:
        return False

    current: dict[str, Any] = data
    for key in path[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value

    leaf_key = path[-1]
    if not force and leaf_key in current:
        return False
    if current.get(leaf_key) == value:
        return False

    current[leaf_key] = value
    return True


def _reset_group_chat_prompt_to_default(data: dict[str, Any]) -> list[str]:
    default_group_chat_prompt = ChatConfig().reply_style.group_chat_prompt
    changed = set_nested_config_value(data, ("chat", "reply_style", "group_chat_prompt"), default_group_chat_prompt)
    return ["chat.reply_style.group_chat_prompt"] if changed else []


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def _parse_triplet_target(value: str) -> dict[str, str] | None:
    parts = value.split(":", 2)
    if len(parts) != 3:
        return None

    platform, item_id, rule_type = parts
    if rule_type not in ("group", "private"):
        return None
    return {"platform": platform, "item_id": item_id, "rule_type": rule_type}


def _parse_jargon_group_target(value: str) -> dict[str, str] | None:
    normalized_value = value.strip()
    if normalized_value == "*":
        return {"platform": "*", "item_id": "*", "rule_type": "group"}
    return _parse_triplet_target(normalized_value)


def _migrate_jargon_groups(jargon: dict[str, Any]) -> bool:
    raw_jargon_groups = jargon.get("jargon_groups")
    if isinstance(raw_jargon_groups, str):
        normalized_value = raw_jargon_groups.strip()
        if not normalized_value:
            jargon["jargon_groups"] = []
            return True

        parsed = _parse_jargon_group_target(normalized_value)
        if parsed is None:
            return False

        jargon["jargon_groups"] = [{"jargon_groups": [parsed]}]
        return True

    jargon_groups = _as_list(raw_jargon_groups)
    if jargon_groups is None:
        return False
    if jargon_groups and all(isinstance(item, dict) for item in jargon_groups):
        return False

    migrated_groups: list[dict[str, Any]] = []
    for group in jargon_groups:
        group_items = _as_list(group)
        if group_items is None:
            return False

        targets: list[dict[str, str]] = []
        for item in group_items:
            parsed = _parse_jargon_group_target(str(item))
            if parsed is None:
                return False
            targets.append(parsed)

        migrated_groups.append({"jargon_groups": targets})

    jargon["jargon_groups"] = migrated_groups
    return True


def _split_jargon_config_from_expression(data: dict[str, Any]) -> list[str]:
    """
    8.10.18: 将黑话学习配置从 expression 拆到独立的 jargon 配置段。
    """
    expr = _as_dict(data.get("expression"))
    if expr is None:
        return []

    jargon = _as_dict(data.get("jargon"))
    if jargon is None:
        jargon = {}
        data["jargon"] = jargon

    reasons: list[str] = []
    if "learning_list" not in jargon:
        learning_list = _as_list(expr.get("learning_list")) or []
        jargon_learning_list: list[dict[str, Any]] = []
        for item in learning_list:
            item_dict = _as_dict(item)
            if item_dict is None or "enable_jargon_learning" not in item_dict:
                continue
            jargon_learning_list.append(
                {
                    "platform": item_dict.get("platform", ""),
                    "item_id": item_dict.get("item_id", ""),
                    "rule_type": item_dict.get("type", item_dict.get("rule_type", "group")),
                    "enable_learning": bool(item_dict.get("enable_jargon_learning")),
                }
            )

        if jargon_learning_list:
            jargon["learning_list"] = jargon_learning_list
            reasons.append("jargon.learning_list")

    learning_list = _as_list(expr.get("learning_list")) or []
    removed_jargon_flag = False
    for item in learning_list:
        item_dict = _as_dict(item)
        if item_dict is not None and "enable_jargon_learning" in item_dict:
            item_dict.pop("enable_jargon_learning", None)
            removed_jargon_flag = True
    if removed_jargon_flag:
        reasons.append("expression.learning_list.enable_jargon_learning")

    if expr.get("all_global_jargon") is True and "jargon_groups" not in jargon:
        jargon["jargon_groups"] = [{"jargon_groups": [{"platform": "*", "item_id": "*", "rule_type": "group"}]}]
        reasons.append("jargon.jargon_groups")

    if "all_global_jargon" in expr:
        expr.pop("all_global_jargon", None)
        reasons.append("expression.all_global_jargon")

    if _migrate_jargon_groups(jargon):
        reasons.append("jargon.jargon_groups")

    return reasons


def _normalize_learning_item_fields(data: dict[str, Any]) -> list[str]:
    """
    8.10.19: 统一 expression/jargon learning_list 字段为 platform/item_id/type/use/learn。
    """
    reasons: list[str] = []

    expression = _as_dict(data.get("expression"))
    if expression is not None and _normalize_learning_list_fields(
        expression,
        use_source_keys=("use_expression", "use"),
        learn_source_keys=("enable_learning", "learn"),
        default_use=True,
        default_learn=True,
    ):
        reasons.append("expression.learning_list")

    jargon = _as_dict(data.get("jargon"))
    if jargon is not None and _normalize_learning_list_fields(
        jargon,
        use_source_keys=("use",),
        learn_source_keys=("enable_learning", "learn"),
        default_use=True,
        default_learn=True,
    ):
        reasons.append("jargon.learning_list")

    return reasons


def _normalize_group_item_fields(data: dict[str, Any]) -> list[str]:
    """
    8.10.20: 统一 expression/jargon 共享组组内字段为 targets。
    """
    reasons: list[str] = []

    expression = _as_dict(data.get("expression"))
    if expression is not None and _normalize_group_list_fields(expression, "expression_groups", "expression_groups"):
        reasons.append("expression.expression_groups")

    jargon = _as_dict(data.get("jargon"))
    if jargon is not None and _normalize_group_list_fields(jargon, "jargon_groups", "jargon_groups"):
        reasons.append("jargon.jargon_groups")

    return reasons


def _upgrade_expression_learning_defaults(data: dict[str, Any]) -> list[str]:
    """
    8.12.1: 表达学习并发默认值调整为 3，并补齐默认开启的学习优化开关。
    """
    expression = _as_dict(data.get("expression"))
    if expression is None:
        expression = {}
        data["expression"] = expression

    reasons: list[str] = []
    if expression.get("max_expression_learner") in (None, 2):
        expression["max_expression_learner"] = 3
        reasons.append("expression.max_expression_learner")
    if "expression_self_reflect" not in expression:
        expression["expression_self_reflect"] = True
        reasons.append("expression.expression_self_reflect")

    return reasons


def _normalize_webui_host_to_list(data: dict[str, Any]) -> list[str]:
    """
    8.14.13: 将 WebUI host 从 str 转换为 list[str]。
    旧配置中 host 为单地址字符串（如 "127.0.0.1"），新配置为列表。
    """
    webui = _as_dict(data.get("webui"))
    if webui is None:
        return []

    host = webui.get("host")
    if isinstance(host, list):
        return []
    if isinstance(host, str):
        webui["host"] = [host]
        return ["webui.host"]

    return []


def _move_chat_config_key(chat: dict[str, Any], section_name: str, key: str) -> bool:
    if key not in chat:
        return False

    section = _as_dict(chat.get(section_name))
    if section is None:
        section = {}
        chat[section_name] = section

    if key not in section:
        section[key] = chat[key]
    chat.pop(key, None)
    return True


def _split_chat_config_sections(data: dict[str, Any]) -> list[str]:
    """
    8.14.19: 将 chat 中的回复时机/频率与回复方式拆到独立子配置段。
    """

    chat = _as_dict(data.get("chat"))
    if chat is None:
        return []

    moved_paths: list[str] = []
    timing_keys = (
        "talk_value",
        "private_talk_value",
        "mentioned_bot_reply",
        "inevitable_at_reply",
        "reply_trigger_mode",
        "planner_interrupt_max_consecutive_count",
        "max_consecutive_wait_count",
        "no_action_backoff_base_seconds",
        "no_action_backoff_cap_seconds",
        "no_action_backoff_start_count",
        "no_action_backoff_bypass_pending_count",
        "enable_talk_value_rules",
        "talk_value_rules",
    )
    style_keys = (
        "enable_reply_quote",
        "group_chat_prompt",
        "private_chat_prompts",
        "chat_prompts",
    )

    for key in timing_keys:
        if _move_chat_config_key(chat, "reply_timing", key):
            moved_paths.append(f"chat.reply_timing.{key}")
    for key in style_keys:
        if _move_chat_config_key(chat, "reply_style", key):
            moved_paths.append(f"chat.reply_style.{key}")

    return moved_paths


def _normalize_group_list_fields(section: dict[str, Any], list_key: str, old_inner_key: str) -> bool:
    group_list = _as_list(section.get(list_key))
    if group_list is None:
        return False

    migrated = False
    for group in group_list:
        group_dict = _as_dict(group)
        if group_dict is None:
            continue

        if "targets" not in group_dict and old_inner_key in group_dict:
            group_dict["targets"] = group_dict.get(old_inner_key)
            migrated = True

        if old_inner_key in group_dict:
            group_dict.pop(old_inner_key, None)
            migrated = True

    return migrated


def _normalize_learning_list_fields(
    section: dict[str, Any],
    *,
    use_source_keys: tuple[str, ...],
    learn_source_keys: tuple[str, ...],
    default_use: bool,
    default_learn: bool,
) -> bool:
    learning_list = _as_list(section.get("learning_list"))
    if learning_list is None:
        return False

    migrated = False
    for item in learning_list:
        item_dict = _as_dict(item)
        if item_dict is None:
            continue

        normalized_type = item_dict.get("type", item_dict.get("rule_type", "group"))
        normalized_use = _first_existing_bool(item_dict, use_source_keys, default_use)
        normalized_learn = _first_existing_bool(item_dict, learn_source_keys, default_learn)

        for key in ("rule_type", "use_expression", "enable_learning"):
            if key in item_dict:
                item_dict.pop(key, None)
                migrated = True

        if item_dict.get("type") != normalized_type:
            item_dict["type"] = normalized_type
            migrated = True
        if item_dict.get("use") != normalized_use:
            item_dict["use"] = normalized_use
            migrated = True
        if item_dict.get("learn") != normalized_learn:
            item_dict["learn"] = normalized_learn
            migrated = True

    return migrated


def _first_existing_bool(item: dict[str, Any], keys: tuple[str, ...], default: bool) -> bool:
    for key in keys:
        if key in item:
            return bool(item.get(key))
    return default


def _copy_personality_to_behavior_style(data: dict[str, Any]) -> list[str]:
    """8.14.29: 首次拆分 Planner 行为风格时，沿用用户原有的人格配置。"""

    personality = _as_dict(data.get("personality"))
    if personality is None or "behavior_style" in personality:
        return []

    original_personality = personality.get("personality")
    if not isinstance(original_personality, str):
        return []

    personality["behavior_style"] = original_personality
    return ["personality.behavior_style"]


BOT_CONFIG_UPGRADE_HOOKS: tuple[ConfigUpgradeHook, ...] = (
    ConfigUpgradeHook(
        target_version="8.10.11",
        config_names=("bot_config.toml",),
        migrate=_reset_group_chat_prompt_to_default,
    ),
    ConfigUpgradeHook(
        target_version="8.10.18",
        config_names=("bot_config.toml",),
        migrate=_split_jargon_config_from_expression,
    ),
    ConfigUpgradeHook(
        target_version="8.10.19",
        config_names=("bot_config.toml",),
        migrate=_normalize_learning_item_fields,
    ),
    ConfigUpgradeHook(
        target_version="8.10.20",
        config_names=("bot_config.toml",),
        migrate=_normalize_group_item_fields,
    ),
    ConfigUpgradeHook(
        target_version="8.12.1",
        config_names=("bot_config.toml",),
        migrate=_upgrade_expression_learning_defaults,
    ),
    ConfigUpgradeHook(
        target_version="8.14.13",
        config_names=("bot_config.toml",),
        migrate=_normalize_webui_host_to_list,
    ),
    ConfigUpgradeHook(
        target_version="8.14.19",
        config_names=("bot_config.toml",),
        migrate=_split_chat_config_sections,
    ),
    ConfigUpgradeHook(
        target_version="8.14.29",
        config_names=("bot_config.toml",),
        migrate=_copy_personality_to_behavior_style,
    ),
)
MODEL_CONFIG_UPGRADE_HOOKS: tuple[ConfigUpgradeHook, ...] = ()


def apply_config_upgrade_hooks(
    data: dict[str, Any],
    config_name: str,
    old_ver: str,
    new_ver: str,
) -> ConfigUpgradeHookResult:
    migrated_reasons: list[str] = []
    hooks = BOT_CONFIG_UPGRADE_HOOKS + MODEL_CONFIG_UPGRADE_HOOKS

    for hook in hooks:
        if config_name not in hook.config_names:
            continue
        if not _version_in_upgrade_range(old_ver, hook.target_version, new_ver):
            continue

        hook_reasons = hook.migrate(data)
        for reason in hook_reasons:
            migrated_reasons.append(f"{hook.target_version}:{reason}")

    reason = ",".join(migrated_reasons)
    return ConfigUpgradeHookResult(data=data, migrated=bool(migrated_reasons), reason=reason)
