"""
高效文本匹配工具模块

实现 Aho-Corasick 算法用于多模式匹配。
"""

from typing import List, Dict, Tuple, Set, Any
from collections import deque


class AhoCorasick:
    """
    Aho-Corasick 自动机实现高效多模式匹配
    """

    def __init__(self):
        # next_states[state][char] = next_state
        self.next_states: List[Dict[str, int]] = [{}]
        # fail[state] = fail_state
        self.fail: List[int] = [0]
        # output[state] = set of patterns ending at this state
        self.output: List[Set[str]] = [set()]
        self.patterns: Set[str] = set()

    def add_pattern(self, pattern: str):
        """添加模式"""
        if not pattern:
            return
        self.patterns.add(pattern)
        state = 0
        for char in pattern:
            if char not in self.next_states[state]:
                new_state = len(self.next_states)
                self.next_states[state][char] = new_state
                self.next_states.append({})
                self.fail.append(0)
                self.output.append(set())
            state = self.next_states[state][char]
        self.output[state].add(pattern)

    def build(self):
        """构建失败指针"""
        queue = deque()
        # 处理第一层
        for char, state in self.next_states[0].items():
            queue.append(state)
            self.fail[state] = 0

        while queue:
            r = queue.popleft()
            for char, s in self.next_states[r].items():
                queue.append(s)
                # 找到失败路径
                state = self.fail[r]
                while char not in self.next_states[state] and state != 0:
                    state = self.fail[state]
                self.fail[s] = self.next_states[state].get(char, 0)
                # 合并输出
                self.output[s].update(self.output[self.fail[s]])

    def search(self, text: str) -> List[Tuple[int, str]]:
        """
        在文本中搜索所有模式
        
        Returns:
            [(结束索引, 匹配到的模式), ...]
        """
        state = 0
        results = []
        for i, char in enumerate(text):
            while char not in self.next_states[state] and state != 0:
                state = self.fail[state]
            state = self.next_states[state].get(char, 0)
            for pattern in self.output[state]:
                results.append((i, pattern))
        return results

    def find_all(self, text: str) -> Dict[str, int]:
        """
        查找并统计所有模式出现次数
        
        Returns:
            {模式: 出现次数}
        """
        results = self.search(text)
        stats = {}
        for _, pattern in results:
            stats[pattern] = stats.get(pattern, 0) + 1
        return stats
