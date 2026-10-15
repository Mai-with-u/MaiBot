"""Hello World 示例插件 — 新 SDK 版本

你的第一个 MaiCore 插件，包含问候功能、时间查询等基础示例。
"""

from datetime import datetime
from typing import Any, Dict, List

import random
import re

from maibot_sdk import API, Action, Command, EventHandler, Field, HomeCard, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ActivationType, EventType, ToolParameterInfo, ToolParamType


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=False, description="是否启用插件")
    config_version: str = Field(default="2.0.0", description="配置版本")


class GreetingConfig(PluginConfigBase):
    """问候配置。"""

    __ui_label__ = "问候"
    __ui_icon__ = "message-circle"
    __ui_order__ = 1

    message: str = Field(default="嗨！很开心见到你！😊", description="默认问候消息")


class TimeConfig(PluginConfigBase):
    """时间查询配置。"""

    __ui_label__ = "时间"
    __ui_icon__ = "clock"
    __ui_order__ = 2

    format: str = Field(default="%Y-%m-%d %H:%M:%S", description="时间显示格式")


class PrintMessageConfig(PluginConfigBase):
    """消息打印配置。"""

    __ui_label__ = "消息打印"
    __ui_icon__ = "terminal"
    __ui_order__ = 3

    enabled: bool = Field(default=False, description="是否打印接收到的消息")


class HelloWorldPluginConfig(PluginConfigBase):
    """Hello World 示例插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    greeting: GreetingConfig = Field(default_factory=GreetingConfig)
    time: TimeConfig = Field(default_factory=TimeConfig)
    print_message: PrintMessageConfig = Field(default_factory=PrintMessageConfig)


class HelloWorldPlugin(MaiBotPlugin):
    """Hello World 示例插件"""

    config_model = HelloWorldPluginConfig

    def __init__(self) -> None:
        super().__init__()
        # 页面演示只维护插件内存状态，不发送聊天消息，也不修改配置文件。
        self._webui_greetings: List[Dict[str, Any]] = []
        self._webui_greeting_count: int = 0
        self._webui_last_greeting: str = "尚未生成问候，请在侧边栏的问候体验页试试。"

    async def on_load(self) -> None:
        """处理插件加载。"""

    async def on_unload(self) -> None:
        """处理插件卸载。"""

    # ===== WebUI 页面 API =====

    @API("webui_summary", description="读取自定义页面的问候摘要和最近记录", version="1")
    async def webui_summary(self) -> Dict[str, Any]:
        """为顶部概览和侧边体验页提供同一份只读数据快照。"""
        return {
            "greeting_count": self._webui_greeting_count,
            "record_count": len(self._webui_greetings),
            "current_time": datetime.now().strftime(self.config.time.format),
            "default_greeting": self.config.greeting.message,
            "last_greeting": self._webui_last_greeting,
            "history": [dict(record) for record in reversed(self._webui_greetings)],
            "chart": [
                {"label": str(record["sequence"]), "characters": len(record["message"])}
                for record in self._webui_greetings
            ],
        }

    @API("webui_generate_greeting", description="生成问候预览，不向聊天流发送消息", version="1")
    async def webui_generate_greeting(
        self, name: str, style: str = "friendly", repeat: int = 1, include_time: bool = True
    ) -> Dict[str, Any]:
        """演示表单参数绑定和写操作完成后的查询刷新。"""
        recipient = name.strip()
        if not recipient or len(name) > 80:
            raise ValueError("称呼不能为空，且不能超过 80 个字符")
        if style not in {"friendly", "formal"}:
            raise ValueError("不支持的问候风格")
        if type(repeat) is not int or not 1 <= repeat <= 5:
            raise ValueError("重复次数必须为 1 到 5 的整数")
        if type(include_time) is not bool:
            raise ValueError("显示时间必须为布尔值")

        greeting = (
            f"{recipient}，{self.config.greeting.message}"
            if style == "friendly"
            else f"您好，{recipient}。很高兴与您见面。"
        )
        created_at = datetime.now().strftime(self.config.time.format)
        message = "\n".join([greeting] * repeat)
        if include_time:
            message = f"{message}\n时间：{created_at}"

        self._webui_greeting_count += 1
        self._webui_last_greeting = message
        self._webui_greetings.append(
            {"sequence": self._webui_greeting_count, "name": recipient, "message": message, "created_at": created_at}
        )
        # 限制演示历史的长度，顶部表格和图表展示实际生成的最近 20 条记录。
        self._webui_greetings = self._webui_greetings[-20:]
        return {"message": message, "greeting_count": self._webui_greeting_count}

    @API("webui_reset_greetings", description="清空内存中的 WebUI 问候演示记录", version="1")
    async def webui_reset_greetings(self) -> Dict[str, Any]:
        """演示带宿主确认弹窗的危险操作，确认文案由 webui.json 声明。"""
        self._webui_greetings.clear()
        self._webui_greeting_count = 0
        self._webui_last_greeting = "演示记录已清空，可以重新生成问候。"
        return {"cleared": True}

    @API("webui_remove_greeting", description="按选中记录的序号删除一条页面演示记录", version="1")
    async def webui_remove_greeting(self, sequence: int) -> Dict[str, Any]:
        """演示行数据参数绑定；只修改页面内存，不发送消息。"""
        if type(sequence) is not int or sequence < 1:
            raise ValueError("记录序号必须为正整数")
        record = next((item for item in self._webui_greetings if item["sequence"] == sequence), None)
        if record is None:
            raise ValueError("记录不存在或已被删除，请刷新页面")
        self._webui_greetings.remove(record)
        self._webui_last_greeting = (
            self._webui_greetings[-1]["message"] if self._webui_greetings else "演示记录已清空，可以重新生成问候。"
        )
        return {"removed": sequence}

    # ===== HomeCard 组件 =====

    @HomeCard(
        "hello_world_feature_card",
        title="Hello World 功能入口",
        description="展示示例插件提供的命令、Action 和工具入口。",
        content=[
            {
                "type": "markdown",
                "content": "这是一个偏功能展示的首页卡片，用来告诉插件开发者如何把入口放到 WebUI 首页。",
            },
            {
                "type": "list",
                "items": [
                    "/test：发送测试消息",
                    "/time：按配置格式查询当前时间",
                    "/random_emojis：发送随机表情包",
                    "compare_numbers：供 LLM 调用的数字比较工具",
                    "WebUI：顶部概览与侧边问候体验页，演示声明式页面组件",
                ],
            },
            {
                "type": "actions",
                "actions": [
                    {"label": "打开插件配置", "url": "/plugin-config?plugin=maibot-team.hello-world-plugin"},
                    {"label": "查看插件市场", "url": "/plugins"},
                    {"label": "打开自定义概览", "url": "/extensions/maibot-team.hello-world-plugin/overview"},
                ],
            },
        ],
        link_url="/plugin-config?plugin=maibot-team.hello-world-plugin",
        link_label="配置示例插件",
        width="large",
        order=120,
    )
    async def home_feature_card(self) -> None:
        """声明偏功能展示的 WebUI 首页卡片。"""

        return None

    @HomeCard(
        "hello_world_data_card",
        title="Hello World 示例数据",
        description="用静态示例数据演示首页数据型卡片的结构化写法。",
        content=[
            {"type": "stat", "label": "已声明组件", "value": "8+", "detail": "Tool / Action / Command / EventHandler / HomeCard"},
            {
                "type": "key_value",
                "entries": {
                    "配置版本": "2.0.0",
                    "默认问候": "嗨！很开心见到你！",
                    "时间格式": "%Y-%m-%d %H:%M:%S",
                },
            },
            {
                "type": "markdown",
                "content": "数据型卡片适合放运行摘要、计数、状态快照；复杂详情建议跳转到独立页面。",
            },
        ],
        width="medium",
        order=130,
    )
    async def home_data_card(self) -> None:
        """声明偏数据展示的 WebUI 首页卡片。"""

        return None

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
        del kwargs

        base_message = self.config.greeting.message
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
        del kwargs

        message = f"再见！期待下次聊天！👋{bye_message}"
        await self.ctx.send.text(message, stream_id)
        return True, "发送了告别消息"

    # ===== Command 组件 =====

    @Command("time", description="查询当前时间", pattern=r"^/time$")
    async def handle_time(self, stream_id: str = "", **kwargs):
        """时间查询命令"""
        del kwargs

        time_format = self.config.time.format
        now = datetime.now()
        time_str = now.strftime(time_format)
        await self.ctx.send.text(f"⏰ 当前时间：{time_str}", stream_id)
        return True, f"显示了当前时间: {time_str}", True

    @Command("random_emojis", description="发送多张随机表情包", pattern=r"^/random_emojis$")
    async def handle_random_emojis(self, stream_id: str = "", **kwargs):
        """发送多张随机表情包"""
        del kwargs

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
        del kwargs

        await self.ctx.send.text("测试正常！Bot 功能运行中 ✅", stream_id)
        return True, "测试完成", True

    @Command(
        "send_to",
        description="向指定聊天流发送文本",
        pattern=r"^/send_to\s+(?P<target_stream_id>\S+)\s+(?P<text>.+)$",
    )
    async def handle_send_to(self, stream_id: str = "", **kwargs: Any):
        """向指定聊天流发送文本。"""

        matched_groups = kwargs.get("matched_groups")
        if not isinstance(matched_groups, dict):
            matched_groups = {}

        target_stream_id = str(matched_groups.get("target_stream_id") or "").strip()
        text = str(matched_groups.get("text") or "").strip()
        if not target_stream_id or not text:
            raw_text = str(kwargs.get("text") or "").strip()
            match = re.match(r"^/send_to\s+(?P<target_stream_id>\S+)\s+(?P<text>.+)$", raw_text, re.DOTALL)
            if match is not None:
                target_stream_id = match.group("target_stream_id").strip()
                text = match.group("text").strip()

        if not target_stream_id:
            return False, "用法：/send_to <stream_id> <要发送的文本>", True

        if not text:
            return False, "要发送的文本不能为空", True

        sent = await self.ctx.send.text(text, target_stream_id)
        if not sent:
            return False, f"发送失败：聊天流不存在或不可发送，stream_id={target_stream_id}", True

        if stream_id and stream_id != target_stream_id:
            await self.ctx.send.text(f"已向 {target_stream_id} 发送文本", stream_id)
        return True, f"已向 {target_stream_id} 发送文本", True

    # ===== EventHandler 组件 =====

    @EventHandler("print_message_handler", description="打印接收到的消息", event_type=EventType.ON_MESSAGE)
    async def handle_print_message(self, message: Any = None, **kwargs: Any):
        """打印消息事件"""
        del kwargs

        if self.config.print_message.enabled and message:
            raw = message.get("raw_message", "") if isinstance(message, dict) else str(message)
            print(f"接收到消息: {raw}")
        return True, True, "消息已打印", None, None

    @EventHandler(
        "forward_messages_handler", description="把接收到的消息转发到指定聊天ID", event_type=EventType.ON_MESSAGE
    )
    async def handle_forward_messages(self, message: Any = None, stream_id: str = "", **kwargs: Any):
        """收集消息并定期转发"""
        del kwargs

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
