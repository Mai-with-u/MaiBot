"""保留 Planner 请求前缀，在末尾追加完整的创意回复任务。"""

from typing import List, Union

from src.agent.planner import ACPPlannerSnapshot
from src.common.prompt_i18n import load_prompt
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextContentPart,
    ContextItem,
    ContextItemMeta,
    ContextTextPart,
    SystemMessageItem,
    UserMessageItem,
)


def build_cached_reply_request(
    snapshot: Union[ResponseRequest, ACPPlannerSnapshot], reply_messages: List[ContextItem],
    reply_reference: str = "",
) -> Union[ResponseRequest, ACPPlannerSnapshot]:
    """不重写前缀及工具定义；保留 Replyer 全部文本和图片，而非只提取纯文本。"""
    if not reply_messages:
        raise ValueError("回复缓存模式缺少 Replyer 请求内容")
    parts: List[ContextContentPart] = [ContextTextPart(load_prompt("reply_cache"))]
    for item in reply_messages:
        if not isinstance(item, (SystemMessageItem, UserMessageItem)):
            raise ValueError("回复缓存模式的 Replyer 请求只允许 system/user 消息")
        role = item.role.value
        parts.append(ContextTextPart(f"\n<replyer_{role}>\n"))
        parts.extend(item.parts)
        parts.append(ContextTextPart(f"\n</replyer_{role}>\n"))
    request = snapshot.detached_snapshot()
    if reply_reference.strip():
        request.context_items.append(AssistantMessageItem(
            meta=ContextItemMeta.create(), parts=(ContextTextPart(reply_reference),),
        ))
    request.context_items.append(UserMessageItem(meta=ContextItemMeta.create(), parts=tuple(parts)))
    return request
