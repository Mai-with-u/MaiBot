"""Emoji 插件 — 新 SDK 版本

根据聊天上下文的情感，使用 LLM 选择并发送合适的表情包。
"""

from maibot_sdk import Action, MaiBotPlugin
from maibot_sdk.types import ActivationType

import random


class EmojiPlugin(MaiBotPlugin):
    """表情包插件"""

    @Action(
        "emoji",
        description="发送表情包辅助表达情绪",
        activation_type=ActivationType.RANDOM,
        activation_probability=0.3,
        parallel_action=True,
        action_require=[
            "发送表情包辅助表达情绪",
            "表达情绪时可以选择使用",
            "不要连续发送，如果你已经发过[表情包]，就不要选择此动作",
        ],
        associated_types=["emoji"],
    )
    async def handle_emoji(self, stream_id: str = "", reasoning: str = "", chat_id: str = "", **kwargs):
        """执行表情动作"""
        reason = reasoning or "表达当前情绪"

        # 1. 随机获取30个表情包
        sampled_emojis = await self.ctx.emoji.get_random(30)
        if not sampled_emojis:
            return False, "无法获取随机表情包"

        # 2. 按情感分组
        emotion_map: dict[str, list] = {}
        for emoji in sampled_emojis:
            emo = emoji.get("emotion", "")
            if emo not in emotion_map:
                emotion_map[emo] = []
            emotion_map[emo].append(emoji)

        available_emotions = list(emotion_map.keys())

        if not available_emotions:
            # 无情感标签，随机发送
            chosen = random.choice(sampled_emojis)
            await self.ctx.send.emoji(chosen["base64"], stream_id)
            return True, "随机发送了表情包"

        # 3. 获取最近消息作为上下文
        messages_text = ""
        if chat_id:
            recent_messages = await self.ctx.message.get_recent(chat_id=chat_id, limit=5)
            if recent_messages:
                messages_text = await self.ctx.message.build_readable(
                    recent_messages,
                    timestamp_mode="normal_no_YMD",
                    truncate=False,
                )

        # 4. 构建 prompt 让 LLM 选择情感
        available_emotions_str = "\n".join(available_emotions)
        prompt = f"""你正在进行QQ聊天，你需要根据聊天记录，选出一个合适的情感标签。
请你根据以下原因和聊天记录进行选择
原因：{reason}
聊天记录：
{messages_text}

这里是可用的情感标签：
{available_emotions_str}
请直接返回最匹配的那个情感标签，不要进行任何解释或添加其他多余的文字。
"""

        # 5. 调用 LLM
        llm_result = await self.ctx.llm.generate(prompt=prompt, model_name="utils")
        if not llm_result or not llm_result.get("success"):
            chosen = random.choice(sampled_emojis)
            await self.ctx.send.emoji(chosen["base64"], stream_id)
            return True, "LLM调用失败，随机发送了表情包"

        chosen_emotion = llm_result.get("response", "").strip().replace('"', "").replace("'", "")

        # 6. 根据选择的情感匹配表情包
        if chosen_emotion in emotion_map:
            chosen = random.choice(emotion_map[chosen_emotion])
        else:
            chosen = random.choice(sampled_emojis)

        # 7. 发送
        send_ok = await self.ctx.send.emoji(chosen["base64"], stream_id)
        if send_ok:
            return True, f"成功发送表情包:[表情包：{chosen_emotion}]"
        return False, "发送表情包失败"

    async def on_load(self) -> None:
        """处理插件加载。"""

        # 从插件配置读取 emoji_chance 来覆盖默认概率
        await self.ctx.config.get("emoji.emoji_chance")

    async def on_unload(self) -> None:
        """处理插件卸载。"""

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """处理配置热重载事件。

        Args:
            scope: 配置变更范围。
            config_data: 最新配置数据。
            version: 配置版本号。
        """

        del config_data
        del version
        if scope == "self":
            await self.ctx.config.get("emoji.emoji_chance")


def create_plugin() -> EmojiPlugin:
    """创建 Emoji 插件实例。

    Returns:
        EmojiPlugin: 新的 Emoji 插件实例。
    """

    return EmojiPlugin()
