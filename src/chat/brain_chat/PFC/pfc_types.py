from enum import Enum
from typing import Literal


class ConversationState(Enum):
    """对话状态"""

    INIT = "初始化"
    RETHINKING = "重新思考"
    ANALYZING = "分析历史"
    PLANNING = "规划目标"
    GENERATING = "生成回复"
    CHECKING = "检查回复"
    SENDING = "发送消息"
    FETCHING = "获取知识"
    WAITING = "等待"
    LISTENING = "倾听"
    ENDED = "结束"
    JUDGING = "判断"
    IGNORED = "屏蔽"


ActionType = Literal["direct_reply", "fetch_knowledge", "wait"]
