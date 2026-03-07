"""发言频率控制插件 — 新 SDK 版本

通过 /chat 命令设置和查看聊天频率。
"""

from maibot_sdk import MaiBotPlugin, Command


class BetterFrequencyPlugin(MaiBotPlugin):
    """聊天频率控制插件"""

    @Command(
        "set_talk_frequency",
        description="设置当前聊天的talk_frequency值：/chat talk_frequency <数字> 或 /chat t <数字>",
        pattern=r"^/chat\s+(?:talk_frequency|t)\s+(?P<value>[+-]?\d*\.?\d+)$",
    )
    async def handle_set_talk_frequency(
        self, stream_id: str = "", matched_groups: dict | None = None, **kwargs
    ):
        """设置当前聊天的 talk_frequency"""
        if not matched_groups or "value" not in matched_groups:
            return False, "命令格式错误", False

        value_str = matched_groups["value"]
        if not value_str:
            return False, "无法获取数值参数", False

        try:
            value = float(value_str)
        except ValueError:
            await self.ctx.send.text("数值格式错误，请输入有效的数字", stream_id)
            return False, "数值格式错误", False

        if not stream_id:
            return False, "无法获取聊天流信息", False

        # 设置 talk_frequency
        await self.ctx.frequency.set_adjust(stream_id, value)

        # 获取当前状态
        current = await self.ctx.frequency.get_current_talk_value(stream_id)
        current_val = current if isinstance(current, (int, float)) else 0
        adjust = await self.ctx.frequency.get_adjust(stream_id)
        adjust_val = adjust if isinstance(adjust, (int, float)) else 1
        base_val = current_val / adjust_val if adjust_val else 0

        msg = (
            f"已设置当前聊天的talk_frequency调整值为: {value}\n"
            f"当前talk_value: {current_val:.2f}\n"
            f"发言频率调整: {adjust_val:.2f}\n"
            f"基础值: {base_val:.2f}"
        )
        await self.ctx.send.text(msg, stream_id)
        return True, None, False

    @Command(
        "show_frequency",
        description="显示当前聊天的频率控制状态：/chat show 或 /chat s",
        pattern=r"^/chat\s+(?:show|s)$",
    )
    async def handle_show_frequency(self, stream_id: str = "", **kwargs):
        """显示当前频率控制状态"""
        if not stream_id:
            return False, "无法获取聊天流信息", False

        current = await self.ctx.frequency.get_current_talk_value(stream_id)
        current_val = current if isinstance(current, (int, float)) else 0
        adjust = await self.ctx.frequency.get_adjust(stream_id)
        adjust_val = adjust if isinstance(adjust, (int, float)) else 1
        base_val = current_val / adjust_val if adjust_val else 0

        status_msg = (
            "当前聊天频率控制状态\n"
            "Talk Value (发言频率):\n\n"
            f"   • 基础值: {base_val:.2f}\n"
            f"   • 发言频率调整: {adjust_val:.2f}\n"
            f"   • 当前值: {current_val:.2f}\n\n"
            "使用命令:\n"
            "   • /chat talk_frequency <数字> 或 /chat t <数字> - 设置发言频率调整\n"
            "   • /chat show 或 /chat s - 显示当前状态"
        )
        await self.ctx.send.text(status_msg, stream_id)
        return True, None, False


def create_plugin():
    return BetterFrequencyPlugin()
