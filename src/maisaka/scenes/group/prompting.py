"""group 场景提示词及回复模板选择。"""

from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.config.official_configs import AttentionDriftConfig
from src.maisaka.scenes.prompting import ChatPromptProfile, localized_text


class GroupPromptProfile(ChatPromptProfile):
    def attention_text(self) -> str:
        return global_config.chat.reply_style.group_chat_prompt

    def attention_drift_config(self) -> AttentionDriftConfig:
        return global_config.experimental.group_attention_drift

    def query_memory_rule(self) -> str:
        return load_prompt("planner_memory_group").strip()

    def update_group_cardname(self, current: str, incoming: str) -> str:
        return incoming or current

    def group_cardname_notice(self, cardname: str, bot_name: str, skip_same: bool) -> str:
        if not cardname or (skip_same and cardname == bot_name):
            return ""
        return localized_text(
            {
                "zh-CN": f"你在本群的昵称是“{cardname}”。",
                "en-US": f'Your nickname in this group is "{cardname}".',
                "ja-JP": f"このグループでのあなたのニックネームは「{cardname}」です。",
            }
        )

    def select_chat_reply_template(self, *, self_reply: bool, mai_replier: bool) -> str:
        # 专用模板沿用 Mai Replier 的训练文案。
        return "reply_group_mai_replier" if mai_replier else "reply_group"
