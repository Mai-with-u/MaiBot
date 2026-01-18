from typing import Optional


class ConversationInfo:
    def __init__(self):
        self.done_action: list = []
        self.goal_list: list = []
        self.knowledge_list: list = []
        self.memory_list: list = []
        self.last_successful_reply_action: Optional[str] = None
