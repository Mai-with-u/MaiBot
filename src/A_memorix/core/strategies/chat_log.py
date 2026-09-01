from typing import List, Tuple

import re

from .base import ProcessedChunk
from .narrative import NarrativeStrategy


class ChatLogStrategy(NarrativeStrategy):
    """按带时间和发送者的消息边界切分纯文本聊天记录。"""

    _TIMESTAMP = r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T])?\d{1,2}:\d{2}(?::\d{2})?"
    _MESSAGE_HEADER = re.compile(
        rf"^(?:\[{_TIMESTAMP}\]|{_TIMESTAMP})\s*"
        r"(?:<[^>\r\n]{1,80}>|\[[^\]\r\n]{1,80}\]|[^\r\n:：]{1,80}[：:])\s*",
        flags=re.MULTILINE,
    )

    def __init__(self, filename: str, window_size: int, overlap: int):
        super().__init__(filename, window_size=window_size, overlap=overlap)
        self.split_warning = ""
        self.oversized_message_count = 0

    def split(self, text: str) -> List[ProcessedChunk]:
        self.split_warning = ""
        self.oversized_message_count = 0
        matches = list(self._MESSAGE_HEADER.finditer(text))
        if not matches or text[: matches[0].start()].strip():
            self.split_warning = "未识别到完整的时间与发送者消息头，已按普通叙事文本切块"
            return super().split(text)

        messages = self._message_ranges(text, matches)
        return self._window_messages(text, messages)

    @staticmethod
    def _message_ranges(text: str, matches: List[re.Match[str]]) -> List[Tuple[int, int]]:
        ranges: List[Tuple[int, int]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            ranges.append((match.start(), end))
        return ranges

    def _window_messages(self, text: str, messages: List[Tuple[int, int]]) -> List[ProcessedChunk]:
        chunks: List[ProcessedChunk] = []
        cursor = 0
        local_idx = 0

        while cursor < len(messages):
            end_index = cursor
            chunk_start = messages[cursor][0]
            while end_index < len(messages):
                candidate_end = messages[end_index][1]
                candidate_size = candidate_end - chunk_start
                if end_index > cursor and candidate_size > self.window_size:
                    break
                end_index += 1
                if candidate_size >= self.window_size:
                    break

            chunk_end = messages[end_index - 1][1]
            chunk_text = text[chunk_start:chunk_end]
            if len(chunk_text) > self.window_size and end_index == cursor + 1:
                self.oversized_message_count += 1
            chunks.append(self._create_chunk(chunk_text, "Chat Log", 0, local_idx, chunk_start))
            local_idx += 1

            if end_index >= len(messages):
                break

            next_cursor = end_index
            overlap_size = 0
            overlap_index = end_index - 1
            while overlap_index > cursor and overlap_size < self.overlap:
                message_start, message_end = messages[overlap_index]
                overlap_size += message_end - message_start
                next_cursor = overlap_index
                overlap_index -= 1
            cursor = next_cursor

        return chunks
