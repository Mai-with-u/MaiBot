"""private 场景提示词及回复模板选择。"""

from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.config.official_configs import AttentionDriftConfig
from src.maisaka.scenes.prompting import ChatPromptProfile


class PrivatePromptProfile(ChatPromptProfile):
    def attention_text(self) -> str:
        return global_config.chat.reply_style.private_chat_prompts

    def attention_drift_config(self) -> AttentionDriftConfig:
        return global_config.experimental.private_attention_drift

    def query_memory_rule(self) -> str:
        return load_prompt("planner_memory_private").strip()

    def select_chat_reply_template(self, *, self_reply: bool, mai_replier: bool) -> str:
        if mai_replier:
            return "reply_private_followup_mai_replier" if self_reply else "reply_private_mai_replier"
        return "reply_private_followup" if self_reply else "reply_private"
