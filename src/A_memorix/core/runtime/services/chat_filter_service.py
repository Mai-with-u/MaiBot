from __future__ import annotations

from typing import Any, Dict

from .base import KernelServiceBase


class MemoryChatFilterService(KernelServiceBase):
    def is_chat_enabled(self, stream_id: str, group_id: str | None = None, user_id: str | None = None) -> bool:
        filter_config = self._cfg("filter", {}) or {}
        if not isinstance(filter_config, dict) or not filter_config:
            return True

        return self._chat_filter_config_allows(
            filter_config,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            default_when_empty=True,
        )

    @staticmethod
    def _chat_filter_config_allows(
        filter_config: Dict[str, Any],
        *,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
        default_when_empty: bool = True,
    ) -> bool:
        if not bool(filter_config.get("enabled", True)):
            return True

        mode = str(filter_config.get("mode", "blacklist") or "blacklist").strip().lower()
        patterns = filter_config.get("chats") or []
        if not isinstance(patterns, list):
            patterns = []

        if not patterns:
            return bool(default_when_empty) if mode == "blacklist" else False

        stream_token = str(stream_id or "").strip()
        group_token = str(group_id or "").strip()
        user_token = str(user_id or "").strip()
        candidates = {token for token in (stream_token, group_token, user_token) if token}

        matched = False
        for raw_pattern in patterns:
            pattern = str(raw_pattern or "").strip()
            if not pattern:
                continue
            if ":" in pattern:
                prefix, value = pattern.split(":", 1)
                prefix = prefix.strip().lower()
                value = value.strip()
                if prefix == "group" and value and value == group_token:
                    matched = True
                elif prefix in {"user", "private"} and value and value == user_token:
                    matched = True
                elif prefix == "stream" and value and value == stream_token:
                    matched = True
            elif pattern in candidates:
                matched = True

            if matched:
                break

        if mode == "blacklist":
            return not matched
        return matched

    def _is_chat_filtered(
        self,
        *,
        respect_filter: bool,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
    ) -> bool:
        if not bool(respect_filter):
            return False

        stream_token = str(stream_id or "").strip()
        group_token = str(group_id or "").strip()
        user_token = str(user_id or "").strip()
        if not (stream_token or group_token or user_token):
            return False
        return not self.is_chat_enabled(stream_token, group_token, user_token)
