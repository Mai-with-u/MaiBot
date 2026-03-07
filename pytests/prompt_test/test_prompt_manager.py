# File: pytests/prompt_test/test_prompt_manager.py

import asyncio
import inspect
from pathlib import Path
from typing import Any
import sys

import pytest

PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.absolute().resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "config"))

from src.prompt.prompt_manager import (  # noqa
    SUFFIX_PROMPT,
    Prompt,
    PromptManager,
    prompt_manager,
)


# ========= Prompt 基础行为 =========


@pytest.mark.parametrize(
    "prompt_name, template",
    [
        pytest.param("simple", "Hello {name}", id="simple-template-with-field"),
        pytest.param("no-fields", "Just a static template", id="template-without-fields"),
        pytest.param(
            "brace-escaping",
            "Use {{ and }} around {field}",
            id="template-with-escaped-braces",
        ),
    ],
)
def test_prompt_init_happy_paths(prompt_name: str, template: str):
    # Act
    prompt = Prompt(prompt_name=prompt_name, template=template)

    # Assert
    assert prompt.prompt_name == prompt_name
    assert prompt.template == template


@pytest.mark.parametrize(
    "prompt_name, template, expected_exception, expected_msg_substring",
    [
        pytest.param("", "Hello {name}", ValueError, "prompt_name 不能为空", id="empty-prompt-name"),
        pytest.param("valid-name", "", ValueError, "template 不能为空", id="empty-template"),
        pytest.param(
            "unnamed-placeholder",
            "Hello {}",
            ValueError,
            "模板中不允许使用未命名的占位符",
            id="unnamed-placeholder-not-allowed",
        ),
        pytest.param(
            "unnamed-placeholder-with-escaped-brace",
            "Value {{}} and {}",
            ValueError,
            "模板中不允许使用未命名的占位符",
            id="unnamed-placeholder-mixed-with-escaped",
        ),
    ],
)
def test_prompt_init_error_cases(
    prompt_name,
    template,
    expected_exception,
    expected_msg_substring,
):
    # Act / Assert
    with pytest.raises(expected_exception) as exc_info:
        Prompt(prompt_name=prompt_name, template=template)

    # Assert
    assert expected_msg_substring in str(exc_info.value)


@pytest.mark.parametrize(
    "initial_context, name, func, expected_value, expected_exception, expected_msg_substring, case_id",
    [
        (
            {},
            "const_str",
            "constant",
            "constant",
            None,
            None,
            "add-context-from-string-creates-wrapper",
        ),
        (
            {},
            "callable_str",
            lambda prompt_name: f"hello-{prompt_name}",
            "hello-my_prompt",
            None,
            None,
            "add-context-from-callable",
        ),
        (
            {"dup": lambda _: "x"},
            "dup",
            "y",
            None,
            KeyError,
            "Context function name 'dup' 已存在于 Prompt 'my_prompt' 中",
            "add-context-duplicate-key-error",
        ),
    ],
)
def test_prompt_add_context(
    initial_context,
    name,
    func,
    expected_value,
    expected_exception,
    expected_msg_substring,
    case_id,
):
    # Arrange
    prompt = Prompt(prompt_name="my_prompt", template="template")
    prompt.prompt_render_context = dict(initial_context)

    # Act
    if expected_exception:
        with pytest.raises(expected_exception) as exc_info:
            prompt.add_context(name, func)

        # Assert
        assert expected_msg_substring in str(exc_info.value)
    else:
        prompt.add_context(name, func)

        # Assert
        assert name in prompt.prompt_render_context
        result = prompt.prompt_render_context[name]("my_prompt")
        assert result == expected_value


def test_prompt_clone_independent_instance():
    # Arrange
    prompt = Prompt(prompt_name="p", template="T {x}")
    prompt.add_context("x", "X")

    # Act
    cloned = prompt.clone()

    # Assert
    assert cloned is not prompt
    assert cloned.prompt_name == prompt.prompt_name
    assert cloned.template == prompt.template
    # 当前实现 clone 不复制 context
    assert cloned.prompt_render_context == {}


# ========= PromptManager：添加/获取/删除/替换 =========


def test_prompt_manager_add_prompt_happy_and_error():
    # Arrange
    manager = PromptManager()
    prompt1 = Prompt(prompt_name="p1", template="T1")
    manager.add_prompt(prompt1, need_save=True)

    # Act
    prompt2 = Prompt(prompt_name="p2", template="T2")
    manager.add_prompt(prompt2, need_save=False)

    # Assert
    assert "p1" in manager._prompt_to_save
    assert "p2" not in manager._prompt_to_save

    # Arrange
    prompt_dup = Prompt(prompt_name="p1", template="T-dup")

    # Act / Assert
    with pytest.raises(KeyError) as exc_info:
        manager.add_prompt(prompt_dup)

    # Assert
    assert "Prompt name 'p1' 已存在" in str(exc_info.value)


def test_prompt_manager_remove_prompt_happy_and_error():
    # Arrange
    manager = PromptManager()
    p1 = Prompt(prompt_name="p1", template="T")
    manager.add_prompt(p1, need_save=True)

    # Act
    manager.remove_prompt("p1")

    # Assert
    assert "p1" not in manager.prompts
    assert "p1" not in manager._prompt_to_save

    # Act / Assert
    with pytest.raises(KeyError) as exc_info:
        manager.remove_prompt("no_such")

    assert "Prompt name 'no_such' 不存在" in str(exc_info.value)


def test_prompt_manager_replace_prompt_happy_and_error():
    # sourcery skip: extract-duplicate-method
    # Arrange
    manager = PromptManager()
    p1 = Prompt(prompt_name="p", template="Old")
    manager.add_prompt(p1, need_save=True)

    p_new = Prompt(prompt_name="p", template="New")

    # Act: 替换且保持 need_save
    manager.replace_prompt(p_new, need_save=True)

    # Assert
    assert manager.prompts["p"].template == "New"
    assert "p" in manager._prompt_to_save

    # Act: 再次替换，且不需要保存
    p_new2 = Prompt(prompt_name="p", template="New2")
    manager.replace_prompt(p_new2, need_save=False)

    # Assert
    assert manager.prompts["p"].template == "New2"
    assert "p" not in manager._prompt_to_save

    # Error: 不存在的 prompt
    p_unknown = Prompt(prompt_name="unknown", template="T")
    with pytest.raises(KeyError) as exc_info:
        manager.replace_prompt(p_unknown)

    assert "Prompt name 'unknown' 不存在，无法替换" in str(exc_info.value)


def test_prompt_manager_get_prompt_is_copy():
    # Arrange
    manager = PromptManager()
    prompt = Prompt(prompt_name="original", template="T")
    manager.add_prompt(prompt)

    # Act
    retrieved_prompt = manager.get_prompt("original")

    # Assert
    assert retrieved_prompt is not prompt
    assert retrieved_prompt.prompt_name == prompt.prompt_name
    assert retrieved_prompt.template == prompt.template
    assert retrieved_prompt.prompt_render_context == prompt.prompt_render_context


def test_prompt_manager_add_prompt_conflict_with_context_name():
    # Arrange
    manager = PromptManager()
    manager.add_context_construct_function("ctx_name", lambda _: "value")
    prompt_conflict = Prompt(prompt_name="ctx_name", template="T")

    # Act / Assert
    with pytest.raises(KeyError) as exc_info:
        manager.add_prompt(prompt_conflict)

    # Assert
    assert "Prompt name 'ctx_name' 已存在" in str(exc_info.value)


def test_prompt_manager_add_context_construct_function_happy():
    # Arrange
    manager = PromptManager()

    def ctx_func(prompt_name: str) -> str:
        return f"ctx-{prompt_name}"

    # Act
    manager.add_context_construct_function("ctx", ctx_func)

    # Assert
    assert "ctx" in manager._context_construct_functions
    stored_func, module = manager._context_construct_functions["ctx"]
    assert stored_func is ctx_func
    assert module == __name__


def test_prompt_manager_add_context_construct_function_duplicate():
    # Arrange
    manager = PromptManager()

    def f(_):
        return "x"

    manager.add_context_construct_function("dup", f)
    manager.add_prompt(Prompt(prompt_name="dup_prompt", template="T"))

    # Act / Assert
    with pytest.raises(KeyError) as exc_info1:
        manager.add_context_construct_function("dup", f)

    # Assert
    assert "Construct function name 'dup' 已存在" in str(exc_info1.value)

    # Act / Assert
    with pytest.raises(KeyError) as exc_info2:
        manager.add_context_construct_function("dup_prompt", f)

    # Assert
    assert "Construct function name 'dup_prompt' 已存在" in str(exc_info2.value)


def test_prompt_manager_get_prompt_not_exist():
    # Arrange
    manager = PromptManager()

    # Act / Assert
    with pytest.raises(KeyError) as exc_info:
        manager.get_prompt("no_such_prompt")

    # Assert
    assert "Prompt name 'no_such_prompt' 不存在" in str(exc_info.value)


# ========= 渲染逻辑 =========


@pytest.mark.parametrize(
    "template, inner_context, global_context, expected, case_id",
    [
        pytest.param(
            "Hello {name}",
            {"name": lambda p: f"name-for-{p}"},
            {},
            "Hello name-for-main",
            "render-with-inner-context",
        ),
        pytest.param(
            "Global {block}",
            {},
            {"block": lambda p: f"block-{p}"},
            "Global block-main",
            "render-with-global-context",
        ),
        pytest.param(
            "Mix {inner} and {global}",
            {"inner": lambda p: f"inner-{p}"},
            {"global": lambda p: f"global-{p}"},
            "Mix inner-main and global-main",
            "render-with-inner-and-global-context",
        ),
        pytest.param(
            "Escaped {{ and }} and {field}",
            {"field": lambda _: "X"},
            {},
            "Escaped { and } and X",
            "render-with-escaped-braces",
        ),
    ],
)
@pytest.mark.asyncio
async def test_prompt_manager_render_contexts(
    template,
    inner_context,
    global_context,
    expected,
    case_id,
):
    # Arrange
    manager = PromptManager()
    tmp_prompt = Prompt(prompt_name="main", template=template)
    manager.add_prompt(tmp_prompt)
    prompt = manager.get_prompt("main")
    for name, fn in inner_context.items():
        prompt.add_context(name, fn)
    for name, fn in global_context.items():
        manager.add_context_construct_function(name, fn)

    # Act
    rendered = await manager.render_prompt(prompt)

    # Assert
    assert rendered == expected


@pytest.mark.asyncio
async def test_prompt_manager_render_nested_prompts():
    # Arrange
    manager = PromptManager()
    p1 = Prompt(prompt_name="p1", template="P1-{x}")
    p2 = Prompt(prompt_name="p2", template="P2-{p1}")
    p3_tmp = Prompt(prompt_name="p3", template="{p2}-end")
    manager.add_prompt(p1)
    manager.add_prompt(p2)
    manager.add_prompt(p3_tmp)
    p3 = manager.get_prompt("p3")
    p3.add_context("x", lambda _: "X")

    # Act
    rendered = await manager.render_prompt(p3)

    # Assert
    assert rendered == "P2-P1-X-end"


@pytest.mark.asyncio
async def test_prompt_manager_render_recursive_limit():
    # Arrange
    manager = PromptManager()
    p1_tmp = Prompt(prompt_name="p1", template="{p2}")
    p2_tmp = Prompt(prompt_name="p2", template="{p1}")
    manager.add_prompt(p1_tmp)
    manager.add_prompt(p2_tmp)
    p1 = manager.get_prompt("p1")

    # Act / Assert
    with pytest.raises(RecursionError) as exc_info:
        await manager.render_prompt(p1)

    # Assert
    assert "递归层级过深" in str(exc_info.value)


@pytest.mark.asyncio
async def test_prompt_manager_render_missing_field_error():
    # Arrange
    manager = PromptManager()
    tmp_prompt = Prompt(prompt_name="main", template="Hello {missing}")
    manager.add_prompt(tmp_prompt)
    prompt = manager.get_prompt("main")

    # Act / Assert
    with pytest.raises(KeyError) as exc_info:
        await manager.render_prompt(prompt)

    # Assert
    assert "Prompt 'main' 中缺少必要的内容块或构建函数: 'missing'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_prompt_manager_render_prefers_inner_context_over_global():
    # Arrange
    manager = PromptManager()
    tmp_prompt = Prompt(prompt_name="main", template="{field}")
    manager.add_context_construct_function("field", lambda _: "global")
    manager.add_prompt(tmp_prompt)
    prompt = manager.get_prompt("main")
    prompt.add_context("field", lambda _: "inner")

    # Act
    rendered = await manager.render_prompt(prompt)

    # Assert
    assert rendered == "inner"


@pytest.mark.asyncio
async def test_prompt_manager_render_with_coroutine_context_function():
    # Arrange
    manager = PromptManager()

    async def async_inner(prompt_name: str) -> str:
        await asyncio.sleep(0)
        return f"async-{prompt_name}"

    tmp_prompt = Prompt(prompt_name="main", template="{inner}")
    manager.add_prompt(tmp_prompt)
    prompt = manager.get_prompt("main")
    prompt.add_context("inner", async_inner)

    # Act
    rendered = await manager.render_prompt(prompt)

    # Assert
    assert rendered == "async-main"


@pytest.mark.asyncio
async def test_prompt_manager_render_with_coroutine_global_context_function():
    # Arrange
    manager = PromptManager()

    async def async_global(prompt_name: str) -> str:
        await asyncio.sleep(0)
        return f"g-{prompt_name}"

    tmp_prompt = Prompt(prompt_name="main", template="{g}")
    manager.add_context_construct_function("g", async_global)
    manager.add_prompt(tmp_prompt)
    prompt = manager.get_prompt("main")

    # Act
    rendered = await manager.render_prompt(prompt)

    # Assert
    assert rendered == "g-main"


@pytest.mark.asyncio
async def test_prompt_manager_render_only_cloned_instance():
    # Arrange
    manager = PromptManager()
    p = Prompt(prompt_name="p", template="T")
    manager.add_prompt(p)

    # Act / Assert: 直接用原始 p 渲染会报错
    with pytest.raises(ValueError) as exc_info:
        await manager.render_prompt(p)

    assert "只能渲染通过 PromptManager.get_prompt 方法获取的 Prompt 实例" in str(exc_info.value)


@pytest.mark.parametrize(
    "is_prompt_context, use_coroutine, case_id",
    [
        pytest.param(True, False, "prompt-context-sync-error"),
        pytest.param(False, False, "global-context-sync-error"),
        pytest.param(True, True, "prompt-context-async-error"),
        pytest.param(False, True, "global-context-async-error"),
    ],
)
@pytest.mark.asyncio
async def test_prompt_manager_get_function_result_error_logging(
    monkeypatch,
    is_prompt_context,
    use_coroutine,
    case_id,
):
    # Arrange
    manager = PromptManager()

    class DummyError(Exception):
        pass

    def sync_func(_name: str) -> str:
        raise DummyError("sync-error")

    async def async_func(_name: str) -> str:
        await asyncio.sleep(0)
        raise DummyError("async-error")

    func = async_func if use_coroutine else sync_func
    logged_messages: list[str] = []

    def fake_error(msg: Any) -> None:
        logged_messages.append(str(msg))

    fake_logger = type("FakeLogger", (), {"error": staticmethod(fake_error)})

    monkeypatch.setattr("src.prompt.prompt_manager.logger", fake_logger)

    # Act / Assert
    with pytest.raises(DummyError):
        await manager._get_function_result(
            func=func,
            prompt_name="P",
            field_name="field",
            is_prompt_context=is_prompt_context,
            module="mod",
        )

    # Assert
    assert logged_messages
    log = logged_messages[0]
    if is_prompt_context:
        assert "调用 Prompt 'P' 内部上下文构造函数 'field' 时出错" in log
    else:
        assert "调用上下文构造函数 'field' 时出错，所属模块: 'mod'" in log


# ========= add_context_construct_function 边界 =========


def test_prompt_manager_add_context_construct_function_unknown_frame(monkeypatch):
    # Arrange
    manager = PromptManager()

    def fake_currentframe() -> None:
        return None

    monkeypatch.setattr("inspect.currentframe", fake_currentframe)

    def f(_):
        return "x"

    # Act / Assert
    with pytest.raises(RuntimeError) as exc_info:
        manager.add_context_construct_function("x", f)

    # Assert
    assert "无法获取调用栈" in str(exc_info.value)


def test_prompt_manager_add_context_construct_function_unknown_caller_frame(monkeypatch):
    # Arrange
    manager = PromptManager()
    real_currentframe = inspect.currentframe

    class FakeFrame:
        f_back = None

    def fake_currentframe():
        return FakeFrame()

    monkeypatch.setattr("inspect.currentframe", fake_currentframe)

    def f(_):
        return "x"

    # Act / Assert
    with pytest.raises(RuntimeError) as exc_info:
        manager.add_context_construct_function("x", f)

    # Assert
    assert "无法获取调用栈的上一级" in str(exc_info.value)

    # Cleanup
    monkeypatch.setattr("inspect.currentframe", real_currentframe)


# ========= save/load & 目录逻辑 =========


def test_prompt_manager_save_prompts_io_error_on_unlink(tmp_path, monkeypatch):
    """
    save_prompts 现在的逻辑：
    1. 先删除 CUSTOM_PROMPTS_DIR 下的所有 *.prompt 文件；
    2. 再将 _prompt_to_save 中的 prompt 写入 CUSTOM_PROMPTS_DIR。

    这里模拟删除已有自定义 prompt 文件时发生 IO 错误。
    """
    # Arrange
    prompts_dir = tmp_path / "prompts"
    custom_dir = tmp_path / "data" / "custom_prompts"
    prompts_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_dir, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_dir, raising=False)

    # 先在自定义目录写入一个 prompt 文件，触发 unlink 路径
    old_file = custom_dir / f"old{SUFFIX_PROMPT}"
    old_file.write_text("old", encoding="utf-8")

    manager = PromptManager()
    p1 = Prompt(prompt_name="save_error", template="T")
    manager.add_prompt(p1, need_save=True)

    # 打桩 Path.unlink，使删除文件时报错
    def fake_unlink(self):
        raise OSError("disk unlink error")

    monkeypatch.setattr("pathlib.Path.unlink", fake_unlink)

    # Act / Assert
    with pytest.raises(OSError) as exc_info:
        manager.save_prompts()

    # Assert
    assert "disk unlink error" in str(exc_info.value)


def test_prompt_manager_save_prompts_io_error_on_write(tmp_path, monkeypatch):
    """
    模拟 save_prompts 在写入新 prompt 文件时发生 IO 错误。
    """
    # Arrange
    prompts_dir = tmp_path / "prompts"
    custom_dir = tmp_path / "data" / "custom_prompts"
    prompts_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_dir, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_dir, raising=False)

    manager = PromptManager()
    p1 = Prompt(prompt_name="save_error", template="T")
    manager.add_prompt(p1, need_save=True)

    class FakeFile:
        def __enter__(self):
            raise OSError("disk write error")

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_open(*_args, **_kwargs):
        return FakeFile()

    monkeypatch.setattr("builtins.open", fake_open)

    # Act / Assert
    with pytest.raises(OSError) as exc_info:
        manager.save_prompts()

    # Assert
    assert "disk write error" in str(exc_info.value)


def test_prompt_manager_load_prompts_io_error_from_default_dir(tmp_path, monkeypatch):
    """
    模拟从 PROMPTS_DIR 读取 prompt 时发生 IO 错误。
    """
    # Arrange
    prompts_dir = tmp_path / "prompts"
    custom_dir = tmp_path / "data" / "custom_prompts"
    prompts_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_dir, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_dir, raising=False)

    prompt_file = prompts_dir / f"bad{SUFFIX_PROMPT}"
    prompt_file.write_text("content", encoding="utf-8")

    class FakeFile:
        def __enter__(self):
            raise OSError("read error")

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_open(*args, **kwargs):
        # 只对 default 目录下的文件触发错误，其余正常（如果有）
        file_path = Path(args[0])
        if file_path == prompt_file:
            return FakeFile()
        return open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    manager = PromptManager()

    # Act / Assert
    with pytest.raises(OSError) as exc_info:
        manager.load_prompts()

    # Assert
    assert "read error" in str(exc_info.value)


def test_prompt_manager_load_prompts_io_error_from_custom_dir(tmp_path, monkeypatch):
    """
    模拟从 CUSTOM_PROMPTS_DIR 读取 prompt 时发生 IO 错误。
    包含两种路径：
    1. default 与 custom 同名，load_prompts 会优先读取 custom；
    2. 仅 custom 有文件，且 default 无同名文件。
    """
    # Arrange
    prompts_dir = tmp_path / "prompts"
    custom_dir = tmp_path / "data" / "custom_prompts"
    prompts_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_dir, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_dir, raising=False)

    # default 与 custom 同名的文件
    same_name = f"same{SUFFIX_PROMPT}"
    base_file = prompts_dir / same_name
    base_file.write_text("base", encoding="utf-8")
    custom_file_same = custom_dir / same_name
    custom_file_same.write_text("custom", encoding="utf-8")

    # 仅 custom 下存在的文件
    only_custom_file = custom_dir / f"only_custom{SUFFIX_PROMPT}"
    only_custom_file.write_text("only", encoding="utf-8")

    class FakeFile:
        def __enter__(self):
            raise OSError("custom read error")

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_open(*args, **kwargs):
        file_path = Path(args[0])
        # 对 custom 目录下的 prompt 文件统一触发错误
        if file_path.parent == custom_dir:
            return FakeFile()
        return open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    manager = PromptManager()

    # Act / Assert
    with pytest.raises(OSError) as exc_info:
        manager.load_prompts()

    # Assert
    assert "custom read error" in str(exc_info.value)


def test_prompt_manager_load_prompts_custom_overrides_default(tmp_path, monkeypatch):
    """
    load_prompts 逻辑：
    - 遍历 PROMPTS_DIR/*.prompt
    - 如果 CUSTOM_PROMPTS_DIR 下存在同名文件，则优先使用自定义目录
    """
    # Arrange
    prompts_dir = tmp_path / "prompts"
    custom_dir = tmp_path / "data" / "custom_prompts"
    prompts_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_dir, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_dir, raising=False)

    # 默认目录 prompt
    base_file = prompts_dir / f"testp{SUFFIX_PROMPT}"
    base_file.write_text("BaseTemplate {x}", encoding="utf-8")

    # 自定义目录同名 prompt，应当覆盖默认
    custom_file = custom_dir / base_file.name
    custom_file.write_text("CustomTemplate {x}", encoding="utf-8")

    manager = PromptManager()

    # Act
    manager.load_prompts()

    # Assert
    p = manager.get_prompt("testp")
    assert p.template == "CustomTemplate {x}"
    # 从自定义目录加载的 prompt 应标记为 need_save（加入 _prompt_to_save）
    assert "testp" in manager._prompt_to_save


def test_prompt_manager_load_prompts_default_dir_not_mark_need_save(tmp_path, monkeypatch):
    """
    从 PROMPTS_DIR 加载、且没有同名自定义 prompt 时，need_save 应为 False（不进入 _prompt_to_save）。
    """
    # Arrange
    prompts_dir = tmp_path / "prompts"
    custom_dir = tmp_path / "data" / "custom_prompts"
    prompts_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_dir, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_dir, raising=False)

    # 仅默认目录有 prompt，自定义目录中无同名文件
    base_file = prompts_dir / f"only_default{SUFFIX_PROMPT}"
    base_file.write_text("DefaultTemplate {x}", encoding="utf-8")

    manager = PromptManager()

    # Act
    manager.load_prompts()

    # Assert
    p = manager.get_prompt("only_default")
    assert p.template == "DefaultTemplate {x}"
    # 从默认目录加载的 prompt 不应标记为 need_save
    assert "only_default" not in manager._prompt_to_save


def test_prompt_manager_save_prompts_use_custom_dir(tmp_path, monkeypatch):
    """
    save_prompts 使用 CUSTOM_PROMPTS_DIR 进行保存。
    """
    prompts_dir = tmp_path / "prompts"
    custom_dir = tmp_path / "data" / "custom_prompts"
    prompts_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_dir, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_dir, raising=False)

    manager = PromptManager()
    p1 = Prompt(prompt_name="save_me", template="Template {x}")
    p1.add_context("x", "X")
    manager.add_prompt(p1, need_save=True)

    # Act
    manager.save_prompts()

    # Assert: 文件应保存在 custom_dir 中
    saved_file = custom_dir / f"save_me{SUFFIX_PROMPT}"
    assert saved_file.exists()
    assert saved_file.read_text(encoding="utf-8") == "Template {x}"


# ========= 其它 =========


def test_prompt_manager_global_instance_access():
    # Act
    pm = prompt_manager

    # Assert
    assert isinstance(pm, PromptManager)


def test_formatter_parsing_named_fields_only():
    # Arrange
    manager = PromptManager()
    prompt = Prompt(prompt_name="main", template="A {x} B {y} C")
    manager.add_prompt(prompt)

    # Act
    fields = {field_name for _, field_name, _, _ in manager._formatter.parse(prompt.template) if field_name}

    # Assert
    assert fields == {"x", "y"}
