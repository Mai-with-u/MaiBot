"""表情包管理插件 — 新 SDK 版本

通过 /emoji 命令管理表情包的添加、列表和删除。
"""

import base64
import datetime
import hashlib
import re

from maibot_sdk import MaiBotPlugin, Command


class EmojiManagePlugin(MaiBotPlugin):
    """表情包管理插件"""

    # ===== 工具方法 =====

    @staticmethod
    def _extract_emoji_base64(segments) -> list[str]:
        """从消息 segments 中提取 emoji/image 的 base64 数据。

        segments 可以是 dict 列表或 Seg 对象列表（兼容两种格式）。
        """
        results: list[str] = []
        if not segments:
            return results

        if isinstance(segments, dict):
            seg_type = segments.get("type", "")
            if seg_type in ("emoji", "image"):
                data = segments.get("data", "")
                if data:
                    results.append(data)
            elif seg_type == "seglist":
                for child in segments.get("data", []):
                    results.extend(EmojiManagePlugin._extract_emoji_base64(child))
            return results

        # 如果有 .type 属性（Seg 对象）
        if hasattr(segments, "type"):
            seg_type = getattr(segments, "type", "")
            if seg_type in ("emoji", "image"):
                results.append(getattr(segments, "data", ""))
            elif seg_type == "seglist":
                for child in getattr(segments, "data", []):
                    results.extend(EmojiManagePlugin._extract_emoji_base64(child))
            return results

        # 列表
        for seg in segments:
            results.extend(EmojiManagePlugin._extract_emoji_base64(seg))
        return results

    # ===== Command 组件 =====

    @Command("add_emoji", description="添加表情包", pattern=r".*/emoji add.*")
    async def handle_add_emoji(self, stream_id: str = "", message_segments=None, **kwargs):
        """添加表情包"""
        emoji_base64_list = self._extract_emoji_base64(message_segments)
        if not emoji_base64_list:
            await self.ctx.send.text("未在消息中找到表情包或图片", stream_id)
            return False, "未在消息中找到表情包或图片", False

        success_count = 0
        fail_count = 0
        results = []

        for i, emoji_b64 in enumerate(emoji_base64_list):
            result = await self.ctx.emoji.register_emoji(emoji_b64)
            if isinstance(result, dict) and result.get("success"):
                success_count += 1
                desc = result.get("description", "未知描述")
                emotions = result.get("emotions", [])
                replaced = result.get("replaced", False)
                msg = f"表情包 {i + 1} 注册成功{'(替换旧表情包)' if replaced else '(新增表情包)'}"
                if desc:
                    msg += f"\n描述: {desc}"
                if emotions:
                    msg += f"\n情感标签: {', '.join(emotions)}"
                results.append(msg)
            else:
                fail_count += 1
                err = result.get("message", "注册失败") if isinstance(result, dict) else "注册失败"
                results.append(f"表情包 {i + 1} 注册失败: {err}")

        total = success_count + fail_count
        summary = f"表情包注册完成: 成功 {success_count} 个，失败 {fail_count} 个，共处理 {total} 个"
        if results:
            summary += "\n" + "\n".join(results)

        await self.ctx.send.text(summary, stream_id)
        return success_count > 0, summary, success_count > 0

    @Command("emoji_list", description="列表表情包", pattern=r"^/emoji list(\s+\d+)?$")
    async def handle_list_emoji(self, stream_id: str = "", raw_message: str = "", **kwargs):
        """列出表情包"""
        max_count = 10
        match = re.match(r"^/emoji list(?:\s+(\d+))?$", raw_message)
        if match and match.group(1):
            max_count = min(int(match.group(1)), 50)

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        count_result = await self.ctx.emoji.get_count()
        emoji_count = count_result if isinstance(count_result, int) else 0

        info_result = await self.ctx.emoji.get_info()
        max_emoji = info_result.get("max_count", 0) if isinstance(info_result, dict) else 0
        available = info_result.get("available_emojis", 0) if isinstance(info_result, dict) else 0

        lines = [
            f"📊 表情包统计信息 ({now})",
            f"• 总数: {emoji_count} / {max_emoji}",
            f"• 可用: {available}",
        ]

        if emoji_count == 0:
            lines.append("\n❌ 暂无表情包")
            await self.ctx.send.text("\n".join(lines), stream_id)
            return True, "\n".join(lines), True

        all_result = await self.ctx.emoji.get_all()
        all_emojis = all_result if isinstance(all_result, list) else []
        if not all_emojis:
            lines.append("\n❌ 无法获取表情包列表")
            await self.ctx.send.text("\n".join(lines), stream_id)
            return False, "\n".join(lines), True

        display = all_emojis[:max_count]
        lines.append(f"\n📋 显示前 {len(display)} 个表情包:")
        for i, emoji in enumerate(display, 1):
            if isinstance(emoji, (list, tuple)) and len(emoji) >= 3:
                _, desc, emotion = emoji[0], emoji[1], emoji[2]
            elif isinstance(emoji, dict):
                desc = emoji.get("description", "")
                emotion = emoji.get("emotion", "")
            else:
                desc, emotion = str(emoji), ""
            short_desc = desc[:50] + "..." if len(desc) > 50 else desc
            lines.append(f"{i}. {short_desc} [{emotion}]")

        if len(all_emojis) > max_count:
            lines.append(f"\n💡 还有 {len(all_emojis) - max_count} 个表情包未显示")

        final = "\n".join(lines)
        await self.ctx.send.text(final, stream_id)
        return True, final, True

    @Command("delete_emoji", description="删除表情包", pattern=r".*/emoji delete.*")
    async def handle_delete_emoji(self, stream_id: str = "", message_segments=None, **kwargs):
        """删除表情包"""
        emoji_base64_list = self._extract_emoji_base64(message_segments)
        if not emoji_base64_list:
            await self.ctx.send.text("未在消息中找到表情包或图片", stream_id)
            return False, "未找到表情包", False

        success_count = 0
        fail_count = 0
        results = []

        for i, emoji_b64 in enumerate(emoji_base64_list):
            # 计算哈希
            if isinstance(emoji_b64, str):
                clean = emoji_b64.encode("ascii", errors="ignore").decode("ascii")
            else:
                clean = str(emoji_b64)
            image_bytes = base64.b64decode(clean)
            emoji_hash = hashlib.md5(image_bytes).hexdigest()  # noqa: S324

            result = await self.ctx.emoji.delete_emoji(emoji_hash)
            if isinstance(result, dict) and result.get("success"):
                success_count += 1
                desc = result.get("description", "未知描述")
                emotions = result.get("emotions", [])
                before = result.get("count_before", 0)
                after = result.get("count_after", 0)
                msg = f"表情包 {i + 1} 删除成功"
                if desc:
                    msg += f"\n描述: {desc}"
                if emotions:
                    msg += f"\n情感标签: {', '.join(emotions)}"
                msg += f"\n表情包数量: {before} → {after}"
                results.append(msg)
            else:
                fail_count += 1
                err = result.get("message", "删除失败") if isinstance(result, dict) else "删除失败"
                results.append(f"表情包 {i + 1} 删除失败: {err}")

        total = success_count + fail_count
        summary = f"表情包删除完成: 成功 {success_count} 个，失败 {fail_count} 个，共处理 {total} 个"
        if results:
            summary += "\n" + "\n".join(results)

        await self.ctx.send.text(summary, stream_id)
        return success_count > 0, summary, success_count > 0

    @Command("random_emojis", description="发送多张随机表情包", pattern=r"^/random_emojis$")
    async def handle_random_emojis(self, stream_id: str = "", **kwargs):
        """发送多张随机表情包"""
        result = await self.ctx.emoji.get_random(5)
        if not result or not result.get("success"):
            return False, "未找到表情包", False
        emojis = result.get("emojis", [])
        if not emojis:
            return False, "未找到表情包", False
        messages = [
            {"user_id": "0", "nickname": "神秘用户", "segments": [{"type": "image", "content": e.get("base64", "")}]}
            for e in emojis
        ]
        await self.ctx.send.forward(messages, stream_id)
        return True, "已发送随机表情包", True


def create_plugin():
    return EmojiManagePlugin()
