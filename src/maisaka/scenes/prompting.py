"""场景提示词接口；无会话的 CLI/通用请求使用显式的未绑定配置。"""

from typing import Dict, Optional

from src.common.i18n import get_locale
from src.config.official_configs import AttentionDriftConfig
from src.maisaka.attention_drift import render_attention_drift_prompt


def localized_text(texts: Dict[str, str]) -> str:
    return texts.get(get_locale(), texts["zh-CN"])


class ChatPromptProfile:
    """共享渲染格式，不决定群聊或私聊配置。"""

    def attention_text(self) -> str:
        return ""

    def attention_drift_config(self) -> Optional[AttentionDriftConfig]:
        return None

    def attention_drift_block(self) -> str:
        config = self.attention_drift_config()
        return "" if config is None else render_attention_drift_prompt(config)

    def planner_attention_block(self) -> str:
        text = self.attention_text().strip()
        return text + "\n" if text else ""

    def replyer_attention_block(self, extra_prompt: str) -> str:
        lines = []
        if text := self.attention_text().strip():
            lines.append(f"通用注意事项：\n{text}")
        if extra_prompt := extra_prompt.strip():
            lines.append(f"当前聊天额外注意事项：\n{extra_prompt}")
        return "在该聊天中的注意事项：\n" + "\n\n".join(lines) + "\n" if lines else ""

    def select_chat_reply_template(self, *, self_reply: bool, mai_replier: bool) -> str:
        raise ValueError("回复模板选择需要已绑定的聊天场景")

    def query_memory_rule(self) -> str:
        return ""

    def update_group_cardname(self, current: str, incoming: str) -> str:
        return ""

    def group_cardname_notice(self, cardname: str, bot_name: str, skip_same: bool) -> str:
        return ""


class UnscopedPromptProfile(ChatPromptProfile):
    """CLI/未绑定会话不注入群聊或私聊设置；保留通用记忆检索提示。"""

    def query_memory_rule(self) -> str:
        return localized_text(
            {
                "en-US": '- query_memory(): Consider retrieval more actively when the other person mentions signals like "before", "last time", "recently", "do you remember", "I like", or "I said", or when the reply depends on long-term preferences, prior promises, shared experiences, or long-term information about a person.',
                "ja-JP": "- query_memory()：相手が「前に」「この前」「最近」「覚えてる？」「好き」「言った」などの合図を出した場合、または返信が長期的な好み、以前の約束、共有した経験、人物の長期的な情報に依存する場合は、より積極的に検索を検討できます。",
                "zh-CN": "- query_memory()：当对方提到“之前”“上次”“最近”“还记得吗”“我喜欢”“我说过”等信号，或回复依赖长期偏好、先前承诺、共同经历、人物长期信息时，可以更积极检索。",
            }
        )
