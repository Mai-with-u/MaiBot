"""Hello World 示例插件 — 新 SDK 版本

你的第一个 MaiCore 插件，包含问候功能、时间查询等基础示例。
"""

from maibot_sdk import Action, Command, EventHandler, MaiBotPlugin, Tool
from maibot_sdk.types import ActivationType, EventType, ToolParameterInfo, ToolParamType

import datetime
import random


class HelloWorldPlugin(MaiBotPlugin):
    """Hello World 示例插件"""

    async def on_load(self) -> None:
        """处理插件加载。"""

    async def on_unload(self) -> None:
        """处理插件卸载。"""

    # ===== Tool 组件 =====

    @Tool(
        "compare_numbers",
        description="使用工具比较两个数的大小，返回较大的数",
        parameters=[
            ToolParameterInfo(name="num1", param_type=ToolParamType.FLOAT, description="第一个数字", required=True),
            ToolParameterInfo(name="num2", param_type=ToolParamType.FLOAT, description="第二个数字", required=True),
        ],
    )
    async def handle_compare_numbers(self, num1: float = 0, num2: float = 0, **kwargs):
        """比较两个数的大小"""
        try:
            if num1 > num2:
                result = f"{num1} 大于 {num2}"
            elif num1 < num2:
                result = f"{num1} 小于 {num2}"
            else:
                result = f"{num1} 等于 {num2}"
            return {"name": "compare_numbers", "content": result}
        except Exception as e:
            return {"name": "compare_numbers", "content": f"比较数字失败，炸了: {e}"}

    # ===== Action 组件 =====

    @Action(
        "hello_greeting",
        description="向用户发送问候消息",
        activation_type=ActivationType.ALWAYS,
        action_parameters={"greeting_message": "要发送的问候消息"},
        action_require=["需要发送友好问候时使用", "当有人向你问好时使用", "当你遇见没有见过的人时使用"],
        associated_types=["text"],
    )
    async def handle_hello(self, stream_id: str = "", greeting_message: str = "", **kwargs):
        """问候动作"""
        config_result = await self.ctx.config.get("greeting.message")
        base_message = config_result if isinstance(config_result, str) else "嗨！很开心见到你！😊"
        message = base_message + greeting_message
        await self.ctx.send.text(message, stream_id)
        return True, "发送了问候消息"

    @Action(
        "bye_greeting",
        description="向用户发送告别消息",
        activation_type=ActivationType.KEYWORD,
        activation_keywords=["再见", "bye", "88", "拜拜"],
        action_parameters={"bye_message": "要发送的告别消息"},
        action_require=["用户要告别时使用", "当有人要离开时使用", "当有人和你说再见时使用"],
        associated_types=["text"],
    )
    async def handle_bye(self, stream_id: str = "", bye_message: str = "", **kwargs):
        """告别动作"""
        message = f"再见！期待下次聊天！👋{bye_message}"
        await self.ctx.send.text(message, stream_id)
        return True, "发送了告别消息"

    # ===== Command 组件 =====

    @Command("time", description="查询当前时间", pattern=r"^/time$")
    async def handle_time(self, stream_id: str = "", **kwargs):
        """时间查询命令"""
        config_result = await self.ctx.config.get("time.format")
        time_format = config_result if isinstance(config_result, str) else "%Y-%m-%d %H:%M:%S"
        now = datetime.datetime.now()
        time_str = now.strftime(time_format)
        await self.ctx.send.text(f"⏰ 当前时间：{time_str}", stream_id)
        return True, f"显示了当前时间: {time_str}", True

    @Command("random_emojis", description="发送多张随机表情包", pattern=r"^/random_emojis$")
    async def handle_random_emojis(self, stream_id: str = "", **kwargs):
        """发送多张随机表情包"""
        emojis = await self.ctx.emoji.get_random(5)
        if not emojis:
            return False, "未找到表情包", False
        # 用转发消息发送多张图片
        messages = [
            {"user_id": "0", "nickname": "神秘用户", "segments": [{"type": "image", "content": e.get("base64", "")}]}
            for e in emojis
        ]
        await self.ctx.send.forward(messages, stream_id)
        return True, "已发送随机表情包", True

    @Command("test", description="测试命令", pattern=r"^/test$")
    async def handle_test(self, stream_id: str = "", **kwargs):
        """测试命令 — 发送简单测试消息"""
        await self.ctx.send.text("测试正常！Bot 功能运行中 ✅", stream_id)
        return True, "测试完成", True

    # ===== EventHandler 组件 =====

    @EventHandler("print_message_handler", description="打印接收到的消息", event_type=EventType.ON_MESSAGE)
    async def handle_print_message(self, message=None, **kwargs):
        """打印消息事件"""
        config_result = await self.ctx.config.get("print_message.enabled")
        enabled = config_result if isinstance(config_result, bool) else False
        if enabled and message:
            raw = message.get("raw_message", "") if isinstance(message, dict) else str(message)
            print(f"接收到消息: {raw}")
        return True, True, "消息已打印", None, None

    @EventHandler(
        "forward_messages_handler", description="把接收到的消息转发到指定聊天ID", event_type=EventType.ON_MESSAGE
    )
    async def handle_forward_messages(self, message=None, stream_id: str = "", **kwargs):
        """收集消息并定期转发"""
        if not message:
            return True, True, None, None, None
        plain_text = message.get("plain_text", "") if isinstance(message, dict) else ""
        if not plain_text:
            return True, True, None, None, None

        # 使用插件级状态收集消息
        if not hasattr(self, "_fwd_messages"):
            self._fwd_messages: list[str] = []
            self._fwd_counter: int = 0

        self._fwd_messages.append(plain_text)
        self._fwd_counter += 1

        if self._fwd_counter % 10 == 0 and stream_id:
            if random.random() < 0.01:
                segments = [{"type": "text", "content": msg} for msg in self._fwd_messages]
                await self.ctx.send.hybrid(segments, stream_id)
            else:
                messages = [
                    {"user_id": "0", "nickname": "转发", "segments": [{"type": "text", "content": msg}]}
                    for msg in self._fwd_messages
                ]
                await self.ctx.send.forward(messages, stream_id)
            self._fwd_messages = []

        return True, True, None, None, None

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """处理配置热重载事件。

        Args:
            scope: 配置变更范围。
            config_data: 最新配置数据。
            version: 配置版本号。
        """

        del scope
        del config_data
        del version


def create_plugin() -> HelloWorldPlugin:
    """创建 Hello World 示例插件实例。

    Returns:
        HelloWorldPlugin: 新的示例插件实例。
    """

    return HelloWorldPlugin()
