import sys
from dataclasses import dataclass, field
import pytest
import importlib
import importlib.util
from types import ModuleType
from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.common.data_models.message_component_data_model import MessageSequence, ForwardComponent
    from src.chat.message_receive.message import (
        SessionMessage,
        TextComponent,
        ImageComponent,
        EmojiComponent,
        VoiceComponent,
        AtComponent,
        ReplyComponent,
        ForwardNodeComponent,
    )


class DummyLogger:
    def __init__(self) -> None:
        self.logging_record = []

    def debug(self, msg):
        print(f"DEBUG: {msg}")
        self.logging_record.append(f"DEBUG: {msg}")

    def info(self, msg):
        print(f"INFO: {msg}")
        self.logging_record.append(f"INFO: {msg}")

    def warning(self, msg):
        print(f"WARNING: {msg}")
        self.logging_record.append(f"WARNING: {msg}")

    def error(self, msg):
        print(f"ERROR: {msg}")
        self.logging_record.append(f"ERROR: {msg}")

    def critical(self, msg):
        print(f"CRITICAL: {msg}")
        self.logging_record.append(f"CRITICAL: {msg}")


def get_logger(name):
    return DummyLogger()


class DummyDBSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def exec(self, statement):
        return self

    def first(self):
        return None

    def commit(self):
        pass

    def all(self):
        return []


def get_db_session():
    return DummyDBSession()


def get_manual_db_session():
    return DummyDBSession()


class DummySelect:
    def __init__(self, model):
        self.model = model

    def filter_by(self, **kwargs):
        return self

    def where(self, condition):
        return self

    def limit(self, n):
        return self


def select(model):
    return DummySelect(model)


async def dummy_get_voice_text(binary_data):
    return None  # 可以根据需要返回模拟的文本结果


class DummyPersonUtils:
    @staticmethod
    def get_person_info_by_user_id_and_platform(user_id, platform):
        return None  # 可以根据需要返回模拟的用户信息


class DummyConfig:
    class MessageReceiveConfig:
        ban_words = set()
        ban_msgs_regex = set()

    message_receive = MessageReceiveConfig()


@dataclass
class UserInfo:
    user_id: str
    user_nickname: str
    user_cardname: Optional[str] = None


@dataclass
class GroupInfo:
    group_id: str
    group_name: str


@dataclass
class MessageInfo:
    user_info: UserInfo
    group_info: Optional[GroupInfo] = None
    additional_config: dict = field(default_factory=dict)


def setup_mocks(monkeypatch):
    def _stub_module(name: str) -> ModuleType:
        module = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    # src.common.logger
    logger_mod = _stub_module("src.common.logger")
    # Mock the logger
    logger_mod.get_logger = get_logger

    db_mod = _stub_module("src.common.database.database")
    db_mod.get_db_session = get_db_session
    db_mod.get_manual_db_session = get_manual_db_session

    db_model_mod = _stub_module("src.common.database.database_model")
    db_model_mod.Messages = None  # 可以根据需要添加更多的属性或方法

    emoji_manager_mod = _stub_module("src.chat.emoji_system.emoji_manager")
    emoji_manager_mod.emoji_manager = None  # 可以根据需要添加更多的属性或方法

    image_manager_mod = _stub_module("src.chat.image_system.image_manager")
    image_manager_mod.image_manager = None  # 可以根据需要添加更多的属性或方法

    voice_utils_mod = _stub_module("src.common.utils.utils_voice")
    voice_utils_mod.get_voice_text = dummy_get_voice_text

    person_utils_mod = _stub_module("src.common.utils.utils_person")
    person_utils_mod.PersonUtils = DummyPersonUtils

    config_mod = _stub_module("src.config.config")
    config_mod.global_config = DummyConfig()


def load_message_via_file(monkeypatch):
    setup_mocks(monkeypatch)
    file_path = Path(__file__).parent.parent.parent / "src" / "chat" / "message_receive" / "message.py"
    spec = importlib.util.spec_from_file_location("message", file_path)
    message_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "message_module", message_module)
    spec.loader.exec_module(message_module)
    message_module.select = select
    SessionMessageClass = message_module.SessionMessage
    TextComponentClass = message_module.TextComponent
    ImageComponentClass = message_module.ImageComponent
    EmojiComponentClass = message_module.EmojiComponent
    VoiceComponentClass = message_module.VoiceComponent
    AtComponentClass = message_module.AtComponent
    ReplyComponentClass = message_module.ReplyComponent
    ForwardNodeComponentClass = message_module.ForwardNodeComponent
    MessageSequenceClass = sys.modules["src.common.data_models.message_component_data_model"].MessageSequence
    ForwardComponentClass = sys.modules["src.common.data_models.message_component_data_model"].ForwardComponent
    globals()["SessionMessage"] = SessionMessageClass
    globals()["TextComponent"] = TextComponentClass
    globals()["ImageComponent"] = ImageComponentClass
    globals()["EmojiComponent"] = EmojiComponentClass
    globals()["VoiceComponent"] = VoiceComponentClass
    globals()["AtComponent"] = AtComponentClass
    globals()["ReplyComponent"] = ReplyComponentClass
    globals()["ForwardNodeComponent"] = ForwardNodeComponentClass
    globals()["MessageSequence"] = MessageSequenceClass
    globals()["ForwardComponent"] = ForwardComponentClass
    return message_module


def dummy_number_to_short_id(original_id: int, salt: str, length: int = 6) -> str:
    return "X" * length  # 返回固定的字符串，长度由参数决定，模拟生成短ID的行为

def dummy_is_bot_self(user_id: str) -> bool:
    return user_id == "bot_self"

def load_utils_via_file(monkeypatch):
    setup_mocks(monkeypatch)

    # Mock math_utils 模块，供 from .math_utils import number_to_short_id 使用
    math_utils_mod = ModuleType("src.common.utils.math_utils")
    math_utils_mod.number_to_short_id = dummy_number_to_short_id
    monkeypatch.setitem(sys.modules, "src.common.utils.math_utils", math_utils_mod)

    # 确保包层级模块存在于 sys.modules 中，使相对导入能正确解析
    for pkg_name in ["src", "src.common", "src.common.utils"]:
        if pkg_name not in sys.modules:
            pkg_mod = ModuleType(pkg_name)
            pkg_mod.__path__ = []
            monkeypatch.setitem(sys.modules, pkg_name, pkg_mod)

    file_path = Path(__file__).parent.parent.parent / "src" / "common" / "utils" / "utils_message.py"
    spec = importlib.util.spec_from_file_location("src.common.utils.utils_message", file_path)
    utils_module = importlib.util.module_from_spec(spec)
    utils_module.__package__ = "src.common.utils"  # 设置包，使相对导入生效
    monkeypatch.setitem(sys.modules, "src.common.utils.utils_message", utils_module)
    monkeypatch.setitem(sys.modules, "message_utils_module", utils_module)
    spec.loader.exec_module(utils_module)
    utils_module.is_bot_self = dummy_is_bot_self
    return utils_module


@pytest.mark.asyncio
async def test_message_utils(monkeypatch):
    load_message_via_file(monkeypatch)
    utils_module = load_utils_via_file(monkeypatch)
    MessageUtils = utils_module.MessageUtils


@pytest.mark.asyncio
async def test_build_readable_message_basic(monkeypatch):
    """基础用例：单条消息，显示行号"""
    load_message_via_file(monkeypatch)
    utils_module = load_utils_via_file(monkeypatch)
    MessageUtils = utils_module.MessageUtils

    msg = SessionMessage("m1", datetime.now())
    msg.platform = "test"
    msg.session_id = "s_test"
    user_info = UserInfo(user_id="u1", user_nickname="Alice")
    msg.message_info = MessageInfo(user_info=user_info)
    msg.raw_message = MessageSequence([TextComponent("Hello world")])
    text, mapping = await MessageUtils.build_readable_message([msg], anonymize=False, show_lineno=True)
    assert "[1] Alice说：Hello world" in text
    assert mapping == {}


@pytest.mark.asyncio
async def test_build_readable_message_anonymize(monkeypatch):
    """匿名化用例：验证 mapping 和返回文本"""
    load_message_via_file(monkeypatch)
    utils_module = load_utils_via_file(monkeypatch)
    MessageUtils = utils_module.MessageUtils

    msg = SessionMessage("m2", datetime.now())
    msg.platform = "test"
    msg.session_id = "s_test"
    user_info = UserInfo(user_id="u42", user_nickname="Bob")
    msg.message_info = MessageInfo(user_info=user_info)
    msg.raw_message = MessageSequence([TextComponent("Secret text")])
    text, mapping = await MessageUtils.build_readable_message([msg], anonymize=True, show_lineno=False)
    # 根据实现，original_name 为 user_nickname，因此文本中应包含原始名称
    assert "XXXXXX说：" in text
    assert "u42" in mapping
    assert mapping["u42"][0] == "XXXXXX"
    assert mapping["u42"][1] == "Bob"


@pytest.mark.asyncio
async def test_build_readable_message_replace_bot(monkeypatch):
    """替换机器人名用例：当 user_id 为 bot_self 时应被替换为 target_bot_name"""
    load_message_via_file(monkeypatch)
    utils_module = load_utils_via_file(monkeypatch)
    MessageUtils = utils_module.MessageUtils

    msg = SessionMessage("m3", datetime.now())
    msg.platform = "test"
    msg.session_id = "s_test"
    user_info = UserInfo(user_id="bot_self", user_nickname="SomeBot")
    msg.message_info = MessageInfo(user_info=user_info)
    msg.raw_message = MessageSequence([TextComponent("ping")])
    text, mapping = await MessageUtils.build_readable_message([msg], replace_bot_name=True, target_bot_name="MAIBot")
    assert "MAIBot说：ping" in text


@pytest.mark.asyncio
async def test_build_readable_message_image_extraction(monkeypatch):
    """图片提取：验证 extract_pictures 为 True 时，文本中包含图片占位及 img_map 内容被返回"""
    load_message_via_file(monkeypatch)
    utils_module = load_utils_via_file(monkeypatch)
    MessageUtils = utils_module.MessageUtils

    # 构建包含图片组件的消息
    img = ImageComponent(binary_hash="h", binary_data=b"\x01\x02", content="Img")
    msg = SessionMessage("mi1", datetime.now())
    msg.platform = "test"
    msg.session_id = "s_img"
    msg.raw_message = MessageSequence([img])
    msg.message_info = MessageInfo(UserInfo(user_id="ui_img", user_nickname="ImgUser"))
    text, mapping = await MessageUtils.build_readable_message([msg], extract_pictures=True)
    # 应包含图片描述占位
    assert "图片1" in text
    # mapping 不为空（匿名化未开启则为空）
    assert isinstance(mapping, dict)


@pytest.mark.asyncio
async def test_build_readable_message_anonymize_and_replace_bot_name_and_lineno(monkeypatch):
    """组合用例：多个消息同时包含匿名化、机器人名称替换"""
    load_message_via_file(monkeypatch)
    utils_module = load_utils_via_file(monkeypatch)
    MessageUtils = utils_module.MessageUtils
    # 构建多个消息
    msg1 = SessionMessage("m4", datetime.now())
    msg1.platform = "test"
    msg1.session_id = "s_comb"
    msg2 = SessionMessage("m5", datetime.now())
    msg2.platform = "test"
    msg2.session_id = "s_comb"
    msg1.message_info = MessageInfo(UserInfo(user_id="u_comb", user_nickname="Charlie"))
    msg2.message_info = MessageInfo(UserInfo(user_id="bot_self", user_nickname="SomeBot"))
    msg1.raw_message = MessageSequence([TextComponent("Hi")])
    msg2.raw_message = MessageSequence([TextComponent("Hello")])
    text, mapping = await MessageUtils.build_readable_message(
        [msg1, msg2],
        anonymize=True,
        replace_bot_name=True,
        target_bot_name="MAIBot",
        show_lineno=True,
    )
    # 验证文本内容
    assert "[1] XXXXXX说：Hi" in text
    assert "[2] MAIBot说：Hello" in text
    # 验证 mapping 内容
    assert "u_comb" in mapping
    assert mapping["u_comb"][0] == "XXXXXX"

@pytest.mark.asyncio
async def test_build_readable_message_with_at(monkeypatch):
    """包含@组件的消息：验证@组件中的用户信息也被匿名化和替换"""
    load_message_via_file(monkeypatch)
    utils_module = load_utils_via_file(monkeypatch)
    MessageUtils = utils_module.MessageUtils

    # 构建包含回复组件的消息
    at_comp = AtComponent(target_user_id="u_at", target_user_nickname="AtUser")
    msg = SessionMessage("m_at", datetime.now())
    msg.platform = "test"
    msg.session_id = "s_at"
    msg.raw_message = MessageSequence([at_comp])
    msg.message_info = MessageInfo(UserInfo(user_id="u_main", user_nickname="MainUser"))
    text, mapping = await MessageUtils.build_readable_message([msg], anonymize=True, replace_bot_name=True, target_bot_name="MAIBot")
    # 验证主消息和@组件中的用户信息都被处理
    assert "XXXXXX说：" in text  # 主消息用户被匿名化
    assert "XXXXXX说：@XXXXXX" in text  # @组件用户被匿名化