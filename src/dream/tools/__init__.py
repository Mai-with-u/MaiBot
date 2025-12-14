"""
dream agent 工具实现模块。

每个工具的具体实现放在独立文件中，通过 make_xxx(chat_id) 工厂函数
生成绑定到特定 chat_id 的协程函数，由 dream_agent.init_dream_tools 统一注册。
"""
