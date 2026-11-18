"""跨业务层的受控流程中止。"""


class OperationAbortedError(Exception):
    """调用方主动取消操作，不应被当作执行故障重试或惩罚模型。"""
