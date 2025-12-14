from typing import Optional


class ConversationInfo:
    def __init__(self):
        self.done_action = []
        self.goal_list = []
        self.knowledge_list = []
        self.memory_list = []
        self.last_successful_reply_action: Optional[str] = None
