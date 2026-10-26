from abc import ABC, abstractmethod
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import ast
import asyncio
import base64
import hashlib

import pytest


class QuietLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class ContextBuilder:
    def __init__(self):
        self.role = None
        self.text = ""

    def set_role(self, role):
        self.role = role
        return self

    def add_text_content(self, text):
        self.text += text
        return self

    def build(self):
        return SimpleNamespace(role=self.role, text=self.text)


@pytest.fixture
def learning():
    # Load the native components and selected learner methods without starting
    # database, plugin, model, or platform services during collection.
    root = Path(__file__).resolve().parents[2]
    namespace = {
        "ABC": ABC,
        "abstractmethod": abstractmethod,
        "deepcopy": deepcopy,
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Sequence": Sequence,
        "Tuple": Tuple,
        "Union": Union,
        "asyncio": asyncio,
        "base64": base64,
        "hashlib": hashlib,
        "StandardMessageComponents": Any,
        "Seg": SimpleNamespace,
        "logger": QuietLogger(),
    }
    components_path = root / "src/common/data_models/message_component_data_model.py"
    component_classes = [
        node for node in ast.parse(components_path.read_text(encoding="utf-8")).body if isinstance(node, ast.ClassDef)
    ]
    exec(compile(ast.Module(body=component_classes, type_ignores=[]), str(components_path), "exec"), namespace)
    styles_path = root / "src/learners/expression_style_utils.py"
    style_functions = [
        node for node in ast.parse(styles_path.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef)
    ]
    exec(compile(ast.Module(body=style_functions, type_ignores=[]), str(styles_path), "exec"), namespace)
    learner_path = root / "src/learners/expression_learner.py"
    learner_class = next(
        node
        for node in ast.parse(learner_path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == "ExpressionLearner"
    )
    methods = {"_build_multi_learning_messages", "_filter_expressions", "_learning_text_from_message"}
    learner_class.body = [
        node
        for node in learner_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in methods
    ]
    namespace.update(
        {
            "ContextItem": Any,
            "ContextItemBuilder": ContextBuilder,
            "RoleType": SimpleNamespace(System="system", User="user"),
            "global_config": SimpleNamespace(bot=SimpleNamespace(nickname="test-bot", alias_names=[])),
            "is_bot_self": lambda platform, user_id: user_id == "bot",
        }
    )
    exec(compile(ast.Module(body=[learner_class], type_ignores=[]), str(learner_path), "exec"), namespace)
    return SimpleNamespace(**namespace)


def message(learning, components, *, sender="human", processed=""):
    class Message:
        platform = "test"
        message_info = SimpleNamespace(
            user_info=SimpleNamespace(
                user_id=sender,
                user_nickname=sender,
                user_cardname=None,
            )
        )
        raw_message = learning.MessageSequence(components)
        processed_plain_text = processed

        async def process(self):
            # The normal chat projection may already contain resolved quotes.
            pass

    return Message()


@pytest.mark.asyncio
@pytest.mark.parametrize("prefilled", [False, True], ids=["resolved", "prefilled"])
async def test_quote_only_is_not_a_learning_source(learning, prefilled):
    reply = learning.ReplyComponent("old", target_message_content="引用原句" if prefilled else None)
    msg = message(learning, [reply], processed="引用原句" if prefilled else "[回复了test-bot的消息: 引用原句]")
    learner = learning.ExpressionLearner()

    request = await learner._build_multi_learning_messages([msg], "system")

    assert "引用原句" not in request[1].text
    assert learner._filter_expressions([("表示惊叹时", "使用 引用原句", "1")], [msg]) == []
    assert msg.processed_plain_text


@pytest.mark.asyncio
async def test_quote_does_not_hide_authored_body_or_change_chat_projection(learning):
    msg = message(
        learning,
        [learning.ReplyComponent("old", target_message_content="引用原句"), learning.TextComponent("我不同意这句话")],
        processed="引用原句 我不同意这句话",
    )
    learner = learning.ExpressionLearner()

    request = await learner._build_multi_learning_messages([msg], "system")

    assert "引用原句" not in request[1].text
    assert "我不同意这句话" in request[1].text
    assert learner._filter_expressions([("表达异议", "温和表达不同意见", "1")], [msg]) == [
        ("表达异议", "温和表达不同意见"),
    ]
    assert msg.processed_plain_text == "引用原句 我不同意这句话"


@pytest.mark.asyncio
async def test_forwarded_node_is_not_outer_senders_body(learning):
    forward = learning.ForwardNodeComponent(
        [
            learning.ForwardComponent(
                user_nickname="other",
                message_id="old",
                content=[learning.TextComponent("转发原句")],
            )
        ]
    )
    msg = message(learning, [forward], processed="[合并转发] 转发原句")
    learner = learning.ExpressionLearner()

    request = await learner._build_multi_learning_messages([msg], "system")

    assert "转发原句" not in request[1].text
    assert learner._filter_expressions([("感叹", "转发原句", "1")], [msg]) == []


@pytest.mark.asyncio
async def test_source_number_stays_bound_to_original_message_position(learning):
    first = message(learning, [learning.ReplyComponent("old")], processed="引用")
    second = message(learning, [learning.TextComponent("妙得很")], processed="妙得很")
    learner = learning.ExpressionLearner()

    request = await learner._build_multi_learning_messages([first, second], "system")

    assert "[id:1]" in request[1].text
    assert "[id:2]" in request[2].text
    candidates = [("感叹", "妙得很", "1"), ("感叹", "妙得很", "2"), ("感叹", "妙得很", "99")]
    assert learner._filter_expressions(candidates, [first, second]) == [("感叹", "妙得很")]


@pytest.mark.asyncio
async def test_multiple_authored_texts_keep_order_and_ignore_media_descriptions(learning):
    msg = message(
        learning,
        [
            learning.TextComponent("前半句"),
            learning.ImageComponent(binary_hash="image", content="图片生成的描述"),
            learning.TextComponent("后半句"),
        ],
        processed="前半句 图片生成的描述 后半句",
    )
    learner = learning.ExpressionLearner()

    request = await learner._build_multi_learning_messages([msg], "system")

    assert request[1].text.endswith("前半句 后半句")
    assert "图片生成的描述" not in request[1].text
    assert learner._filter_expressions([("说明", "抽象总结措辞", "1")], [msg]) == [("说明", "抽象总结措辞")]


def test_ordinary_body_and_self_and_invalid_id_checks_are_preserved(learning):
    human = message(learning, [learning.TextComponent("妙得很")], processed="妙得很")
    bot = message(learning, [learning.TextComponent("妙得很")], sender="bot", processed="妙得很")
    learner = learning.ExpressionLearner()
    candidates = [
        ("感叹", "使用 妙得很", "1"),
        ("感叹", "妙得很", "2"),
        ("感叹", "妙得很", "0"),
        ("感叹", "妙得很", "99"),
    ]

    assert learner._filter_expressions(candidates, [human, bot]) == [("感叹", "妙得很")]


@pytest.mark.asyncio
async def test_persisted_component_roundtrip_keeps_source_boundary(learning):
    original = learning.MessageSequence(
        [learning.ReplyComponent("old", target_message_content="引用原句"), learning.TextComponent("自己的正文")]
    )
    restored = learning.MessageSequence.from_dict(original.to_dict())
    msg = message(learning, restored.components, processed="引用原句 自己的正文")
    learner = learning.ExpressionLearner()

    request = await learner._build_multi_learning_messages([msg], "system")

    assert request[1].text.endswith("自己的正文")
    assert "引用原句" not in request[1].text
    assert learner._filter_expressions([("说明", "自己的正文", "1")], [msg]) == [("说明", "自己的正文")]


@pytest.mark.asyncio
@pytest.mark.parametrize("quoted", [False, True], ids=["voice", "quoted-voice"])
async def test_existing_voice_transcript_remains_authored_content(learning, quoted):
    transcript = "[语音: 我倾向先验证]"
    voice = learning.VoiceComponent(binary_hash="voice", content=transcript)
    components = [voice]
    if quoted:
        components.insert(0, learning.ReplyComponent("old", target_message_content="引用原句"))
    processed = ("引用原句 " if quoted else "") + transcript
    msg = message(learning, components, processed=processed)
    learner = learning.ExpressionLearner()

    request = await learner._build_multi_learning_messages([msg], "system")

    assert request[1].text.endswith(transcript)
    assert "引用原句" not in request[1].text
    assert learner._filter_expressions([("提出建议", "先核实再行动", "1")], [msg]) == [("提出建议", "先核实再行动")]
    assert voice.content == transcript
    assert msg.processed_plain_text == processed
