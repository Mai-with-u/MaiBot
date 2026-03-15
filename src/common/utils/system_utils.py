# TODO: 这个包装层后续可以删除，统一直接使用 src.chat.utils.utils.is_bot_self
# 注意：参数顺序已从旧版 (user_id, platform) 变更为 (platform, user_id)，与统一接口一致
def is_bot_self(platform: str, user_id: str) -> bool:
    """
    判断用户 ID 是否是机器人自己。

    当前仅保留兼容入口，真实实现委托给统一的多平台判断函数。
    """
    from src.chat.utils.utils import is_bot_self as _is_bot_self

    return _is_bot_self(platform, user_id)
