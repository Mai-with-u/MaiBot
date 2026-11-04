"""private 场景提示词及回复模板选择。"""

from src.config.config import global_config
from src.config.official_configs import AttentionDriftConfig
from src.maisaka.scenes.prompting import ChatPromptProfile, localized_text


class PrivatePromptProfile(ChatPromptProfile):
    def attention_text(self) -> str:
        return global_config.chat.reply_style.private_chat_prompts

    def attention_drift_config(self) -> AttentionDriftConfig:
        return global_config.experimental.private_attention_drift

    def query_memory_rule(self) -> str:
        return localized_text(
            {
                "en-US": '- query_memory(): Consider retrieval more actively when the other person mentions signals like "before", "last time", "recently", "do you remember", "I like", or "I said", or when the reply depends on long-term preferences, prior promises, shared experiences, or long-term information about a person.',
                "ja-JP": "- query_memory()：相手が「前に」「この前」「最近」「覚えてる？」「好き」「言った」などの合図を出した場合、または返信が長期的な好み、以前の約束、共有した経験、人物の長期的な情報に依存する場合は、より積極的に検索を検討できます。",
                "zh-CN": "- query_memory()：当对方提到“之前”“上次”“最近”“还记得吗”“我喜欢”“我说过”等信号，或回复依赖长期偏好、先前承诺、共同经历、人物长期信息时，可以更积极检索。",
            }
        )

    def select_retro_template(self, *, short_reply: bool, self_reply: bool, mai_replier: bool) -> str:
        return "retro_private_replyer_self" if self_reply else "retro_private_replyer"
