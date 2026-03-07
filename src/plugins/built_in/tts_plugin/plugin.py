"""TTS 插件 — 新 SDK 版本

将文本转换为语音进行播放。
"""

import re

from maibot_sdk import MaiBotPlugin, Action
from maibot_sdk.types import ActivationType


class TTSPlugin(MaiBotPlugin):
    """文本转语音插件"""

    @Action(
        "tts_action",
        description="将文本转换为语音进行播放，适用于需要语音输出的场景",
        activation_type=ActivationType.KEYWORD,
        activation_keywords=["语音", "tts", "播报", "读出来", "语音播放", "听", "朗读"],
        parallel_action=False,
        action_parameters={"voice_text": "你想用语音表达的内容，这段内容将会以语音形式发出"},
        action_require=[
            "当需要发送语音信息时使用",
            "当用户明确要求使用语音功能时使用",
            "当表达内容更适合用语音而不是文字传达时使用",
            "当用户想听到语音回答而非阅读文本时使用",
        ],
        associated_types=["tts_text"],
    )
    async def handle_tts_action(self, stream_id: str = "", action_data: dict = None, reasoning: str = "", **kwargs):
        """处理 TTS 文本转语音动作"""
        action_data = action_data or {}
        text = action_data.get("voice_text", "")
        if not text:
            return False, "执行TTS动作失败：未提供文本内容"

        # 文本预处理
        processed_text = re.sub(r"([!?,.;:。！？，、；：])\1+", r"\1", text)
        if not any(processed_text.endswith(end) for end in [".", "?", "!", "。", "！", "？"]):
            processed_text = f"{processed_text}。"

        # 发送自定义 tts 消息
        result = await self.ctx.call_capability(
            "send.custom",
            message_type="tts_text",
            content=processed_text,
            stream_id=stream_id,
        )
        if result and result.get("success"):
            return True, "TTS动作执行成功"
        return False, f"TTS动作执行失败: {result}"


def create_plugin():
    return TTSPlugin()
