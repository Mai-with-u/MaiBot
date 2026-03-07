# 本文件为测试文件，含有大量的MonkeyPatch和Mock代码，请忽略TypeChecker的报错
import importlib.util
import sys
from dataclasses import dataclass
from types import ModuleType
from pathlib import Path

import pytest


def _install_stub_modules(monkeypatch):
    def _stub_module(name: str) -> ModuleType:
        module = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    # src.common.logger
    logger_mod = _stub_module("src.common.logger")

    class _Logger:
        def __init__(self):
            self.info_calls = []
            self.debug_calls = []
            self.warning_calls = []
            self.error_calls = []
            self.critical_calls = []

        def info(self, *args, **kwargs):
            self.info_calls.append(args)

        def debug(self, *args, **kwargs):
            self.debug_calls.append(args)

        def warning(self, *args, **kwargs):
            self.warning_calls.append(args)

        def error(self, *args, **kwargs):
            self.error_calls.append(args)

        def critical(self, *args, **kwargs):
            self.critical_calls.append(args)

    def get_logger(_name: str):
        return _Logger()

    logger_mod.get_logger = get_logger

    # src.common.data_models.image_data_model
    data_model_mod = _stub_module("src.common.data_models.image_data_model")

    @dataclass
    class MaiEmoji:
        full_path: Path | None = None
        file_name: str = ""
        description: str | None = None
        emotion: list[str] | None = None
        file_hash: str | None = None
        query_count: int = 0
        register_time: object | None = None
        image_format: str | None = None
        image_bytes: bytes | None = None

        @staticmethod
        def from_db_instance(_record):
            return MaiEmoji()

        def to_db_instance(self):
            return Images()

        async def calculate_hash_format(self):
            return True

        @staticmethod
        def read_image_bytes(_path):
            return b""

    data_model_mod.MaiEmoji = MaiEmoji

    # src.common.database.database_model
    db_model_mod = _stub_module("src.common.database.database_model")

    class Images:
        id = 0
        is_registered = False
        is_banned = False
        no_file_flag = False
        register_time = None
        query_count = 0
        last_used_time = None
        full_path = ""
        image_hash = ""
        image_type = None

    class ImageType:
        EMOJI = "EMOJI"

    db_model_mod.Images = Images
    db_model_mod.ImageType = ImageType

    # src.common.database.database
    db_mod = _stub_module("src.common.database.database")

    class _DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            class _Result:
                def scalars(self):
                    return self

                def all(self):
                    return []

                def first(self):
                    return None

            return _Result()

        def add(self, _record):
            pass

        def delete(self, _record):
            pass

        def flush(self):
            pass

        def commit(self):
            pass

    def get_db_session():
        return _DummySession()

    def get_db_session_manual():
        return _DummySession()

    db_mod.get_db_session = get_db_session
    db_mod.get_db_session_manual = get_db_session_manual

    # src.common.utils.utils_image
    image_utils_mod = _stub_module("src.common.utils.utils_image")

    class ImageUtils:
        @staticmethod
        def gif_2_static_image(_image_bytes):
            return b""

        @staticmethod
        def image_bytes_to_base64(_image_bytes):
            return ""

    image_utils_mod.ImageUtils = ImageUtils

    # src.prompt.prompt_manager
    prompt_manager_mod = _stub_module("src.prompt.prompt_manager")

    class _Prompt:
        def add_context(self, _key, _value):
            pass

    class _PromptManager:
        def get_prompt(self, _name):
            return _Prompt()

        async def render_prompt(self, _prompt):
            return ""

    prompt_manager_mod.prompt_manager = _PromptManager()

    # src.config.config
    config_mod = _stub_module("src.config.config")

    class _EmojiConfig:
        max_reg_num = 20
        content_filtration = False
        filtration_prompt = ""
        steal_emoji = False
        do_replace = False
        check_interval = 1

    class _BotConfig:
        nickname = "bot"

    class _ModelTaskConfig:
        vlm = None
        utils = None

    class _ModelConfig:
        model_task_config = _ModelTaskConfig()

    class _GlobalConfig:
        emoji = _EmojiConfig()
        bot = _BotConfig()

    config_mod.global_config = _GlobalConfig()
    config_mod.model_config = _ModelConfig()

    # src.llm_models.utils_model
    llm_mod = _stub_module("src.llm_models.utils_model")

    class LLMRequest:
        def __init__(self, *args, **kwargs):
            pass

        async def generate_response_async(self, *args, **kwargs):
            return "", None

        async def generate_response_for_image(self, *args, **kwargs):
            return "", None

    llm_mod.LLMRequest = LLMRequest

    # third-party stubs
    rich_traceback_mod = _stub_module("rich.traceback")

    def install(*_args, **_kwargs):
        pass

    rich_traceback_mod.install = install

    sqlmodel_mod = _stub_module("sqlmodel")

    def select(_model):
        return object()

    sqlmodel_mod.select = select

    levenshtein_mod = _stub_module("Levenshtein")

    def distance(a, b):
        return abs(len(str(a)) - len(str(b)))

    levenshtein_mod.distance = distance


def import_emoji_manager_new(monkeypatch):
    _install_stub_modules(monkeypatch)
    file_path = Path(__file__).resolve().parents[2] / "src" / "chat" / "emoji_system" / "emoji_manager.py"
    spec = importlib.util.spec_from_file_location("emoji_manager", file_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "emoji_manager_new", module)
    spec.loader.exec_module(module)

    class _Select:
        def filter_by(self, **kwargs):
            return self

        def limit(self, n):
            return self

    module.select = lambda _model: _Select()
    return module


def _messages(calls):
    return [" ".join(map(str, args)) for args in calls]


@pytest.mark.asyncio
async def test_replace_an_emoji_by_llm_decision_no_delete(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    async def _generate_response_async(*_args, **_kwargs):
        return "不删除", None

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )

    result = await manager.replace_an_emoji_by_llm(emoji_manager_new.MaiEmoji())

    assert result is False
    assert any("不删除任何表情包" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_replace_an_emoji_by_llm_decision_parse_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    async def _generate_response_async(*_args, **_kwargs):
        return "删除编号1", None

    def _bad_search(*_args, **_kwargs):
        raise RuntimeError("search failed")

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )
    monkeypatch.setattr(emoji_manager_new.re, "search", _bad_search)

    result = await manager.replace_an_emoji_by_llm(emoji_manager_new.MaiEmoji())

    assert result is False
    assert any("解析决策结果时出错" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_replace_an_emoji_by_llm_decision_missing_number(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    async def _generate_response_async(*_args, **_kwargs):
        return "删除编号ABC", None

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )

    result = await manager.replace_an_emoji_by_llm(emoji_manager_new.MaiEmoji())

    assert result is False
    assert any("未能解析删除编号" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_replace_an_emoji_by_llm_decision_index_out_of_range(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    async def _generate_response_async(*_args, **_kwargs):
        return "删除编号3", None

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )

    result = await manager.replace_an_emoji_by_llm(emoji_manager_new.MaiEmoji())

    assert result is False
    assert any("无效的表情包编号" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_replace_an_emoji_by_llm_delete_failed(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    async def _generate_response_async(*_args, **_kwargs):
        return "删除编号1", None

    def _delete(_emoji):
        return False

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )
    monkeypatch.setattr(manager, "delete_emoji", _delete)

    result = await manager.replace_an_emoji_by_llm(emoji_manager_new.MaiEmoji())

    assert result is False
    assert any("删除表情包失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_replace_an_emoji_by_llm_register_failed(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    async def _generate_response_async(*_args, **_kwargs):
        return "删除编号1", None

    def _delete(_emoji):
        return True

    def _register(_emoji):
        return False

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )
    monkeypatch.setattr(manager, "delete_emoji", _delete)
    monkeypatch.setattr(manager, "register_emoji_to_db", _register)

    result = await manager.replace_an_emoji_by_llm(emoji_manager_new.MaiEmoji())

    assert result is False
    assert any("注册新表情包失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_replace_an_emoji_by_llm_success(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    old_emoji = emoji_manager_new.MaiEmoji()
    old_emoji.description = "old"
    manager.emojis = [old_emoji]

    async def _generate_response_async(*_args, **_kwargs):
        return "删除编号1", None

    def _delete(_emoji):
        return True

    def _register(_emoji):
        return True

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )
    monkeypatch.setattr(manager, "delete_emoji", _delete)
    monkeypatch.setattr(manager, "register_emoji_to_db", _register)

    new_emoji = emoji_manager_new.MaiEmoji()
    new_emoji.description = "new"

    result = await manager.replace_an_emoji_by_llm(new_emoji)

    assert result is True
    assert new_emoji in manager.emojis
    assert old_emoji not in manager.emojis
    assert any("成功替换并注册新表情包" in m for m in _messages(logger.info_calls))


def test_load_emojis_from_db_empty(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    manager = emoji_manager_new.EmojiManager()

    manager.load_emojis_from_db()

    assert manager.emojis == []
    assert manager._emoji_num == 0
    assert any("成功加载" in m for m in _messages(logger.info_calls))


def test_load_emojis_from_db_partial_bad_records(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _Record:
        def __init__(self, record_id, full_path):
            self.id = record_id
            self.full_path = full_path
            self.image_type = emoji_manager_new.ImageType.EMOJI
            self.no_file_flag = False
            self.is_banned = False

    records = [_Record(1, "bad"), _Record(2, "ok")]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return records

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    def _from_db_instance(record):
        if record.id == 1:
            raise ValueError("bad record")
        emoji = emoji_manager_new.MaiEmoji()
        emoji.file_name = "ok"
        return emoji

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "from_db_instance", staticmethod(_from_db_instance))
    manager = emoji_manager_new.EmojiManager()

    manager.load_emojis_from_db()

    assert len(manager.emojis) == 1
    assert manager.emojis[0].file_name == "ok"
    assert manager._emoji_num == 1
    assert any("加载表情包记录时出错" in m for m in _messages(logger.error_calls))
    assert any("成功加载" in m for m in _messages(logger.info_calls))


def test_load_emojis_from_db_execute_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            raise RuntimeError("execute failed")

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]
    manager._emoji_num = 1

    with pytest.raises(RuntimeError):
        manager.load_emojis_from_db()

    assert manager.emojis == []
    assert manager._emoji_num == 0
    assert any("不可恢复错误" in m for m in _messages(logger.critical_calls))


def test_load_emojis_from_db_get_db_session_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    def _get_db_session():
        raise RuntimeError("get_db_session failed")

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]
    manager._emoji_num = 1

    with pytest.raises(RuntimeError):
        manager.load_emojis_from_db()

    assert manager.emojis == []
    assert manager._emoji_num == 0
    assert any("不可恢复错误" in m for m in _messages(logger.critical_calls))


def test_load_emojis_from_db_scalars_all_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _Result:
        def scalars(self):
            return self

        def all(self):
            raise RuntimeError("all failed")

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]
    manager._emoji_num = 1

    with pytest.raises(RuntimeError):
        manager.load_emojis_from_db()

    assert manager.emojis == []
    assert manager._emoji_num == 0
    assert any("不可恢复错误" in m for m in _messages(logger.critical_calls))


def test_load_emojis_from_db_skips_filtered_records(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _Record:
        def __init__(self, record_id, full_path, image_type, no_file_flag=False, is_banned=False):
            self.id = record_id
            self.full_path = full_path
            self.image_type = image_type
            self.no_file_flag = no_file_flag
            self.is_banned = is_banned

    records = [
        _Record(1, "img.png", "IMAGE"),
        _Record(2, "nofile.png", emoji_manager_new.ImageType.EMOJI, no_file_flag=True),
        _Record(3, "banned.png", emoji_manager_new.ImageType.EMOJI, is_banned=True),
        _Record(4, "ok.png", emoji_manager_new.ImageType.EMOJI),
    ]

    class _Result:
        def all(self):
            return records

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    created = []

    def _from_db_instance(record):
        emoji = emoji_manager_new.MaiEmoji()
        emoji.file_name = record.full_path
        created.append(record.id)
        return emoji

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "from_db_instance", staticmethod(_from_db_instance))
    manager = emoji_manager_new.EmojiManager()

    manager.load_emojis_from_db()

    assert created == [4]
    assert len(manager.emojis) == 1
    assert manager._emoji_num == 1
    assert any("成功加载" in m for m in _messages(logger.info_calls))


def test_register_emoji_to_db_invalid_object(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    result = manager.register_emoji_to_db(None)

    assert result is False
    assert any("无效的表情包对象" in m for m in _messages(logger.error_calls))


def test_register_emoji_to_db_wrong_type(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    result = manager.register_emoji_to_db(object())

    assert result is False
    assert any("无效的表情包对象" in m for m in _messages(logger.error_calls))


def test_register_emoji_to_db_file_missing(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = Path("/missing/file.png")

    result = manager.register_emoji_to_db(emoji)

    assert result is False
    assert any("表情包文件不存在" in m for m in _messages(logger.error_calls))


def test_register_emoji_to_db_move_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self):
            self._name = "a.png"
            self._exists = True

        def exists(self):
            return self._exists

        def replace(self, _target):
            raise RuntimeError("move failed")

        @property
        def name(self):
            return self._name

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath()
    emoji.file_name = "a.png"

    result = manager.register_emoji_to_db(emoji)

    assert result is False
    assert any("移动表情包文件时出错" in m for m in _messages(logger.error_calls))


def test_register_emoji_to_db_db_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self):
            self._name = "a.png"
            self._exists = True
            self._replaced = False

        def exists(self):
            return self._exists

        def replace(self, _target):
            self._replaced = True

        @property
        def name(self):
            return self._name

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath()
    emoji.file_name = "a.png"

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add(self, _record):
            raise RuntimeError("db add failed")

        def flush(self):
            pass

        def exec(self, _statement):
            return self

        def first(self):
            return None

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    result = manager.register_emoji_to_db(emoji)

    assert result is False
    assert any("注册到数据库时出错" in m for m in _messages(logger.error_calls))


def test_register_emoji_to_db_success(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self, name):
            self._name = name
            self._exists = True
            self._replaced = False
            self._target = None

        def exists(self):
            return self._exists

        def replace(self, target):
            self._replaced = True
            self._target = target

        @property
        def name(self):
            return self._name

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath("a.png")
    emoji.file_name = "a.png"

    class _Record:
        def __init__(self):
            self.id = 123
            self.is_registered = False
            self.is_banned = False
            self.register_time = None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add(self, _record):
            pass

        def flush(self):
            pass

        def exec(self, _statement):
            return self

        def first(self):
            return None

    def _get_db_session():
        return _Session()

    def _to_db_instance(self):
        return _Record()

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "to_db_instance", _to_db_instance, raising=False)

    result = manager.register_emoji_to_db(emoji)

    assert result is True
    assert any("成功注册表情包到数据库" in m for m in _messages(logger.info_calls))


def test_delete_emoji_file_missing_and_db_record_missing(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self):
            self._name = "missing.png"

        def unlink(self):
            raise FileNotFoundError("missing")

        def exists(self):
            return False

        @property
        def name(self):
            return self._name

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath()
    emoji.file_name = "missing.png"
    emoji.file_hash = "hash-missing"

    result = manager.delete_emoji(emoji)

    assert result is True
    assert any("不存在" in m for m in _messages(logger.warning_calls))
    assert any("未找到表情包记录" in m for m in _messages(logger.warning_calls))


def test_delete_emoji_file_delete_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self):
            self._name = "boom.png"

        def unlink(self):
            raise RuntimeError("unlink failed")

        @property
        def name(self):
            return self._name

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath()
    emoji.file_name = "boom.png"
    emoji.file_hash = "hash-boom"

    result = manager.delete_emoji(emoji)

    assert result is False
    assert any("删除表情包文件时出错" in m for m in _messages(logger.error_calls))


def test_delete_emoji_db_error_file_still_exists(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self):
            self._name = "keep.png"

        def unlink(self):
            return None

        def exists(self):
            return True

        @property
        def name(self):
            return self._name

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            raise RuntimeError("db delete failed")

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath()
    emoji.file_name = "keep.png"
    emoji.file_hash = "hash-keep"

    result = manager.delete_emoji(emoji)

    assert result is False
    assert any("删除数据库记录时出错" in m for m in _messages(logger.error_calls))
    assert any("数据库记录修改失败，但文件仍存在" in m for m in _messages(logger.warning_calls))


def test_delete_emoji_success(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self):
            self._name = "ok.png"
            self._deleted = False

        def unlink(self):
            self._deleted = True

        def exists(self):
            return not self._deleted

        @property
        def name(self):
            return self._name

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Record:
        def __init__(self):
            self.no_file_flag = False

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return _Record()

    class _Session:
        def __init__(self):
            self.added = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

        def add(self, _record):
            self.added = True

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath()
    emoji.file_name = "ok.png"
    emoji.file_hash = "hash-ok"

    result = manager.delete_emoji(emoji)

    assert result is True
    assert any("成功删除表情包文件" in m for m in _messages(logger.info_calls))
    assert any("成功修改数据库中的表情包记录" in m for m in _messages(logger.info_calls))


def test_delete_emoji_no_desc_deletes_record(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _DummyPath:
        def __init__(self):
            self._name = "empty.png"

        def unlink(self):
            return None

        def exists(self):
            return False

        @property
        def name(self):
            return self._name

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return object()

    class _Session:
        def __init__(self):
            self.deleted = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

        def delete(self, _record):
            self.deleted = True

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.full_path = _DummyPath()
    emoji.file_name = "empty.png"
    emoji.file_hash = "hash-empty"

    result = manager.delete_emoji(emoji, no_desc=True)

    assert result is True
    assert any("成功删除数据库中的空表情包记录" in m for m in _messages(logger.info_calls))


def test_update_emoji_usage_success(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Record:
        def __init__(self):
            self.query_count = 2
            self.last_used_time = None

    record = _Record()

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return record

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

        def add(self, _record):
            self.added = True

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-ok"

    result = manager.update_emoji_usage(emoji)

    assert result is True
    assert emoji.query_count == 1
    assert record.query_count == 1
    assert any("成功记录表情包使用" in m for m in _messages(logger.info_calls))


def test_update_emoji_usage_missing_record(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-missing"

    result = manager.update_emoji_usage(emoji)

    assert result is False
    assert any("未找到表情包记录" in m for m in _messages(logger.error_calls))


def test_update_emoji_usage_execute_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            raise RuntimeError("execute failed")

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-execute"

    result = manager.update_emoji_usage(emoji)

    assert result is False
    assert any("记录使用时出错" in m for m in _messages(logger.error_calls))


def test_update_emoji_usage_get_db_session_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    def _get_db_session():
        raise RuntimeError("get_db_session failed")

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-session"

    result = manager.update_emoji_usage(emoji)

    assert result is False
    assert any("记录使用时出错" in m for m in _messages(logger.error_calls))


def test_update_emoji_success(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Record:
        def __init__(self):
            self.description = None
            self.emotion = None

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return _Record()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

        def add(self, _record):
            self.added = True

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-update"
    emoji.description = "new-desc"
    emoji.emotion = ["a", "b"]

    result = manager.update_emoji(emoji)

    assert result is True
    assert any("成功更新表情包信息" in m for m in _messages(logger.info_calls))


def test_update_emoji_missing_record(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-missing"

    result = manager.update_emoji(emoji)

    assert result is False
    assert any("未找到表情包记录" in m for m in _messages(logger.error_calls))


def test_update_emoji_execute_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            raise RuntimeError("execute failed")

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-execute"

    result = manager.update_emoji(emoji)

    assert result is False
    assert any("更新数据库记录时出错" in m for m in _messages(logger.error_calls))


def test_update_emoji_get_db_session_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    def _get_db_session():
        raise RuntimeError("get_db_session failed")

    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-session"

    result = manager.update_emoji(emoji)

    assert result is False
    assert any("更新数据库记录时出错" in m for m in _messages(logger.error_calls))


def test_get_emoji_by_hash_from_db_no_file_flag(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Record:
        def __init__(self):
            self.no_file_flag = True
            self.image_hash = "hash-nofile"

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return _Record()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    result = manager.get_emoji_by_hash_from_db("hash-nofile")

    assert result is None
    assert any("标记为文件不存在" in m for m in _messages(logger.warning_calls))


def test_get_emoji_by_hash_from_db_success(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Record:
        def __init__(self):
            self.no_file_flag = False
            self.image_hash = "hash-ok"

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return _Record()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash-ok"

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "from_db_instance", staticmethod(lambda _r: emoji))

    result = manager.get_emoji_by_hash_from_db("hash-ok")

    assert result is emoji


def test_ban_emoji_success(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Record:
        def __init__(self):
            self.is_banned = False

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return _Record()

    class _Session:
        def __init__(self):
            self.added = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

        def add(self, _record):
            self.added = True

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_name = "ban.png"
    emoji.file_hash = "hash-ban"
    manager.emojis = [emoji]

    result = manager.ban_emoji(emoji)

    assert result is True
    assert emoji not in manager.emojis
    assert any("成功封禁表情包" in m for m in _messages(logger.info_calls))


def test_ban_emoji_missing_record(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return _Result()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_name = "missing.png"
    emoji.file_hash = "hash-missing"

    result = manager.ban_emoji(emoji)

    assert result is False
    assert any("未找到表情包记录" in m for m in _messages(logger.warning_calls))


def test_ban_emoji_db_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    class _Select:
        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _select(_model):
        return _Select()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            raise RuntimeError("db failed")

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "select", _select)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_name = "boom.png"
    emoji.file_hash = "hash-boom"

    result = manager.ban_emoji(emoji)

    assert result is False
    assert any("封禁时出错" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_get_emoji_for_emotion_empty_list(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = []

    result = await manager.get_emoji_for_emotion("开心")

    assert result is None
    assert any("表情包列表为空" in m for m in _messages(logger.warning_calls))


@pytest.mark.asyncio
async def test_get_emoji_for_emotion_no_matches(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    def _calc(_label):
        return []

    monkeypatch.setattr(manager, "_calculate_emotion_similarity_list", _calc)

    result = await manager.get_emoji_for_emotion("无匹配")

    assert result is None
    assert any("未找到匹配的表情包" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_get_emoji_for_emotion_success_updates_usage(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    emoji1 = emoji_manager_new.MaiEmoji()
    emoji1.file_name = "e1.png"
    emoji1.emotion = ["开心"]
    emoji2 = emoji_manager_new.MaiEmoji()
    emoji2.file_name = "e2.png"
    emoji2.emotion = ["难过"]
    manager.emojis = [emoji1, emoji2]

    def _calc(_label):
        return [(emoji1, 0.9), (emoji2, 0.2)]

    monkeypatch.setattr(manager, "_calculate_emotion_similarity_list", _calc)
    monkeypatch.setattr(emoji_manager_new.random, "choice", lambda items: items[0])

    called = {"emoji": None}

    def _update(emoji):
        called["emoji"] = emoji
        return True

    monkeypatch.setattr(manager, "update_emoji_usage", _update)

    result = await manager.get_emoji_for_emotion("开心")

    assert result is emoji1
    assert called["emoji"] is emoji1
    assert any("选中表情包" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_get_emoji_for_emotion_similarity_error_propagates(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    manager = emoji_manager_new.EmojiManager()
    manager.emojis = [emoji_manager_new.MaiEmoji()]

    def _calc(_label):
        raise RuntimeError("calc failed")

    monkeypatch.setattr(manager, "_calculate_emotion_similarity_list", _calc)

    with pytest.raises(RuntimeError):
        await manager.get_emoji_for_emotion("异常")


@pytest.mark.asyncio
async def test_build_emoji_description_calls_hash_and_sets_description(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    called = {"hash": False, "vlm": False}

    async def _calc(self):
        called["hash"] = True
        return True

    def _read_bytes(_path):
        return b""

    async def _vlm_response(*_args, **_kwargs):
        called["vlm"] = True
        return "desc", None

    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "calculate_hash_format", _calc, raising=False)
    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "read_image_bytes", staticmethod(_read_bytes), raising=False)
    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_vlm,
        "generate_response_for_image",
        _vlm_response,
    )

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = None
    emoji.image_format = "png"
    emoji.full_path = Path("/tmp/a.png")

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_description(emoji)

    assert result is True
    assert updated.description == "desc"
    assert called["hash"] is True
    assert called["vlm"] is True
    assert any("成功为表情包构建描述" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_build_emoji_description_gif_conversion_error(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    def _read_bytes(_path):
        return b""

    def _gif_to_static(_bytes):
        raise RuntimeError("gif fail")

    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "read_image_bytes", staticmethod(_read_bytes), raising=False)
    monkeypatch.setattr(emoji_manager_new.ImageUtils, "gif_2_static_image", staticmethod(_gif_to_static))

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash"
    emoji.image_format = "gif"
    emoji.full_path = Path("/tmp/a.gif")

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_description(emoji)

    assert result is False
    assert updated.description is None
    assert any("转换 GIF 图片时出错" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_build_emoji_description_content_filtration_reject(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    emoji_manager_new.global_config.emoji.content_filtration = True
    emoji_manager_new.global_config.emoji.filtration_prompt = "rule"

    def _read_bytes(_path):
        return b""

    call_count = {"n": 0}

    async def _vlm_response(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return "否", None
        return "desc", None

    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "read_image_bytes", staticmethod(_read_bytes), raising=False)
    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_vlm,
        "generate_response_for_image",
        _vlm_response,
    )

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash"
    emoji.image_format = "png"
    emoji.full_path = Path("/tmp/a.png")

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_description(emoji)

    assert result is False
    assert updated.description is None
    assert any("表情包内容不符合要求" in m for m in _messages(logger.warning_calls))


@pytest.mark.asyncio
async def test_build_emoji_description_content_filtration_pass(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    emoji_manager_new.global_config.emoji.content_filtration = True
    emoji_manager_new.global_config.emoji.filtration_prompt = "rule"

    def _read_bytes(_path):
        return b""

    async def _vlm_response(prompt, *_args, **_kwargs):
        if "rule" in str(prompt):
            return "是", None
        return "desc", None

    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "read_image_bytes", staticmethod(_read_bytes), raising=False)
    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_vlm,
        "generate_response_for_image",
        _vlm_response,
    )

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash"
    emoji.image_format = "png"
    emoji.full_path = Path("/tmp/a.png")

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_description(emoji)

    assert result is True
    assert updated.description == "desc"
    assert any("成功为表情包构建描述" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_build_emoji_description_vlm_exception_propagates(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)

    def _read_bytes(_path):
        return b""

    async def _vlm_response(*_args, **_kwargs):
        raise RuntimeError("vlm failed")

    monkeypatch.setattr(emoji_manager_new.MaiEmoji, "read_image_bytes", staticmethod(_read_bytes), raising=False)
    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_vlm,
        "generate_response_for_image",
        _vlm_response,
    )

    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_hash = "hash"
    emoji.image_format = "png"
    emoji.full_path = Path("/tmp/a.png")

    with pytest.raises(RuntimeError):
        await emoji_manager_new.EmojiManager().build_emoji_description(emoji)


@pytest.mark.asyncio
async def test_build_emoji_emotion_description_missing(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    emoji = emoji_manager_new.MaiEmoji()
    emoji.description = None

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_emotion(emoji)

    assert result is False
    assert updated.emotion is None
    assert any("表情包描述为空" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_build_emoji_emotion_llm_exception_propagates(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)

    async def _generate_response_async(*_args, **_kwargs):
        raise RuntimeError("llm failed")

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )

    emoji = emoji_manager_new.MaiEmoji()
    emoji.description = "desc"

    with pytest.raises(RuntimeError):
        await emoji_manager_new.EmojiManager().build_emoji_emotion(emoji)


@pytest.mark.asyncio
async def test_build_emoji_emotion_empty_result(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    async def _generate_response_async(*_args, **_kwargs):
        return " , ，  ", None

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )

    emoji = emoji_manager_new.MaiEmoji()
    emoji.description = "desc"

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_emotion(emoji)

    assert result is True
    assert updated.emotion == []
    assert any("成功为表情包构建情感标签" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_build_emoji_emotion_more_than_five_random_sample(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    async def _generate_response_async(*_args, **_kwargs):
        return "a,b,c,d,e,f", None

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )
    monkeypatch.setattr(emoji_manager_new.random, "sample", lambda items, _k: items[:3])

    emoji = emoji_manager_new.MaiEmoji()
    emoji.description = "desc"

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_emotion(emoji)

    assert result is True
    assert updated.emotion == ["a", "b", "c"]
    assert any("成功为表情包构建情感标签" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_build_emoji_emotion_three_items_random_sample(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    async def _generate_response_async(*_args, **_kwargs):
        return "a，b，c", None

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )
    monkeypatch.setattr(emoji_manager_new.random, "sample", lambda items, _k: items[:2])

    emoji = emoji_manager_new.MaiEmoji()
    emoji.description = "desc"

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_emotion(emoji)

    assert result is True
    assert updated.emotion == ["a", "b"]
    assert any("成功为表情包构建情感标签" in m for m in _messages(logger.info_calls))


def test_check_emoji_file_integrity_no_issues(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _DummyPath:
        def __init__(self, name):
            self._name = name
            self._exists = True

        def exists(self):
            return self._exists

        @property
        def name(self):
            return self._name

    manager = emoji_manager_new.EmojiManager()
    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_name = "ok.png"
    emoji.full_path = _DummyPath("ok.png")
    emoji.description = "desc"
    manager.emojis = [emoji]
    manager._emoji_num = 1

    called = {"count": 0}

    def _delete(_emoji, no_desc=False):
        called["count"] += 1
        return True

    monkeypatch.setattr(manager, "delete_emoji", _delete)

    manager.check_emoji_file_integrity()

    assert manager.emojis == [emoji]
    assert manager._emoji_num == 1
    assert called["count"] == 0
    assert logger.warning_calls == []
    assert any("完整性检查完成" in m for m in _messages(logger.info_calls))


def test_check_emoji_file_integrity_removes_invalid_records(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _DummyPath:
        def __init__(self, name, exists=True):
            self._name = name
            self._exists = exists

        def exists(self):
            return self._exists

        @property
        def name(self):
            return self._name

    manager = emoji_manager_new.EmojiManager()
    missing_file = emoji_manager_new.MaiEmoji()
    missing_file.file_name = "missing.png"
    missing_file.full_path = _DummyPath("missing.png", exists=False)
    missing_file.description = "desc"

    missing_desc = emoji_manager_new.MaiEmoji()
    missing_desc.file_name = "nodesc.png"
    missing_desc.full_path = _DummyPath("nodesc.png", exists=True)
    missing_desc.description = None

    manager.emojis = [missing_file, missing_desc]
    manager._emoji_num = 2

    deleted = []

    def _delete(emoji, no_desc=False):
        deleted.append((emoji.file_name, no_desc))
        return True

    monkeypatch.setattr(manager, "delete_emoji", _delete)

    manager.check_emoji_file_integrity()

    assert manager.emojis == []
    assert manager._emoji_num == 0
    assert set(deleted) == {("missing.png", False), ("nodesc.png", True)}
    messages = _messages(logger.warning_calls)
    assert any("文件缺失" in m for m in messages)
    assert any("缺失描述" in m for m in messages)
    assert any("成功删除缺失文件的表情包记录" in m for m in _messages(logger.info_calls))
    assert any("删除了 2 条记录" in m for m in _messages(logger.info_calls))


def test_check_emoji_file_integrity_delete_failed(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    class _DummyPath:
        def __init__(self, name):
            self._name = name
            self._exists = False

        def exists(self):
            return self._exists

        @property
        def name(self):
            return self._name

    manager = emoji_manager_new.EmojiManager()
    emoji = emoji_manager_new.MaiEmoji()
    emoji.file_name = "bad.png"
    emoji.full_path = _DummyPath("bad.png")
    emoji.description = "desc"
    manager.emojis = [emoji]
    manager._emoji_num = 1

    def _delete(_emoji, no_desc=False):
        return False

    monkeypatch.setattr(manager, "delete_emoji", _delete)

    manager.check_emoji_file_integrity()

    assert manager.emojis == [emoji]
    assert manager._emoji_num == 1
    assert any("表情包文件缺失" in m for m in _messages(logger.warning_calls))
    assert any("删除缺失文件的表情包记录失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_build_emoji_emotion_two_items_no_sample(monkeypatch):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger

    async def _generate_response_async(*_args, **_kwargs):
        return "a, b", None

    monkeypatch.setattr(
        emoji_manager_new.emoji_manager_emotion_judge_llm,
        "generate_response_async",
        _generate_response_async,
    )

    emoji = emoji_manager_new.MaiEmoji()
    emoji.description = "desc"

    result, updated = await emoji_manager_new.EmojiManager().build_emoji_emotion(emoji)

    assert result is True
    assert updated.emotion == ["a", "b"]
    assert any("成功为表情包构建情感标签" in m for m in _messages(logger.info_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_file_missing(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    missing_file = tmp_path / "missing.png"

    result = await manager.register_emoji_by_filename(missing_file)

    assert result is False
    assert any("表情包文件不存在" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_create_object_error(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    file_path = tmp_path / "ok.png"
    file_path.write_bytes(b"")

    class _BadEmoji:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("create failed")

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _BadEmoji)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is False
    assert any("创建表情包对象时出错" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_hash_format_failed(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    file_path = tmp_path / "hash.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            return False

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _statement):
            return self

        def first(self):
            return None

    class _Select:
        def __init__(self) -> None:
            pass

        def filter_by(self, **_kwargs):
            return self

        def limit(self, _num):
            return self

    def _get_db_session_manual():
        return _Session()

    def _get_db_session():
        return _Session()

    monkeypatch.setattr(emoji_manager_new, "get_db_session_manual", _get_db_session_manual)
    monkeypatch.setattr(emoji_manager_new, "get_db_session", _get_db_session)
    monkeypatch.setattr(emoji_manager_new, "select", lambda _model: _Select())
    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is False
    assert any("计算表情包哈希值和格式失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_duplicate_hash(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    file_path = tmp_path / "dup.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            self.file_hash = "hash-dup"
            self.full_path = file_path
            return True

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    existing = emoji_manager_new.MaiEmoji()
    existing.file_name = "exist.png"
    monkeypatch.setattr(manager, "get_emoji_by_hash", lambda _h: existing)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is False
    assert any("表情包已存在" in m for m in _messages(logger.warning_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_build_description_failed(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    file_path = tmp_path / "desc.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            self.file_hash = "hash-desc"
            self.full_path = file_path
            return True

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    async def _build_desc(_e):
        return False, _e

    monkeypatch.setattr(manager, "build_emoji_description", _build_desc)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is False
    assert any("构建表情包描述失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_build_emotion_failed(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()

    file_path = tmp_path / "emo.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            self.file_hash = "hash-emo"
            self.full_path = file_path
            return True

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    async def _build_desc(_e):
        return True, _e

    async def _build_emo(_e):
        return False, _e

    monkeypatch.setattr(manager, "build_emoji_description", _build_desc)
    monkeypatch.setattr(manager, "build_emoji_emotion", _build_emo)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is False
    assert any("构建表情包情感标签失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_capacity_replace_failed(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager._emoji_num = 1
    emoji_manager_new.global_config.emoji.max_reg_num = 1
    emoji_manager_new.global_config.emoji.do_replace = True

    file_path = tmp_path / "full.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            self.file_hash = "hash-full"
            self.full_path = file_path
            return True

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    async def _build_desc(_e):
        return True, _e

    async def _build_emo(_e):
        return True, _e

    async def _replace(_e):
        return False

    monkeypatch.setattr(manager, "build_emoji_description", _build_desc)
    monkeypatch.setattr(manager, "build_emoji_emotion", _build_emo)
    monkeypatch.setattr(manager, "replace_an_emoji_by_llm", _replace)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is False
    assert any("数量已达上限" in m for m in _messages(logger.warning_calls))
    assert any("替换表情包失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_capacity_replace_success(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager._emoji_num = 1
    emoji_manager_new.global_config.emoji.max_reg_num = 1
    emoji_manager_new.global_config.emoji.do_replace = True

    file_path = tmp_path / "full-ok.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            self.file_hash = "hash-full-ok"
            self.full_path = file_path
            return True

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    async def _build_desc(_e):
        return True, _e

    async def _build_emo(_e):
        return True, _e

    async def _replace(_e):
        return True

    monkeypatch.setattr(manager, "build_emoji_description", _build_desc)
    monkeypatch.setattr(manager, "build_emoji_emotion", _build_emo)
    monkeypatch.setattr(manager, "replace_an_emoji_by_llm", _replace)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is True
    assert any("数量已达上限" in m for m in _messages(logger.warning_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_register_db_failed(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager._emoji_num = 0
    emoji_manager_new.global_config.emoji.max_reg_num = 10

    file_path = tmp_path / "db-fail.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            self.file_hash = "hash-db-fail"
            self.full_path = file_path
            return True

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    async def _build_desc(_e):
        return True, _e

    async def _build_emo(_e):
        return True, _e

    monkeypatch.setattr(manager, "build_emoji_description", _build_desc)
    monkeypatch.setattr(manager, "build_emoji_emotion", _build_emo)
    monkeypatch.setattr(manager, "register_emoji_to_db", lambda _e: False)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is False
    assert any("注册表情包到数据库失败" in m for m in _messages(logger.error_calls))


@pytest.mark.asyncio
async def test_register_emoji_by_filename_register_db_success(monkeypatch, tmp_path):
    emoji_manager_new = import_emoji_manager_new(monkeypatch)
    logger = emoji_manager_new.logger
    manager = emoji_manager_new.EmojiManager()
    manager._emoji_num = 0
    emoji_manager_new.global_config.emoji.max_reg_num = 10

    file_path = tmp_path / "db-ok.png"
    file_path.write_bytes(b"")

    class _Emoji(emoji_manager_new.MaiEmoji):
        async def calculate_hash_format(self):
            self.file_hash = "hash-db-ok"
            self.full_path = file_path
            self.file_name = "db-ok.png"
            return True

    monkeypatch.setattr(emoji_manager_new, "MaiEmoji", _Emoji)

    async def _build_desc(_e):
        return True, _e

    async def _build_emo(_e):
        return True, _e

    monkeypatch.setattr(manager, "build_emoji_description", _build_desc)
    monkeypatch.setattr(manager, "build_emoji_emotion", _build_emo)
    monkeypatch.setattr(manager, "register_emoji_to_db", lambda _e: True)

    result = await manager.register_emoji_by_filename(file_path)

    assert result is True
    assert manager._emoji_num == 1
    assert len(manager.emojis) == 1
    assert any("成功注册新表情包" in m for m in _messages(logger.info_calls))
