"""将 Platform IO 入站封装分发到主消息链。"""

from src.platform_io.types import InboundMessageEnvelope


async def dispatch_core_inbound(envelope: InboundMessageEnvelope) -> None:
    """把已通过 Broker 审核的入站消息送入 ``chat_bot.receive_message``。

    Args:
        envelope: Platform IO 产出的入站封装。

    Raises:
        ValueError: 当封装中没有可用的 ``SessionMessage`` 时抛出。
    """

    session_message = envelope.session_message
    if session_message is None:
        raise ValueError("Platform IO 入站封装缺少可用的 SessionMessage")

    from src.chat.message_receive.bot import chat_bot

    await chat_bot._ensure_started()
    await chat_bot.receive_message(session_message)
