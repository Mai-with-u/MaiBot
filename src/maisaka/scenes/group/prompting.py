"""group 场景提示词及回复模板选择。"""

from src.config.config import global_config
from src.config.official_configs import AttentionDriftConfig
from src.maisaka.scenes.prompting import ChatPromptProfile, localized_text


class GroupPromptProfile(ChatPromptProfile):
    def attention_text(self) -> str:
        return global_config.chat.reply_style.group_chat_prompt

    def attention_drift_config(self) -> AttentionDriftConfig:
        return global_config.experimental.group_attention_drift

    def query_memory_rule(self) -> str:
        return localized_text(
            {
                "en-US": "- query_memory(): Use it only when the reply clearly depends on past group conversation, shared experiences, public agreements, task progress, or recent clues. Do not retrieve memory for greetings, immediate emotional responses, light banter, or content that can be answered from recent messages alone. Do not bring private-chat or personal-privacy memories into a group chat.",
                "ja-JP": "- query_memory()：返信がグループ内の過去会話、共有した経験、公開された約束、タスクの進捗、最近の手がかりに明確に依存する場合だけ使ってください。挨拶、その場の感情への反応、軽いやり取り、最近のメッセージだけで答えられる内容では検索しないでください。個人チャットや私的な記憶をグループチャットに持ち込まないでください。",
                "zh-CN": "- query_memory()：只有回复明显依赖群内过去对话、共同经历、公开约定、任务进展或近期线索时使用；不要为了寒暄、即时情绪回应、轻松接话、只看最近消息就能回答的内容而检索。不要把私聊或个人隐私记忆带到群聊里。",
            }
        )

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

    def select_retro_template(self, *, short_reply: bool, self_reply: bool, mai_replier: bool) -> str:
        # Mai Replier 对齐训练分布：训练数据 100% 使用标准模板（retro_replyer），
        # light 模板（口语化/尽量简短/不要太有条理）属训练外成分。Mai Replier 群聊恒用标准模板。
        return "retro_replyer_light" if short_reply and not mai_replier else "retro_replyer"
