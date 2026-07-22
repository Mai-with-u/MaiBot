"""
高效文本匹配工具模块

优先使用 ahocorasick-rs 原生实现，缺失时回退到纯 Python Aho-Corasick。
"""

from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from src.common.logger import get_logger

import os

try:
    import ahocorasick_rs  # type: ignore

    HAS_AHOCORASICK_RS = True
except ImportError:
    ahocorasick_rs = None
    HAS_AHOCORASICK_RS = False

logger = get_logger("A_Memorix.Matcher")


class AhoCorasick:
    """
    Aho-Corasick 自动机实现高效多模式匹配
    """

    def __init__(self, native_min_patterns: Optional[int] = None):
        # 状态转移表：next_states[state][char] = next_state
        self.next_states: List[Dict[str, int]] = [{}]
        # 失败指针：fail[state] = fail_state
        self.fail: List[int] = [0]
        # 输出集合：output[state] 保存在该状态结束的模式。
        self.output: List[Set[str]] = [set()]
        self.patterns: Set[str] = set()
        self._native_matcher: Optional[object] = None
        self._native_patterns: List[str] = []
        self._python_built = False
        self.native_min_patterns = self._resolve_native_min_patterns(native_min_patterns)

    @staticmethod
    def _resolve_native_min_patterns(native_min_patterns: Optional[int]) -> int:
        if native_min_patterns is not None:
            return max(1, int(native_min_patterns))
        raw_value = os.getenv("A_MEMORIX_NATIVE_MATCHER_MIN_PATTERNS", "").strip()
        if raw_value:
            try:
                return max(1, int(raw_value))
            except ValueError:
                logger.warning(f"忽略无效的原生匹配阈值: {raw_value}")
        return 3000

    def add_pattern(self, pattern: str):
        """添加模式"""
        if not pattern:
            return
        self._native_matcher = None
        self._python_built = False
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
        self._build_native_matcher()
        if self._native_matcher is not None:
            return
        self._build_python_matcher()

    def _build_python_matcher(self) -> None:
        if self._python_built:
            return
        queue = deque()
        # 处理第一层
        for _char, state in self.next_states[0].items():
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
        self._python_built = True

    def _build_native_matcher(self) -> None:
        if not HAS_AHOCORASICK_RS or ahocorasick_rs is None:
            return
        patterns = sorted(self.patterns)
        if not patterns or len(patterns) < self.native_min_patterns:
            return
        try:
            try:
                self._native_matcher = ahocorasick_rs.AhoCorasick(
                    patterns,
                    matchkind=ahocorasick_rs.MATCHKIND_STANDARD,
                    store_patterns=True,
                )
            except TypeError:
                self._native_matcher = ahocorasick_rs.AhoCorasick(
                    patterns,
                    match_kind=ahocorasick_rs.MATCHKIND_STANDARD,
                    store_patterns=True,
                )
            self._native_patterns = patterns
        except Exception:
            self._native_matcher = None
            self._native_patterns = []

    def search(self, text: str) -> List[Tuple[int, str]]:
        """
        在文本中搜索所有模式

        Returns:
            [(结束索引, 匹配到的模式), ...]
        """
        if self._native_matcher is not None:
            try:
                matches = self._native_matcher.find_matches_as_indexes(text)  # type: ignore[attr-defined]
                return [
                    (int(end) - 1, self._native_patterns[int(pattern_index)]) for pattern_index, _start, end in matches
                ]
            except (IndexError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(f"原生匹配器查询失败，切换到 Python 实现: {exc}")
                self._native_matcher = None

        self._build_python_matcher()
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
        if self._native_matcher is not None:
            try:
                stats: Dict[str, int] = {}
                for pattern in self._native_matcher.find_matches_as_strings(text):  # type: ignore[attr-defined]
                    stats[str(pattern)] = stats.get(str(pattern), 0) + 1
                return stats
            except (IndexError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(f"原生匹配器统计失败，切换到 Python 实现: {exc}")
                self._native_matcher = None

        results = self.search(text)
        stats = {}
        for _, pattern in results:
            stats[pattern] = stats.get(pattern, 0) + 1
        return stats
