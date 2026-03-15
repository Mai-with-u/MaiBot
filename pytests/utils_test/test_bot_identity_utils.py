from pathlib import Path
from types import ModuleType, SimpleNamespace

import importlib.util
import sys


class DummyLogger:
    def __init__(self) -> None:
        self.warning_messages: list[str] = []

    def debug(self, _msg: str) -> None:
        return

    def info(self, _msg: str) -> None:
        return

    def warning(self, msg: str) -> None:
        self.warning_messages.append(msg)

    def error(self, _msg: str) -> None:
        return


def load_utils_module(monkeypatch, qq_account=123456, platforms=None):
    logger = DummyLogger()
    configured_platforms = platforms or []

    def _stub_module(name: str) -> ModuleType:
        module = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    for package_name in [
        "src",
        "src.chat",
        "src.chat.message_receive",
        "src.chat.utils",
        "src.common",
        "src.config",
        "src.llm_models",
        "src.person_info",
    ]:
        if package_name not in sys.modules:
            package_module = ModuleType(package_name)
            package_module.__path__ = []
            monkeypatch.setitem(sys.modules, package_name, package_module)

    jieba_module = ModuleType("jieba")
    jieba_module.cut = lambda text: list(text)
    monkeypatch.setitem(sys.modules, "jieba", jieba_module)

    logger_module = _stub_module("src.common.logger")
    logger_module.get_logger = lambda _name: logger

    config_module = _stub_module("src.config.config")
    config_module.global_config = SimpleNamespace(
        bot=SimpleNamespace(
            qq_account=qq_account,
            platforms=configured_platforms,
            nickname="MaiBot",
            alias_names=[],
        ),
        chat=SimpleNamespace(
            at_bot_inevitable_reply=1,
            mentioned_bot_reply=1,
        ),
    )
    config_module.model_config = SimpleNamespace()

    message_module = _stub_module("src.chat.message_receive.message")

    class SessionMessage:
        pass

    message_module.SessionMessage = SessionMessage

    chat_manager_module = _stub_module("src.chat.message_receive.chat_manager")
    chat_manager_module.chat_manager = SimpleNamespace(get_session_by_session_id=lambda _chat_id: None)

    llm_module = _stub_module("src.llm_models.utils_model")

    class LLMRequest:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

    llm_module.LLMRequest = LLMRequest

    person_module = _stub_module("src.person_info.person_info")

    class Person:
        pass

    person_module.Person = Person

    typo_generator_module = _stub_module("src.chat.utils.typo_generator")

    class ChineseTypoGenerator:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def create_typo_sentence(self, sentence: str):
            return sentence, ""

    typo_generator_module.ChineseTypoGenerator = ChineseTypoGenerator

    file_path = Path(__file__).parent.parent.parent / "src" / "chat" / "utils" / "utils.py"
    spec = importlib.util.spec_from_file_location("src.chat.utils.utils", file_path)
    utils_module = importlib.util.module_from_spec(spec)
    utils_module.__package__ = "src.chat.utils"
    monkeypatch.setitem(sys.modules, "src.chat.utils.utils", utils_module)
    assert spec.loader is not None
    spec.loader.exec_module(utils_module)
    return utils_module, logger


def test_platform_specific_bot_accounts(monkeypatch):
    utils_module, _logger = load_utils_module(
        monkeypatch,
        qq_account=123456,
        platforms=[" TG : tg_bot ", "discord: disc_bot"],
    )

    assert utils_module.get_bot_account("qq") == "123456"
    assert utils_module.get_bot_account("webui") == "123456"
    assert utils_module.get_bot_account("telegram") == "tg_bot"
    assert utils_module.get_bot_account("tg") == "tg_bot"
    assert utils_module.get_bot_account("discord") == "disc_bot"

    assert utils_module.is_bot_self("qq", "123456")
    assert utils_module.is_bot_self("webui", "123456")
    assert utils_module.is_bot_self("telegram", "tg_bot")
    assert utils_module.is_bot_self(" TG ", "tg_bot")


def test_get_all_bot_accounts_includes_runtime_aliases(monkeypatch):
    utils_module, _logger = load_utils_module(
        monkeypatch,
        qq_account=123456,
        platforms=["TG:tg_bot", "discord:disc_bot"],
    )

    assert utils_module.get_all_bot_accounts() == {
        "qq": "123456",
        "webui": "123456",
        "telegram": "tg_bot",
        "tg": "tg_bot",
        "discord": "disc_bot",
    }


def test_get_all_bot_accounts_keeps_canonical_qq_identity(monkeypatch):
    utils_module, _logger = load_utils_module(
        monkeypatch,
        qq_account=123456,
        platforms=["qq:999999", "webui:888888", "TG:tg_bot"],
    )

    assert utils_module.get_all_bot_accounts()["qq"] == "123456"
    assert utils_module.get_all_bot_accounts()["webui"] == "123456"


def test_unknown_platform_no_longer_falls_back_to_qq(monkeypatch):
    utils_module, logger = load_utils_module(monkeypatch, qq_account=123456, platforms=[])

    assert utils_module.is_bot_self("unknown_platform", "123456") is False
    assert logger.warning_messages
    assert "unknown_platform" in logger.warning_messages[-1]


def test_unknown_platform_warns_only_once(monkeypatch):
    utils_module, logger = load_utils_module(monkeypatch, qq_account=123456, platforms=[])

    assert utils_module.is_bot_self("unknown_platform", "first") is False
    assert utils_module.is_bot_self(" unknown_platform ", "second") is False
    assert len(logger.warning_messages) == 1


def test_unconfigured_qq_account_disables_qq_and_webui_identity(monkeypatch):
    utils_module, _logger = load_utils_module(monkeypatch, qq_account=0, platforms=["telegram:tg_bot"])

    assert utils_module.get_bot_account("qq") == ""
    assert utils_module.get_bot_account("webui") == ""
    assert utils_module.is_bot_self("qq", "0") is False
    assert utils_module.is_bot_self("webui", "0") is False


def test_is_mentioned_bot_in_message_uses_platform_account(monkeypatch):
    utils_module, _logger = load_utils_module(monkeypatch, qq_account=123456, platforms=["TG:tg_bot"])

    message = SimpleNamespace(
        processed_plain_text="@tg_bot 你好",
        platform="telegram",
        is_mentioned=False,
        message_segment=None,
        message_info=SimpleNamespace(
            additional_config={},
            user_info=SimpleNamespace(user_id="user_1"),
        ),
    )

    is_mentioned, is_at, reply_probability = utils_module.is_mentioned_bot_in_message(message)

    assert is_mentioned is True
    assert is_at is True
    assert reply_probability == 1.0


def test_is_mentioned_bot_in_message_normalizes_qq_platform(monkeypatch):
    utils_module, _logger = load_utils_module(monkeypatch, qq_account=123456, platforms=[])

    message = SimpleNamespace(
        processed_plain_text="@<MaiBot:123456> 你好",
        platform=" QQ ",
        is_mentioned=False,
        message_segment=None,
        message_info=SimpleNamespace(
            additional_config={},
            user_info=SimpleNamespace(user_id="user_1"),
        ),
    )

    is_mentioned, is_at, reply_probability = utils_module.is_mentioned_bot_in_message(message)

    assert is_mentioned is True
    assert is_at is True
    assert reply_probability == 1.0
