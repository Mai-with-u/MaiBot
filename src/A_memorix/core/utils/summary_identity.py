"""为摘要构造有明确身份、引用边界和日期分组的聊天窗口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Tuple

import json
import re

from src.common.data_models.message_component_data_model import (
    AtComponent,
    ForwardNodeComponent,
    ReplyComponent,
    TextComponent,
)

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
else:
    # 运行时解析类型注解也不应加载聊天模块及其数据库、配置依赖。
    SessionMessage = Any


IDENTITY_REFERENCE = re.compile(r"\[U\d+\]")


def observe_identity(
    store: Any,
    *,
    platform: str,
    user_id: str,
    aliases: List[str],
    group_id: str = "",
    refresh_profile: bool = False,
) -> Dict[str, Any]:
    """从真实发送者元数据登记身份；首次接入时补收已有消息和人物主档中的称呼。"""
    from sqlmodel import select

    from src.common.database.database import get_db_session
    from src.common.database.database_model import Messages, PersonInfo
    from src.person_info.person_info import get_person_id

    if not platform.strip() or not user_id.strip():
        raise ValueError("消息缺少真实的发送者 platform/user_id")
    # 与宿主 get_person_id 对适配器平台前缀的处理保持一致。
    identity_platform = platform.split("-")[1] if "-" in platform else platform
    person_id = get_person_id(platform, user_id)
    existing = store.get_person_identity(person_id)
    preferred_name = ""
    if existing is None or refresh_profile:
        with get_db_session(auto_commit=False) as session:
            person = session.exec(select(PersonInfo).where(PersonInfo.person_id == person_id)).first()
            if person is not None:
                preferred_name = person.person_name or ""
                aliases = [*aliases, person.person_name or "", person.user_nickname]
                cards = json.loads(person.group_cardname) if person.group_cardname else []
                aliases.extend(str(card.get("group_cardname") or card.get("group_nick_name") or "") for card in cards)
    if existing is None:
        with get_db_session(auto_commit=False) as session:
            # 只按 ID 收集旧称呼，不用同名检索建立身份关系。
            historical = session.exec(
                select(Messages.user_nickname, Messages.user_cardname)
                .where(Messages.platform.in_([platform, identity_platform]), Messages.user_id == user_id)
                .distinct()
            ).all()
            aliases = [*aliases, *(name for pair in historical for name in pair if name)]
    if refresh_profile:
        override = store.get_person_profile_alias_override(person_id)
        if override:
            aliases = [*aliases, *override["aliases"]]
    return store.observe_person_identity(
        person_id=person_id,
        platform=identity_platform,
        user_id=user_id,
        aliases=aliases,
        group_id=group_id,
        preferred_name=preferred_name,
    )


class SummaryIdentityContext:
    def __init__(self, store: Any, messages: Sequence[SessionMessage]) -> None:
        self.store = store
        self.messages = list(messages)
        self.identities: Dict[str, Dict[str, Any]] = {}
        self.references: Dict[str, str] = {}
        self._message_index = {(msg.platform, msg.session_id, msg.message_id): msg for msg in messages}

    def _identity(
        self,
        message: SessionMessage,
        user_id: str,
        nickname: str = "",
        cardname: str = "",
    ) -> str:
        from src.person_info.person_info import get_person_id

        group = message.message_info.group_info
        identity = observe_identity(
            self.store,
            platform=message.platform,
            user_id=user_id,
            aliases=[nickname, cardname],
            group_id=group.group_id if group else "",
            refresh_profile=get_person_id(message.platform, user_id) not in self.references,
        )
        person_id = identity["person_id"]
        if person_id not in self.references:
            reference = f"[U{len(self.references) + 1}]"
            self.references[person_id] = reference
            self.identities[reference] = identity
        else:
            self.identities[self.references[person_id]] = identity
        return self.references[person_id]

    @staticmethod
    def _data_text(text: str) -> str:
        # 用户正文不得伪造由程序分配的身份标记。
        return IDENTITY_REFERENCE.sub(lambda match: match[0].replace("[", "［").replace("]", "］"), text)

    def _reply(self, message: SessionMessage, component: ReplyComponent, depth: int) -> str:
        from src.common.message_repository import find_messages

        key = (message.platform, message.session_id, component.target_message_id)
        original = self._message_index.get(key)
        if original is None:
            matches = find_messages(
                platform=message.platform,
                session_id=message.session_id,
                message_id=component.target_message_id,
                limit=2,
            )
            if len(matches) > 1:
                raise ValueError("引用消息 ID 在当前聊天流中不唯一")
            original = matches[0] if matches else None
        if original is not None:
            sender = original.message_info.user_info
            author = self._identity(original, sender.user_id, sender.user_nickname, sender.user_cardname or "")
            content = self._components(original, original.raw_message.components, depth + 1, include_reply=False)
            original_date = original.timestamp.strftime("%Y-%m-%d")
            date_note = (
                f" [原消息日期：{original_date}]" if original.timestamp.date() != message.timestamp.date() else ""
            )
        elif component.target_message_sender_id:
            author = self._identity(
                message,
                component.target_message_sender_id,
                component.target_message_sender_nickname or "",
                component.target_message_sender_cardname or "",
            )
            content = self._data_text(component.target_message_content or "")
            date_note = " [原消息日期未知]"
        else:
            author = "作者未核验"
            content = self._data_text(component.target_message_content or "原消息不可用")
            date_note = " [原消息日期未知]"
        return f"引用 {author}{date_note}：{json.dumps(content, ensure_ascii=False)}"

    def _components(
        self,
        message: SessionMessage,
        components: Sequence[Any],
        depth: int = 0,
        *,
        include_reply: bool = True,
    ) -> str:
        from src.services.message_service import _render_without_reply_components

        if depth > 5:
            raise ValueError("记忆输入中的引用或转发嵌套超过 5 层")
        parts: List[str] = []
        for component in components:
            if isinstance(component, ReplyComponent):
                if include_reply:
                    parts.append(self._reply(message, component, depth))
            elif isinstance(component, TextComponent):
                parts.append(self._data_text(component.text))
            elif isinstance(component, AtComponent):
                reference = self._identity(
                    message,
                    component.target_user_id,
                    component.target_user_nickname or "",
                    component.target_user_cardname or "",
                )
                parts.append(f"@{reference}")
            elif isinstance(component, ForwardNodeComponent):
                nodes = []
                for node in component.forward_components:
                    author = (
                        self._identity(message, node.user_id, node.user_nickname, node.user_cardname or "")
                        if node.user_id
                        else "作者未核验"
                    )
                    content = self._components(message, node.content, depth + 1)
                    nodes.append(f"{author}：{json.dumps(content, ensure_ascii=False)}")
                parts.append("转发资料，非当前作者自述：" + " | ".join(nodes))
            else:
                parts.append(self._data_text(_render_without_reply_components(message, [component])))
        return " ".join(parts).strip()

    def render(self) -> Tuple[str, str]:
        from src.chat.utils.utils import is_bot_self

        lines: List[str] = []
        current_date = ""
        for message in self.messages:
            sender = message.message_info.user_info
            author = self._identity(message, sender.user_id, sender.user_nickname, sender.user_cardname or "")
            date = message.timestamp.strftime("%Y-%m-%d")
            if date != current_date:
                lines.append(f"日期：{date}")
                current_date = date
            own_components = [item for item in message.raw_message.components if not isinstance(item, ReplyComponent)]
            body = self._components(message, own_components)
            # 引文与当前作者自己的文字分开，不能因缓存过正文而丢失引文边界。
            quotes = [
                self._reply(message, item, 0)
                for item in message.raw_message.components
                if isinstance(item, ReplyComponent)
            ]
            line = f"[{message.timestamp:%H:%M:%S}] {author}：{json.dumps(body, ensure_ascii=False)}"
            if quotes:
                line += " | " + " | ".join(quotes)
            lines.append(line)
        history = "\n".join(lines)
        # 全部历史称呼留在程序侧；模型只需看到窗口正文中实际出现的称呼。
        catalog = [
            {
                "ref": ref,
                "name": item["canonical_name"],
                "aliases": [alias for alias in item["aliases"] if alias in history],
                "is_bot": is_bot_self(item["platform"], item["user_id"]),
            }
            for ref, item in self.identities.items()
        ]
        return history, json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))

    def restore_text(self, text: str) -> str:
        """模型只能引用已登记的短标记，程序恢复为跨窗口稳定的名称。"""

        def replace(match: re.Match[str]) -> str:
            if match[0] not in self.identities:
                raise ValueError(f"摘要返回了未登记的身份标记: {match[0]}")
            return str(self.identities[match[0]]["canonical_name"])

        return IDENTITY_REFERENCE.sub(replace, text)

    def metadata(self, texts: Sequence[str]) -> Dict[str, Any]:
        used = {match[0] for text in texts for match in IDENTITY_REFERENCE.finditer(text)}
        if used - self.identities.keys():
            raise ValueError("摘要包含未登记的身份标记")
        people = [self.identities[ref] for ref in self.identities if ref in used]
        return {
            "person_ids": [item["person_id"] for item in people],
            "identity_bindings": {item["canonical_name"]: item for item in people},
        }
