"""本地聊天室路由 - WebUI 与麦麦直接对话。"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, delete, func
from sqlmodel import col, select
import json
import tomlkit

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.message_receive.chat_manager import chat_manager as core_chat_manager
from src.common.database.database import get_db_session
from src.common.database.database_model import (
    BehaviorAction,
    BehaviorExperiencePath,
    BehaviorOutcome,
    BehaviorSceneCluster,
    ChatSession,
    Expression,
    HighFrequencyTerm,
    Jargon,
    Messages,
    PersonInfo,
    StatisticsMessageHourly,
    ToolRecord,
)
from src.common.logger import get_logger
from src.common.utils.utils_config import (
    BehaviorConfigUtils,
    ChatConfigUtils,
    ExpressionConfigUtils,
    JargonConfigUtils,
)
from src.config.config import BOT_CONFIG_PATH, config_manager, global_config
from src.platform_io import AdapterIdentity, DriverKind, RouteKey, get_adapter_policy_manager, get_platform_io_manager
from src.webui.dependencies import require_auth
from src.webui.utils.toml_utils import save_toml_with_format

from .service import (
    WEBUI_CHAT_PLATFORM,
    chat_history,
    chat_manager,
    normalize_webui_user_id,
)

logger = get_logger("webui.chat")

router = APIRouter(prefix="/api/chat", tags=["LocalChat"], dependencies=[Depends(require_auth)])


class TalkFrequencyUpdateRequest(BaseModel):
    """聊天流发言频率编辑请求。"""

    previous_time: Optional[str] = None
    time: str = Field(default="*")
    value: float = Field(ge=0, le=1)


class LearningUpdateRequest(BaseModel):
    """聊天流学习配置编辑请求。"""

    use: bool = True
    learn: bool = True


class ChatPromptUpdateRequest(BaseModel):
    """聊天流专属 Prompt 编辑请求。"""

    prompt: str = Field(default="")


class AdapterPolicyUpdateRequest(BaseModel):
    """聊天流适配器放行策略编辑请求。"""

    adapter_id: str = Field(default="")
    action: str = Field(default="")


class ChatTargetResolveItem(BaseModel):
    """配置目标解析请求项。"""

    platform: str = Field(default="")
    item_id: str = Field(default="")
    rule_type: str = Field(default="group")


class ChatTargetResolveBatchRequest(BaseModel):
    """批量解析配置目标请求。"""

    targets: List[ChatTargetResolveItem] = Field(default_factory=list)


def _datetime_to_timestamp(value: Optional[datetime]) -> Optional[float]:
    """将数据库时间转换为前端更易处理的秒级时间戳。"""

    return value.timestamp() if value else None


def _get_chat_type(chat_session: ChatSession) -> str:
    """根据会话记录判断聊天流类型。"""

    return "group" if chat_session.group_id else "private"


def _get_chat_target_id(chat_session: ChatSession) -> str:
    """获取配置规则实际匹配的群号或用户 ID。"""

    return chat_session.group_id or chat_session.user_id or ""


def _get_chat_display_name(chat_session: ChatSession, latest_message: Optional[Any]) -> str:
    """优先展示聊天流实际名称，缺失时再退回到可读的 ID 名称。"""

    if latest_message:
        group_name = str(latest_message.group_name or "").strip()
        if latest_message.group_id and group_name:
            return group_name
        if latest_message.group_id:
            return f"群聊{latest_message.group_id}"

        private_name = str(
            latest_message.user_cardname
            or latest_message.user_nickname
            or (f"用户{latest_message.user_id}" if latest_message.user_id else "")
        ).strip()
        if private_name:
            return f"{private_name}的私聊"

    if chat_session.group_name:
        return chat_session.group_name
    if chat_session.group_id:
        return f"群聊{chat_session.group_id}"

    private_name = chat_session.user_cardname or chat_session.user_nickname or (
        f"用户{chat_session.user_id}" if chat_session.user_id else ""
    )
    return f"{private_name}的私聊" if private_name else chat_session.session_id


def _needs_latest_message_for_display_name(chat_session: ChatSession) -> bool:
    """判断是否需要读取最新消息来补齐聊天流名称。"""

    if chat_session.group_id:
        return not str(chat_session.group_name or "").strip()
    return not str(
        chat_session.user_cardname
        or chat_session.user_nickname
        or (f"用户{chat_session.user_id}" if chat_session.user_id else "")
    ).strip()


def _get_latest_messages_by_session(session_ids: List[str]) -> Dict[str, Any]:
    """批量获取每个聊天流的最新消息。"""

    if not session_ids:
        return {}

    with get_db_session() as session:
        latest_timestamp_subquery = (
            select(
                Messages.session_id,
                func.max(Messages.timestamp).label("latest_timestamp"),
            )
            .where(col(Messages.session_id).in_(session_ids))
            .group_by(Messages.session_id)
            .subquery()
        )
        statement = (
            select(
                Messages.session_id,
                Messages.group_id,
                Messages.group_name,
                Messages.user_id,
                Messages.user_nickname,
                Messages.user_cardname,
                Messages.timestamp,
            )
            .join(
                latest_timestamp_subquery,
                and_(
                    Messages.session_id == latest_timestamp_subquery.c.session_id,
                    Messages.timestamp == latest_timestamp_subquery.c.latest_timestamp,
                ),
            )
            .order_by(col(Messages.session_id).asc(), col(Messages.id).desc())
        )
        latest_messages: Dict[str, Any] = {}
        for row in session.exec(statement).all():
            session_id = str(row.session_id or "").strip()
            if session_id and session_id not in latest_messages:
                latest_messages[session_id] = SimpleNamespace(
                    session_id=row.session_id,
                    group_id=row.group_id,
                    group_name=row.group_name,
                    user_id=row.user_id,
                    user_nickname=row.user_nickname,
                    user_cardname=row.user_cardname,
                    timestamp=row.timestamp,
                )
        return latest_messages


def _get_message_counts_by_session(session_ids: List[str]) -> Dict[str, int]:
    """批量统计每个聊天流的消息数量。"""

    if not session_ids:
        return {}

    with get_db_session() as session:
        statement = (
            select(Messages.session_id, func.count(Messages.id))
            .where(col(Messages.session_id).in_(session_ids))
            .group_by(Messages.session_id)
        )
        return {
            str(session_id): int(count)
            for session_id, count in session.exec(statement).all()
            if session_id
        }


def _get_expression_counts_by_session(session_ids: List[str]) -> Dict[str, int]:
    """批量统计每个聊天流的表达数量。"""

    if not session_ids:
        return {}

    with get_db_session() as session:
        statement = (
            select(Expression.session_id, func.count(Expression.id))
            .where(col(Expression.session_id).in_(session_ids))
            .group_by(Expression.session_id)
        )
        return {
            str(session_id): int(count)
            for session_id, count in session.exec(statement).all()
            if session_id
        }


def _get_jargon_counts_by_session(session_ids: List[str]) -> Dict[str, int]:
    """批量统计每个聊天流关联的黑话数量。"""

    if not session_ids:
        return {}

    session_id_set = set(session_ids)
    counts = {session_id: 0 for session_id in session_ids}
    with get_db_session() as session:
        statement = select(Jargon.session_id_dict).where(col(Jargon.session_id_dict).is_not(None))
        for raw_session_id_dict in session.exec(statement).all():
            try:
                session_counts = json.loads(raw_session_id_dict or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(session_counts, dict):
                continue
            for session_id in session_counts:
                if session_id in session_id_set:
                    counts[session_id] += 1
    return counts


def _chat_session_to_response(
    chat_session: ChatSession,
    latest_message: Optional[Any],
    message_count: int,
    expression_count: int,
    jargon_count: int,
) -> Dict[str, Any]:
    """将 ChatSession 转换为 WebUI 列表项。"""

    chat_type = _get_chat_type(chat_session)
    return {
        "id": chat_session.id,
        "session_id": chat_session.session_id,
        "display_name": _get_chat_display_name(chat_session, latest_message),
        "chat_type": chat_type,
        "target_id": _get_chat_target_id(chat_session),
        "platform": chat_session.platform,
        "account_id": chat_session.account_id,
        "scope": chat_session.scope,
        "user_id": chat_session.user_id,
        "user_nickname": chat_session.user_nickname,
        "user_cardname": chat_session.user_cardname,
        "group_id": chat_session.group_id,
        "group_name": chat_session.group_name,
        "message_count": message_count,
        "expression_count": expression_count,
        "jargon_count": jargon_count,
        "created_at": _datetime_to_timestamp(chat_session.created_timestamp),
        "last_active_at": _datetime_to_timestamp(chat_session.last_active_timestamp),
        "latest_message": "",
        "latest_message_at": _datetime_to_timestamp(latest_message.timestamp) if latest_message else None,
    }


def _resolve_chat_targets(targets: List[ChatTargetResolveItem]) -> List[Dict[str, Any]]:
    """批量解析配置目标到真实聊天流。"""

    normalized_targets: List[tuple[str, str, str]] = []
    for target in targets:
        platform = str(target.platform or "").strip()
        item_id = str(target.item_id or "").strip()
        rule_type = "private" if str(target.rule_type or "").strip() == "private" else "group"
        normalized_targets.append((platform, item_id, rule_type))

    resolved_by_key: Dict[tuple[str, str, str], ChatSession] = {}
    query_keys = {
        (platform, item_id, rule_type)
        for platform, item_id, rule_type in normalized_targets
        if platform and item_id
    }

    with get_db_session() as session:
        for rule_type, target_attr in (("group", "group_id"), ("private", "user_id")):
            scoped_keys = [(platform, item_id) for platform, item_id, key_rule_type in query_keys if key_rule_type == rule_type]
            if not scoped_keys:
                continue

            platform_values = {platform for platform, _ in scoped_keys}
            item_values = {item_id for _, item_id in scoped_keys}
            statement = (
                select(ChatSession)
                .where(
                    col(ChatSession.platform).in_(platform_values),
                    col(getattr(ChatSession, target_attr)).in_(item_values),
                )
                .order_by(
                    case((col(ChatSession.last_active_timestamp).is_(None), 1), else_=0),
                    col(ChatSession.last_active_timestamp).desc(),
                    col(ChatSession.created_timestamp).desc(),
                )
            )
            for chat_session in session.exec(statement).all():
                platform = str(chat_session.platform or "").strip()
                item_id = str(getattr(chat_session, target_attr) or "").strip()
                key = (platform, item_id, rule_type)
                if key in query_keys and key not in resolved_by_key:
                    resolved_by_key[key] = chat_session

    fallback_session_ids = [
        chat_session.session_id
        for chat_session in resolved_by_key.values()
        if chat_session.session_id and _needs_latest_message_for_display_name(chat_session)
    ]
    latest_messages = _get_latest_messages_by_session(fallback_session_ids)

    results: List[Dict[str, Any]] = []
    for key in normalized_targets:
        chat_session = resolved_by_key.get(key)
        if chat_session is None:
            results.append({"found": False, "session": None})
            continue

        results.append(
            {
                "found": True,
                "session": _chat_session_to_response(
                    chat_session=chat_session,
                    latest_message=latest_messages.get(chat_session.session_id),
                    message_count=0,
                    expression_count=0,
                    jargon_count=0,
                ),
            }
        )

    return results


def _target_config_to_dict(config_item: Any) -> Optional[Dict[str, Any]]:
    """序列化学习配置项，方便前端展示命中的规则来源。"""

    if config_item is None:
        return None

    platform, item_id, rule_type = ChatConfigUtils._target_values(config_item)
    return {
        "platform": platform,
        "item_id": item_id,
        "type": rule_type,
        "use": bool(getattr(config_item, "use", True)),
        "learn": bool(getattr(config_item, "learn", True)),
        "is_default": ChatConfigUtils.is_default_target(config_item),
        "is_platform_default": ChatConfigUtils.is_platform_default_target(config_item),
        "is_wildcard": ChatConfigUtils.is_wildcard_target(config_item),
    }


def _learning_item_to_config_dict(config_item: Any) -> Dict[str, Any]:
    """把学习配置项转换为可保存的普通字典。"""

    platform, item_id, rule_type = ChatConfigUtils._target_values(config_item)
    return {
        "platform": platform,
        "item_id": item_id,
        "type": rule_type or "group",
        "use": bool(getattr(config_item, "use", True) if not isinstance(config_item, dict) else config_item.get("use", True)),
        "learn": bool(
            getattr(config_item, "learn", True) if not isinstance(config_item, dict) else config_item.get("learn", True)
        ),
    }


def _is_same_learning_target(rule: Dict[str, Any], chat_session: ChatSession) -> bool:
    """判断学习规则是否精确作用于当前聊天流。"""

    return (
        str(rule.get("platform") or "").strip() == str(chat_session.platform or "").strip()
        and str(rule.get("item_id") or "").strip() == _get_chat_target_id(chat_session)
        and str(rule.get("type") or rule.get("rule_type") or "").strip() == _get_chat_type(chat_session)
    )


def _format_frequency(value: float) -> str:
    normalized_value = max(0.0, float(value))
    return f"{normalized_value:.3f}（{normalized_value * 100:.1f}%）"


def _talk_rule_to_dict(rule: Any, session_id: str, is_group_chat: bool, now_min: int) -> Optional[Dict[str, Any]]:
    """序列化一条匹配当前聊天流的发言频率规则。"""

    target_priority = ChatConfigUtils._talk_rule_target_priority(rule, session_id, is_group_chat)
    if target_priority is None:
        return None

    platform, item_id, rule_type = ChatConfigUtils._target_values(rule)
    rule_time = ChatConfigUtils._get_rule_time(rule)
    time_priority = ChatConfigUtils._talk_rule_time_priority(rule_time, now_min)
    value = ChatConfigUtils._get_rule_value(rule)
    return {
        "platform": platform,
        "item_id": item_id,
        "type": rule_type,
        "time": rule_time,
        "value": value,
        "value_label": _format_frequency(value),
        "target_priority": target_priority,
        "time_priority": time_priority,
        "time_active": time_priority is not None,
        "is_effective": False,
        "is_default_target": not platform and not item_id,
    }


def _get_talk_rule_details(chat_session: ChatSession) -> Dict[str, Any]:
    """获取聊天流发言频率默认值、生效值与匹配规则。"""

    session_id = chat_session.session_id
    is_group_chat = _get_chat_type(chat_session) == "group"
    reply_timing_config = global_config.chat.reply_timing
    base_value = float(reply_timing_config.talk_value if is_group_chat else reply_timing_config.private_talk_value)
    effective_value = float(ChatConfigUtils.get_talk_value(session_id, is_group_chat=is_group_chat))
    local_time = datetime.now().strftime("%H:%M")
    current_time = datetime.now().time()
    now_min = current_time.hour * 60 + current_time.minute
    rules = [
        rule_detail
        for rule in reply_timing_config.talk_value_rules
        if (rule_detail := _talk_rule_to_dict(rule, session_id, is_group_chat, now_min)) is not None
    ]
    selected_index: Optional[int] = None
    selected_priority = (0, 0)
    for index, rule in enumerate(rules):
        time_priority = rule["time_priority"]
        if time_priority is None:
            continue
        priority = (rule["target_priority"], time_priority)
        if priority <= selected_priority:
            continue
        selected_priority = priority
        selected_index = index

    if selected_index is not None:
        rules[selected_index]["is_effective"] = True

    sorted_rules = sorted(
        rules,
        key=lambda rule: (
            1 if rule["is_effective"] else 0,
            rule["target_priority"],
            rule["time_priority"] or 0,
        ),
        reverse=True,
    )

    return {
        "enabled": bool(reply_timing_config.enable_talk_value_rules),
        "base_value": base_value,
        "base_value_label": _format_frequency(base_value),
        "effective_value": effective_value,
        "effective_value_label": _format_frequency(effective_value),
        "current_time": local_time,
        "matched_rules": sorted_rules,
    }


def _chat_prompt_item_values(chat_prompt_item: Any) -> Optional[Dict[str, Any]]:
    """解析一条聊天 Prompt 配置，兼容旧字符串格式。"""

    if hasattr(chat_prompt_item, "platform"):
        platform = str(chat_prompt_item.platform or "").strip()
        item_id = str(chat_prompt_item.item_id or "").strip()
        rule_type = str(chat_prompt_item.rule_type or "").strip()
        prompt = str(chat_prompt_item.prompt or "").strip()
    elif isinstance(chat_prompt_item, dict):
        platform = str(chat_prompt_item.get("platform") or "").strip()
        item_id = str(chat_prompt_item.get("item_id") or "").strip()
        rule_type = str(chat_prompt_item.get("rule_type") or chat_prompt_item.get("type") or "").strip()
        prompt = str(chat_prompt_item.get("prompt") or "").strip()
    elif isinstance(chat_prompt_item, str):
        parts = chat_prompt_item.split(":", 3)
        if len(parts) != 4:
            return None
        platform, item_id, rule_type, prompt = [part.strip() for part in parts]
    else:
        return None

    if not platform or not item_id or not prompt or rule_type not in {"group", "private"}:
        return None
    return {
        "platform": platform,
        "item_id": item_id,
        "rule_type": rule_type,
        "prompt": prompt,
    }


def _chat_prompt_to_config_dict(chat_prompt_item: Any) -> Optional[Dict[str, Any]]:
    """把聊天 Prompt 配置转换为可保存的普通字典。"""

    values = _chat_prompt_item_values(chat_prompt_item)
    if values is None:
        return None
    return {
        "platform": values["platform"],
        "item_id": values["item_id"],
        "rule_type": values["rule_type"],
        "prompt": values["prompt"],
    }


def _is_same_prompt_target(prompt_item: Dict[str, Any], chat_session: ChatSession) -> bool:
    """判断 Prompt 配置是否精确作用于当前聊天流。"""

    return (
        str(prompt_item.get("platform") or "").strip() == str(chat_session.platform or "").strip()
        and str(prompt_item.get("item_id") or "").strip() == _get_chat_target_id(chat_session)
        and str(prompt_item.get("rule_type") or "").strip() == _get_chat_type(chat_session)
    )


def _get_chat_prompt_details(chat_session: ChatSession) -> Dict[str, Any]:
    """获取当前聊天流实际使用的基础 Prompt 与额外聊天流 Prompt。"""

    is_group_chat = _get_chat_type(chat_session) == "group"
    reply_style_config = global_config.chat.reply_style
    base_prompt_type = "group" if is_group_chat else "private"
    base_prompt_title = "群聊提示词" if is_group_chat else "私聊提示词"
    base_prompt = reply_style_config.group_chat_prompt if is_group_chat else reply_style_config.private_chat_prompts
    chat_prompts = []
    for index, chat_prompt_item in enumerate(reply_style_config.chat_prompts):
        prompt_config = _chat_prompt_item_values(chat_prompt_item)
        if prompt_config is None:
            continue
        if not _is_same_prompt_target(prompt_config, chat_session):
            continue
        chat_prompts.append(
            {
                "index": index,
                "platform": prompt_config["platform"],
                "item_id": prompt_config["item_id"],
                "rule_type": prompt_config["rule_type"],
                "prompt": prompt_config["prompt"],
            }
        )
    return {
        "base_prompt_type": base_prompt_type,
        "base_prompt_title": base_prompt_title,
        "base_prompt": base_prompt,
        "chat_prompts": chat_prompts,
    }


def _normalize_talk_rule_time(rule_time: Optional[str]) -> str:
    """校验并规范化发言频率规则时间。"""

    normalized_time = str(rule_time or "").strip()
    if normalized_time in {"", "*"}:
        return normalized_time
    if ChatConfigUtils.parse_range(normalized_time) is None:
        raise HTTPException(status_code=400, detail="时间段格式应为 HH:MM-HH:MM、* 或留空")
    return normalized_time


def _talk_rule_to_config_dict(rule: Any) -> Dict[str, Any]:
    """把 TOML/Pydantic 规则项转换为可保存的普通字典。"""

    platform, item_id, rule_type = ChatConfigUtils._target_values(rule)
    return {
        "platform": platform,
        "item_id": item_id,
        "rule_type": rule_type or "group",
        "time": ChatConfigUtils._get_rule_time(rule),
        "value": ChatConfigUtils._get_rule_value(rule),
    }


def _is_same_talk_rule_target(rule: Dict[str, Any], chat_session: ChatSession) -> bool:
    """判断规则是否精确作用于当前聊天流。"""

    return (
        str(rule.get("platform") or "").strip() == str(chat_session.platform or "").strip()
        and str(rule.get("item_id") or "").strip() == _get_chat_target_id(chat_session)
        and str(rule.get("rule_type") or "").strip() == _get_chat_type(chat_session)
    )


async def _save_chat_talk_frequency_rule(
    chat_session: ChatSession,
    request: TalkFrequencyUpdateRequest,
) -> None:
    """写入当前聊天流的精确发言频率规则，并热重载配置。"""

    config_path = BOT_CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")

    normalized_time = _normalize_talk_rule_time(request.time)
    previous_time = (
        _normalize_talk_rule_time(request.previous_time)
        if request.previous_time is not None
        else None
    )
    next_rule = {
        "platform": str(chat_session.platform or "").strip(),
        "item_id": _get_chat_target_id(chat_session),
        "rule_type": _get_chat_type(chat_session),
        "time": normalized_time,
        "value": float(request.value),
    }
    if not next_rule["platform"] or not next_rule["item_id"]:
        raise HTTPException(status_code=400, detail="聊天流缺少平台或目标 ID，无法保存规则")

    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = tomlkit.load(config_file)

    chat_config = config_data.get("chat")
    if not isinstance(chat_config, dict):
        raise HTTPException(status_code=400, detail="配置文件缺少 [chat] 配置节")

    reply_timing_config = chat_config.get("reply_timing")
    if not isinstance(reply_timing_config, dict):
        raise HTTPException(status_code=400, detail="配置文件缺少 [chat.reply_timing] 配置节")

    raw_rules = reply_timing_config.get("talk_value_rules")
    rules = (
        [_talk_rule_to_config_dict(rule) for rule in raw_rules]
        if raw_rules is not None and not isinstance(raw_rules, (str, bytes, dict))
        else []
    )
    replace_index: Optional[int] = None
    fallback_index: Optional[int] = None
    for index, rule in enumerate(rules):
        if not _is_same_talk_rule_target(rule, chat_session):
            continue
        if str(rule.get("time") or "").strip() == normalized_time:
            replace_index = index
            break
        if previous_time is not None and str(rule.get("time") or "").strip() == previous_time:
            fallback_index = index

    target_index = replace_index if replace_index is not None else fallback_index
    if target_index is None:
        rules.append(next_rule)
    else:
        rules[target_index] = next_rule

    reply_timing_config["enable_talk_value_rules"] = True
    reply_timing_config["talk_value_rules"] = rules
    save_toml_with_format(config_data, str(config_path))

    if not await config_manager.reload_config(changed_scopes=["bot"]):
        raise HTTPException(status_code=500, detail="配置已写入，但热重载失败")


async def _delete_chat_talk_frequency_rule(chat_session: ChatSession, rule_time: Optional[str]) -> None:
    """删除当前聊天流的一条精确发言频率规则，并热重载配置。"""

    config_path = BOT_CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")

    normalized_time = _normalize_talk_rule_time(rule_time)
    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = tomlkit.load(config_file)

    chat_config = config_data.get("chat")
    if not isinstance(chat_config, dict):
        raise HTTPException(status_code=400, detail="配置文件缺少 [chat] 配置节")

    reply_timing_config = chat_config.get("reply_timing")
    if not isinstance(reply_timing_config, dict):
        raise HTTPException(status_code=400, detail="配置文件缺少 [chat.reply_timing] 配置节")

    raw_rules = reply_timing_config.get("talk_value_rules")
    rules = (
        [_talk_rule_to_config_dict(rule) for rule in raw_rules]
        if raw_rules is not None and not isinstance(raw_rules, (str, bytes, dict))
        else []
    )
    next_rules = [
        rule
        for rule in rules
        if not (
            _is_same_talk_rule_target(rule, chat_session)
            and str(rule.get("time") or "").strip() == normalized_time
        )
    ]
    if len(next_rules) == len(rules):
        raise HTTPException(status_code=404, detail="未找到可删除的当前聊天流发言频率规则")

    reply_timing_config["talk_value_rules"] = next_rules
    save_toml_with_format(config_data, str(config_path))

    if not await config_manager.reload_config(changed_scopes=["bot"]):
        raise HTTPException(status_code=500, detail="配置已写入，但热重载失败")


def _get_config_table(config_data: Any, section_name: str) -> Any:
    """读取配置节，缺失时给出清晰错误。"""

    config_section = config_data.get(section_name)
    if not isinstance(config_section, dict):
        raise HTTPException(status_code=400, detail=f"配置文件缺少 [{section_name}] 配置节")
    return config_section


async def _save_chat_learning_rule(chat_session: ChatSession, kind: str, request: LearningUpdateRequest) -> None:
    """写入当前聊天流的精确学习规则，并热重载配置。"""

    learning_config_map = {
        "expression": ("expression", "learning_list"),
        "jargon": ("jargon", "learning_list"),
        "behavior": ("experimental", "behavior_learning_list"),
    }
    config_target = learning_config_map.get(kind)
    if config_target is None:
        raise HTTPException(status_code=400, detail="未知学习类型")

    config_path = BOT_CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")

    next_rule = {
        "platform": str(chat_session.platform or "").strip(),
        "item_id": _get_chat_target_id(chat_session),
        "type": _get_chat_type(chat_session),
        "use": bool(request.use),
        "learn": bool(request.learn),
    }
    if not next_rule["platform"] or not next_rule["item_id"]:
        raise HTTPException(status_code=400, detail="聊天流缺少平台或目标 ID，无法保存学习规则")

    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = tomlkit.load(config_file)

    section_name, field_name = config_target
    config_section = _get_config_table(config_data, section_name)
    raw_rules = config_section.get(field_name)
    rules = (
        [_learning_item_to_config_dict(rule) for rule in raw_rules]
        if raw_rules is not None and not isinstance(raw_rules, (str, bytes, dict))
        else []
    )
    replace_index: Optional[int] = None
    for index, rule in enumerate(rules):
        if _is_same_learning_target(rule, chat_session):
            replace_index = index
            break

    if replace_index is None:
        rules.append(next_rule)
    else:
        rules[replace_index] = next_rule

    config_section[field_name] = rules
    if kind == "behavior" and request.learn:
        config_section["enable_behavior_learning"] = True
    save_toml_with_format(config_data, str(config_path))

    if not await config_manager.reload_config(changed_scopes=["bot"]):
        raise HTTPException(status_code=500, detail="配置已写入，但热重载失败")


async def _save_chat_prompt_rule(
    chat_session: ChatSession,
    prompt_index: Optional[int],
    request: ChatPromptUpdateRequest,
) -> None:
    """新增或更新当前聊天流的一条专属 Prompt，并热重载配置。"""

    config_path = BOT_CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")

    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt 不能为空")

    next_prompt = {
        "platform": str(chat_session.platform or "").strip(),
        "item_id": _get_chat_target_id(chat_session),
        "rule_type": _get_chat_type(chat_session),
        "prompt": prompt,
    }
    if not next_prompt["platform"] or not next_prompt["item_id"]:
        raise HTTPException(status_code=400, detail="聊天流缺少平台或目标 ID，无法保存 Prompt")

    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = tomlkit.load(config_file)

    chat_config = _get_config_table(config_data, "chat")
    reply_style_config = chat_config.get("reply_style")
    if not isinstance(reply_style_config, dict):
        raise HTTPException(status_code=400, detail="配置文件缺少 [chat.reply_style] 配置节")

    raw_prompts = reply_style_config.get("chat_prompts")
    prompts = (
        [
            prompt_config
            for item in raw_prompts
            if (prompt_config := _chat_prompt_to_config_dict(item)) is not None
        ]
        if raw_prompts is not None and not isinstance(raw_prompts, (str, bytes, dict))
        else []
    )

    if prompt_index is None:
        prompts.append(next_prompt)
    elif 0 <= prompt_index < len(prompts) and _is_same_prompt_target(prompts[prompt_index], chat_session):
        prompts[prompt_index] = next_prompt
    else:
        raise HTTPException(status_code=404, detail="未找到可修改的当前聊天流 Prompt")

    reply_style_config["chat_prompts"] = prompts
    save_toml_with_format(config_data, str(config_path))

    if not await config_manager.reload_config(changed_scopes=["bot"]):
        raise HTTPException(status_code=500, detail="配置已写入，但热重载失败")


async def _delete_chat_prompt_rule(chat_session: ChatSession, prompt_index: int) -> None:
    """删除当前聊天流的一条专属 Prompt，并热重载配置。"""

    config_path = BOT_CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")

    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = tomlkit.load(config_file)

    chat_config = _get_config_table(config_data, "chat")
    reply_style_config = chat_config.get("reply_style")
    if not isinstance(reply_style_config, dict):
        raise HTTPException(status_code=400, detail="配置文件缺少 [chat.reply_style] 配置节")

    raw_prompts = reply_style_config.get("chat_prompts")
    prompts = (
        [
            prompt_config
            for item in raw_prompts
            if (prompt_config := _chat_prompt_to_config_dict(item)) is not None
        ]
        if raw_prompts is not None and not isinstance(raw_prompts, (str, bytes, dict))
        else []
    )
    if not (0 <= prompt_index < len(prompts)) or not _is_same_prompt_target(prompts[prompt_index], chat_session):
        raise HTTPException(status_code=404, detail="未找到可删除的当前聊天流 Prompt")

    del prompts[prompt_index]
    reply_style_config["chat_prompts"] = prompts
    save_toml_with_format(config_data, str(config_path))

    if not await config_manager.reload_config(changed_scopes=["bot"]):
        raise HTTPException(status_code=500, detail="配置已写入，但热重载失败")


def _chat_session_detail_to_response(chat_session: ChatSession) -> Dict[str, Any]:
    """构建单个聊天流的详情响应。"""

    session_id = chat_session.session_id
    # 确保配置匹配工具能基于真实聊天流元数据判断通配与定向规则。
    core_chat_manager.get_existing_session_by_session_id(session_id)

    expression_use, expression_learn = ExpressionConfigUtils.get_expression_config_for_chat(session_id)
    behavior_use, behavior_learn = BehaviorConfigUtils.get_behavior_config_for_chat(session_id)
    jargon_use, jargon_learn = JargonConfigUtils.get_jargon_config_for_chat(session_id)
    return {
        "session_id": session_id,
        "display_name": _get_chat_display_name(chat_session, None),
        "chat_type": _get_chat_type(chat_session),
        "platform": chat_session.platform,
        "target_id": _get_chat_target_id(chat_session),
        "group_id": chat_session.group_id,
        "user_id": chat_session.user_id,
        "expression": {
            "use": expression_use,
            "learn": expression_learn,
            "matched_rule": _target_config_to_dict(ExpressionConfigUtils._find_expression_config_item(session_id)),
        },
        "behavior": {
            "use": behavior_use,
            "learn": behavior_learn,
            "matched_rule": _target_config_to_dict(BehaviorConfigUtils._find_behavior_config_item(session_id)),
        },
        "jargon": {
            "use": jargon_use,
            "learn": jargon_learn,
            "matched_rule": _target_config_to_dict(JargonConfigUtils._find_jargon_config_item(session_id)),
        },
        "talk_frequency": _get_talk_rule_details(chat_session),
        "prompts": _get_chat_prompt_details(chat_session),
        "adapters": _get_adapter_policy_details(chat_session),
    }


def _get_adapter_policy_details(chat_session: ChatSession) -> List[Dict[str, Any]]:
    """返回当前聊天流在各适配器插件中的路由与放行状态。"""

    platform = str(chat_session.platform or "").strip()
    target_id = _get_chat_target_id(chat_session)
    chat_type = _get_chat_type(chat_session)
    if not platform or not target_id:
        return []

    try:
        chat_route_key = RouteKey(
            platform=platform,
            account_id=str(chat_session.account_id or "").strip() or None,
            scope=str(chat_session.scope or "").strip() or None,
        )
    except ValueError:
        return []

    platform_io_manager = get_platform_io_manager()
    policy_manager = get_adapter_policy_manager()
    adapter_details: List[Dict[str, Any]] = []
    for driver in platform_io_manager.driver_registry.list(kind=DriverKind.PLUGIN):
        descriptor = driver.descriptor
        metadata = descriptor.metadata if isinstance(descriptor.metadata, dict) else {}
        if str(metadata.get("plugin_type") or "").strip().lower() != "adapter":
            continue

        identity = _get_driver_adapter_identity(driver)
        policy_result = policy_manager.evaluate(
            identity,
            chat_type=chat_type,
            target_id=target_id,
        )
        send_bound = platform_io_manager.send_route_table.has_binding_for_driver(chat_route_key, descriptor.driver_id)
        receive_bound = platform_io_manager.receive_route_table.has_binding_for_driver(
            chat_route_key,
            descriptor.driver_id,
        )
        adapter_details.append(
            {
                "adapter_id": descriptor.driver_id,
                "plugin_id": descriptor.plugin_id or "",
                "gateway_name": identity.gateway_name,
                "platform": descriptor.platform,
                "account_id": descriptor.account_id,
                "scope": descriptor.scope,
                "protocol": metadata.get("protocol") or "",
                "route_type": metadata.get("route_type") or "",
                "send_bound": send_bound,
                "receive_bound": receive_bound,
                "routed": send_bound or receive_bound,
                "policy": policy_result.to_dict(),
            }
        )

    return sorted(adapter_details, key=lambda item: (not item["routed"], item["plugin_id"], item["gateway_name"]))


def _get_driver_adapter_identity(driver: Any) -> AdapterIdentity:
    """根据运行中的插件驱动描述构造适配器策略身份。"""

    descriptor = driver.descriptor
    metadata = descriptor.metadata if isinstance(descriptor.metadata, dict) else {}
    return AdapterIdentity(
        adapter_id=descriptor.driver_id,
        plugin_id=descriptor.plugin_id or "",
        gateway_name=str(metadata.get("gateway_name") or "").strip(),
        platform=descriptor.platform,
        account_id=descriptor.account_id,
        scope=descriptor.scope,
    )


def _get_running_adapter_identity(adapter_id: str) -> AdapterIdentity:
    """按 driver_id 查找运行中的适配器插件。"""

    normalized_adapter_id = str(adapter_id or "").strip()
    if not normalized_adapter_id:
        raise HTTPException(status_code=400, detail="缺少适配器 ID")

    driver = get_platform_io_manager().driver_registry.get(normalized_adapter_id)
    if driver is None:
        raise HTTPException(status_code=404, detail="适配器当前未运行，无法修改策略")

    metadata = driver.descriptor.metadata if isinstance(driver.descriptor.metadata, dict) else {}
    if str(metadata.get("plugin_type") or "").strip().lower() != "adapter":
        raise HTTPException(status_code=400, detail="目标驱动不是适配器插件")
    return _get_driver_adapter_identity(driver)


async def _save_adapter_policy_rule(chat_session: ChatSession, request: AdapterPolicyUpdateRequest) -> None:
    """写入当前聊天流在指定适配器上的显式放行策略。"""

    target_id = _get_chat_target_id(chat_session)
    if not target_id:
        raise HTTPException(status_code=400, detail="聊天流缺少目标 ID，无法保存适配器策略")

    try:
        get_adapter_policy_manager().set_chat_override(
            _get_running_adapter_identity(request.adapter_id),
            chat_type=_get_chat_type(chat_session),
            target_id=target_id,
            action=request.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


SESSION_DELETE_TABLES = [
    ("messages", "消息", Messages, "session_id"),
    ("expressions", "表达", Expression, "session_id"),
    ("tool_records", "工具调用记录", ToolRecord, "session_id"),
    ("behavior_experience_paths", "行为经验路径", BehaviorExperiencePath, "session_id"),
    ("behavior_scene_clusters", "行为场景簇", BehaviorSceneCluster, "session_id"),
    ("behavior_actions", "行为动作", BehaviorAction, "session_id"),
    ("behavior_outcomes", "行为结果", BehaviorOutcome, "session_id"),
    ("statistics_message_hourly", "消息统计", StatisticsMessageHourly, "chat_id"),
    ("high_frequency_terms", "高频词", HighFrequencyTerm, "chat_id"),
    ("chat_sessions", "聊天流记录", ChatSession, "session_id"),
]


def _delete_rows_by_text_field(session: Any, model: Any, field_name: str, session_id: str) -> int:
    """删除指定模型中归属于当前聊天流的记录。"""

    field = getattr(model, field_name)
    result = session.exec(delete(model).where(col(field) == session_id))
    return int(result.rowcount or 0)


def _delete_or_unlink_jargons(session: Any, session_id: str) -> Dict[str, int]:
    """清理黑话中的聊天流关联；仅剩当前聊天流时删除整条黑话。"""

    deleted = 0
    unlinked = 0
    removed_refs = 0
    candidate_statement = select(Jargon).where(func.instr(col(Jargon.session_id_dict), f'"{session_id}"') > 0)
    candidates = session.exec(candidate_statement).all()
    for jargon in candidates:
        try:
            session_counts = json.loads(jargon.session_id_dict or "{}")
        except json.JSONDecodeError:
            logger.warning(f"跳过无法解析 session_id_dict 的黑话记录: jargon_id={jargon.id}")
            continue
        if not isinstance(session_counts, dict) or session_id not in session_counts:
            continue

        removed_count = int(session_counts.pop(session_id, 0) or 0)
        removed_refs += 1
        if not session_counts and not jargon.is_global:
            session.delete(jargon)
            deleted += 1
            continue

        jargon.session_id_dict = json.dumps(session_counts, ensure_ascii=False)
        if removed_count > 0:
            jargon.count = max(0, int(jargon.count or 0) - removed_count)
        session.add(jargon)
        unlinked += 1

    return {
        "deleted": deleted,
        "unlinked": unlinked,
        "removed_refs": removed_refs,
    }


def _release_deleted_chat_runtime(session_id: str) -> None:
    """移除运行期缓存，避免定时保存把已删除聊天流重新写回数据库。"""

    core_chat_manager.sessions.pop(session_id, None)
    heartflow_manager.heartflow_chat_list.pop(session_id, None)


def _delete_chat_session_scope(session_id: str) -> Dict[str, Any]:
    """删除聊天流及所有直接归属该 session_id 的数据库记录。"""

    with get_db_session() as session:
        chat_session = session.exec(select(ChatSession).where(col(ChatSession.session_id) == session_id)).first()
        if chat_session is None:
            raise HTTPException(status_code=404, detail=f"聊天流不存在: {session_id}")

        items: List[Dict[str, Any]] = []
        total_deleted = 0

        jargon_result = _delete_or_unlink_jargons(session, session_id)
        if jargon_result["deleted"] or jargon_result["unlinked"]:
            items.append(
                {
                    "key": "jargons",
                    "label": "黑话",
                    "count": jargon_result["deleted"],
                    "unlinked": jargon_result["unlinked"],
                }
            )
        total_deleted += jargon_result["deleted"]

        for key, label, model, field_name in SESSION_DELETE_TABLES:
            deleted_count = _delete_rows_by_text_field(session, model, field_name, session_id)
            total_deleted += deleted_count
            items.append({"key": key, "label": label, "count": deleted_count})

    _release_deleted_chat_runtime(session_id)
    logger.warning(
        "已删除聊天流及关联数据: "
        f"session_id={session_id} total_deleted={total_deleted} items={items}"
    )
    return {
        "success": True,
        "session_id": session_id,
        "deleted_total": total_deleted,
        "jargons": jargon_result,
        "items": items,
    }


@router.get("/history")
async def get_chat_history(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: Optional[str] = Query(default=None),
    group_id: Optional[str] = Query(default=None),
) -> Dict[str, object]:
    """获取聊天历史记录。

    优先按 ``group_id`` 加载虚拟群聊历史；未提供时使用规范化后的 ``user_id`` 加载 WebUI 私聊历史。
    """
    if group_id:
        history = chat_history.get_history(limit, group_id=group_id)
    else:
        normalized_user_id = normalize_webui_user_id(user_id)
        history = chat_history.get_history(limit, user_id=normalized_user_id)
    return {"success": True, "messages": history, "total": len(history)}


@router.get("/platforms")
async def get_available_platforms() -> Dict[str, object]:
    """获取可用平台列表。"""
    try:
        with get_db_session() as session:
            statement = (
                select(PersonInfo.platform, func.count().label("count"))
                .group_by(PersonInfo.platform)
                .order_by(func.count().desc())
            )
            platforms = session.exec(statement).all()

        result = [{"platform": platform, "count": count} for platform, count in platforms if platform]
        return {"success": True, "platforms": result}
    except Exception as e:
        logger.error(f"获取平台列表失败: {e}")
        return {"success": False, "error": str(e), "platforms": []}


@router.get("/persons")
async def get_persons_by_platform(
    platform: str = Query(..., description="平台名称"),
    search: Optional[str] = Query(default=None, description="搜索关键词"),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, object]:
    """获取指定平台的用户列表。"""
    try:
        statement = select(PersonInfo).where(col(PersonInfo.platform) == platform)
        if search:
            statement = statement.where(
                (col(PersonInfo.person_name).contains(search))
                | (col(PersonInfo.user_nickname).contains(search))
                | (col(PersonInfo.user_id).contains(search))
            )

        statement = statement.order_by(
            case((col(PersonInfo.last_known_time).is_(None), 1), else_=0),
            col(PersonInfo.last_known_time).desc(),
        ).limit(limit)

        with get_db_session() as session:
            persons = session.exec(statement).all()
            result = [
                {
                    "person_id": person.person_id,
                    "user_id": person.user_id,
                    "person_name": person.person_name,
                    "nickname": person.user_nickname,
                    "is_known": person.is_known,
                    "platform": person.platform,
                    "display_name": person.person_name or person.user_nickname or person.user_id,
                }
                for person in persons
            ]
        return {"success": True, "persons": result, "total": len(result)}
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        return {"success": False, "error": str(e), "persons": []}


@router.get("/sessions")
async def get_chat_sessions(
    limit: int = Query(default=200, ge=1, le=1000),
) -> Dict[str, object]:
    """获取已存在的聊天流列表。"""

    with get_db_session() as session:
        statement = (
            select(ChatSession)
            .order_by(
                case((col(ChatSession.last_active_timestamp).is_(None), 1), else_=0),
                col(ChatSession.last_active_timestamp).desc(),
                col(ChatSession.created_timestamp).desc(),
            )
            .limit(limit)
        )
        chat_sessions = session.exec(statement).all()

    session_ids = [chat_session.session_id for chat_session in chat_sessions if chat_session.session_id]
    display_fallback_session_ids = [
        chat_session.session_id
        for chat_session in chat_sessions
        if chat_session.session_id and _needs_latest_message_for_display_name(chat_session)
    ]
    latest_messages = _get_latest_messages_by_session(display_fallback_session_ids)
    message_counts = _get_message_counts_by_session(session_ids)
    expression_counts = _get_expression_counts_by_session(session_ids)
    jargon_counts = _get_jargon_counts_by_session(session_ids)
    items = [
        _chat_session_to_response(
            chat_session=chat_session,
            latest_message=latest_messages.get(chat_session.session_id),
            message_count=message_counts.get(chat_session.session_id, 0),
            expression_count=expression_counts.get(chat_session.session_id, 0),
            jargon_count=jargon_counts.get(chat_session.session_id, 0),
        )
        for chat_session in chat_sessions
    ]
    return {"success": True, "sessions": items, "total": len(items)}


@router.get("/resolve-target")
async def resolve_chat_target(
    platform: str = Query(..., description="平台名称"),
    item_id: str = Query(..., description="群号或用户 ID"),
    rule_type: str = Query(default="group", description="聊天类型：group/private"),
) -> Dict[str, object]:
    """按配置目标解析真实聊天流，用于配置页即时校验。"""

    result = _resolve_chat_targets(
        [ChatTargetResolveItem(platform=platform, item_id=item_id, rule_type=rule_type)]
    )[0]
    return {"success": True, **result}


@router.post("/resolve-targets")
async def resolve_chat_targets(request: ChatTargetResolveBatchRequest) -> Dict[str, object]:
    """批量按配置目标解析真实聊天流，用于配置页即时校验。"""

    return {"success": True, "results": _resolve_chat_targets(request.targets[:200])}


@router.get("/sessions/{session_id}")
async def get_chat_session_detail(session_id: str) -> Dict[str, object]:
    """获取单个聊天流详情。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    with get_db_session() as session:
        chat_session = session.exec(
            select(ChatSession).where(col(ChatSession.session_id) == normalized_session_id)
        ).first()

    if chat_session is None:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {normalized_session_id}")

    return {"success": True, "detail": _chat_session_detail_to_response(chat_session)}


@router.delete("/sessions/{session_id}")
async def delete_chat_session(session_id: str) -> Dict[str, object]:
    """删除聊天流及所有与该 session_id 直接关联的数据。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    return _delete_chat_session_scope(normalized_session_id)


@router.put("/sessions/{session_id}/talk-frequency")
async def update_chat_session_talk_frequency(
    session_id: str,
    request: TalkFrequencyUpdateRequest,
) -> Dict[str, object]:
    """为当前聊天流新增或更新一条精确发言频率规则。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    with get_db_session() as session:
        chat_session = session.exec(
            select(ChatSession).where(col(ChatSession.session_id) == normalized_session_id)
        ).first()

    if chat_session is None:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {normalized_session_id}")

    await _save_chat_talk_frequency_rule(chat_session, request)
    return {"success": True, "detail": _chat_session_detail_to_response(chat_session)}


@router.delete("/sessions/{session_id}/talk-frequency")
async def delete_chat_session_talk_frequency(
    session_id: str,
    time: Optional[str] = Query(default=None),
) -> Dict[str, object]:
    """删除当前聊天流的一条精确发言频率规则。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    with get_db_session() as session:
        chat_session = session.exec(
            select(ChatSession).where(col(ChatSession.session_id) == normalized_session_id)
        ).first()

    if chat_session is None:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {normalized_session_id}")

    await _delete_chat_talk_frequency_rule(chat_session, time)
    return {"success": True, "detail": _chat_session_detail_to_response(chat_session)}


@router.put("/sessions/{session_id}/learning/{kind}")
async def update_chat_session_learning(
    session_id: str,
    kind: str,
    request: LearningUpdateRequest,
) -> Dict[str, object]:
    """为当前聊天流新增或更新一条精确学习配置。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    with get_db_session() as session:
        chat_session = session.exec(
            select(ChatSession).where(col(ChatSession.session_id) == normalized_session_id)
        ).first()

    if chat_session is None:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {normalized_session_id}")

    await _save_chat_learning_rule(chat_session, kind, request)
    return {"success": True, "detail": _chat_session_detail_to_response(chat_session)}


@router.put("/sessions/{session_id}/prompts")
async def upsert_chat_session_prompt(
    session_id: str,
    request: ChatPromptUpdateRequest,
    index: Optional[int] = Query(default=None, ge=0),
) -> Dict[str, object]:
    """为当前聊天流新增或更新一条专属 Prompt。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    with get_db_session() as session:
        chat_session = session.exec(
            select(ChatSession).where(col(ChatSession.session_id) == normalized_session_id)
        ).first()

    if chat_session is None:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {normalized_session_id}")

    await _save_chat_prompt_rule(chat_session, index, request)
    return {"success": True, "detail": _chat_session_detail_to_response(chat_session)}


@router.put("/sessions/{session_id}/adapters/policy")
async def update_chat_session_adapter_policy(
    session_id: str,
    request: AdapterPolicyUpdateRequest,
) -> Dict[str, object]:
    """为当前聊天流新增、更新或移除一条适配器显式放行规则。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    with get_db_session() as session:
        chat_session = session.exec(
            select(ChatSession).where(col(ChatSession.session_id) == normalized_session_id)
        ).first()

    if chat_session is None:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {normalized_session_id}")

    await _save_adapter_policy_rule(chat_session, request)
    return {"success": True, "detail": _chat_session_detail_to_response(chat_session)}


@router.delete("/sessions/{session_id}/prompts/{index}")
async def delete_chat_session_prompt(session_id: str, index: int) -> Dict[str, object]:
    """删除当前聊天流的一条专属 Prompt。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 session_id")

    with get_db_session() as session:
        chat_session = session.exec(
            select(ChatSession).where(col(ChatSession.session_id) == normalized_session_id)
        ).first()

    if chat_session is None:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {normalized_session_id}")

    await _delete_chat_prompt_rule(chat_session, index)
    return {"success": True, "detail": _chat_session_detail_to_response(chat_session)}


@router.delete("/history")
async def clear_chat_history(
    user_id: Optional[str] = Query(default=None),
    group_id: Optional[str] = Query(default=None),
) -> Dict[str, object]:
    """清空聊天历史记录。

    优先按 ``group_id`` 清理虚拟群聊历史；未提供时使用规范化后的 ``user_id`` 清理 WebUI 私聊历史。
    """
    if group_id:
        deleted = chat_history.clear_history(group_id=group_id)
    else:
        normalized_user_id = normalize_webui_user_id(user_id)
        deleted = chat_history.clear_history(user_id=normalized_user_id)
    return {"success": True, "message": f"已清空 {deleted} 条聊天记录"}


@router.get("/info")
async def get_chat_info() -> Dict[str, object]:
    """获取聊天室信息。"""
    return {
        "bot_name": global_config.bot.nickname,
        "platform": WEBUI_CHAT_PLATFORM,
        "active_sessions": len(chat_manager.active_connections),
    }
