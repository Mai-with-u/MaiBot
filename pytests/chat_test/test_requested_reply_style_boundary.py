"""reply 工具越界 reply_style 的行为契约（issue #2051）。

reply_style 来自模型对 reply 工具的调用，是未经清洗的外部输入：模型可能返回
schema enum 之外的同义值（如「简短回复」，而 enum 里是「简短表达」）。
`_build_requested_reply_style_message` 只负责拼接篇幅提示，越界值不该把整轮回复
带崩——按「正常回复」（不附加篇幅要求）处理，同时留下可观察的告警。

契约：越界值只丢风格要求、不丢回复；合法 enum 值与空值行为保持不变。
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
        ("style", "expected"),
        [
            ("简短表达", "请简短的回复，允许句子残缺，奇怪表达，倒装，省略，符合口语习惯，符合省力随意回复习惯"),
            ("长回复", "可以针对问题做出较为详细的评论和说明"),
        ],
    )
    def test_in_enum_styles_keep_their_prompt_requirement(self, style: str, expected: str) -> None:
        """合法 enum 值行为零变化：仍返回各自对应的篇幅要求文本。

        断言确切文本而非自比较——若两个风格的提示被交换或一起改动，自比较测试仍会
        通过，钉不住映射本身。
        """
        assert build_style_message(style) == expected

    def test_normal_and_empty_styles_return_empty_message(self) -> None:
        """「正常回复」与空值/纯空白维持现状：不附加篇幅要求。"""
        assert build_style_message("正常回复") == ""
        assert build_style_message("") == ""
        assert build_style_message("   ") == ""
