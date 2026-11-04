from unittest.mock import AsyncMock
import asyncio

from src.desktop_pet import thinking_caption


def test_stage_change_discards_late_result_and_idle_clears():
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def summarize(data):
            if data["stage"] == "Planner":
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    # 模拟上游未能取消请求，仍然返回过期结果。
                    return "旧摘要"
            return "正在查找资料"
        send = AsyncMock()
        caption = thinking_caption.ThinkingCaption(send, summarize)
        caption.update({"session_id": "owner", "stage": "Planner"})
        await entered.wait()
        old = caption._task
        caption.update({"session_id": "owner", "stage": "工具执行"})
        await caption._task
        await old
        assert [c.args[0]["text"] for c in send.call_args_list] == [thinking_caption.THINKING_PLACEHOLDER, thinking_caption.THINKING_PLACEHOLDER, "正在查找资料"]
        count = send.call_count
        caption.update({"session_id": "owner", "stage": "工具执行"})
        assert send.call_count == count
        caption.update({"session_id": "owner", "stage": "空闲"})
        await caption._task
        assert send.call_args.args[0]["text"] == ""
    asyncio.run(run())


def test_cancel_invalidates_inflight_result():
    async def run():
        entered = asyncio.Event()
        async def summarize(data):
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return "已过期"
        send = AsyncMock()
        caption = thinking_caption.ThinkingCaption(send, summarize)
        caption.update({"session_id": "owner", "stage": "Planner"})
        await entered.wait()
        task = caption._task
        caption.cancel()
        await task
        assert send.call_count == 1
    asyncio.run(run())
