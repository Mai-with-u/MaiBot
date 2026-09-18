"""reply 工具越界 reply_style 的回归测试（issue #2051）。

模型可能返回 schema enum 之外的同义值（如「简短回复」，enum 里是「简短表达」）。
构建提示词阶段 `_build_requested_reply_style_message` 对 `style_messages` 裸下标
取值，越界值直接 KeyError，异常被 generate_reply_with_context 捕获后整轮回复
不再生成——一个「风格名写错」的小问题，代价是机器人对这条消息装聋。

契约：越界值只丢风格要求（按「正常回复」处理）、不丢回复，并记录越界值便于观察；
合法 enum 值与空值行为保持不变。
"""

import pytest

from src.chat.replyer import maisaka_generator_base as generator_module
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator

build_style_message = BaseMaisakaReplyGenerator._build_requested_reply_style_message


class TestRequestedReplyStyleBoundary:
    def test_out_of_enum_style_does_not_raise_and_falls_back_to_normal(self) -> None:
        """enum 外的同义值不得抛 KeyError；按「正常回复」处理（空字符串）。"""
        assert build_style_message("简短回复") == build_style_message("正常回复")

    def test_out_of_enum_style_is_logged_for_observation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """越界值必须留下可观察的告警日志，而不是静默吞掉。"""
        warnings: list[str] = []
        monkeypatch.setattr(
            generator_module.logger,
            "warning",
            lambda message, *args, **kwargs: warnings.append(str(message)),
        )

        build_style_message("简短回复")

        assert len(warnings) == 1
        assert "简短回复" in warnings[0]

    @pytest.mark.parametrize(
        "style",
        ["简短表达", "长回复"],
    )
    def test_in_enum_styles_keep_their_prompt_requirement(self, style: str) -> None:
        """合法 enum 值行为零变化：仍返回对应的篇幅要求文本。"""
        message = build_style_message(style)
        assert message  # 非「正常回复」的空串
        assert message == build_style_message(style)

    def test_normal_and_empty_styles_return_empty_message(self) -> None:
        """「正常回复」与空值/纯空白维持现状：不附加篇幅要求。"""
        assert build_style_message("正常回复") == ""
        assert build_style_message("") == ""
        assert build_style_message("   ") == ""
