from __future__ import annotations

from pathlib import Path

import pytest

from src.common.i18n import set_locale
from src.common.prompt_i18n import (
    PROMPTS_ROOT,
    clear_prompt_cache,
    extract_prompt_placeholders,
    iter_prompt_files,
    list_prompt_templates,
    load_prompt,
)
from src.prompt.prompt_manager import PromptManager


@pytest.fixture(autouse=True)
def clear_prompt_i18n_cache() -> None:
    set_locale("zh-CN")
    clear_prompt_cache()
    yield
    clear_prompt_cache()
    set_locale("zh-CN")


def write_prompt(prompt_dir: Path, locale: str | None, name: str, content: str) -> None:
    base_dir = prompt_dir if locale is None else prompt_dir / locale
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / f"{name}.prompt").write_text(content, encoding="utf-8")


def test_load_prompt_prefers_requested_locale(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "你好，{user_name}")
    write_prompt(prompts_root, "en-US", "replyer", "Hello, {user_name}")

    rendered = load_prompt("replyer", locale="en-US", prompts_root=prompts_root, user_name="Mai")

    assert rendered == "Hello, Mai"


def test_load_prompt_falls_back_to_default_locale(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "你好，{user_name}")

    rendered = load_prompt("replyer", locale="en-US", prompts_root=prompts_root, user_name="Mai")

    assert rendered == "你好，Mai"


def test_load_prompt_does_not_fall_back_to_legacy_root(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, None, "replyer", "Legacy {user_name}")

    with pytest.raises(FileNotFoundError):
        load_prompt("replyer", locale="en-US", prompts_root=prompts_root, user_name="Mai")


def test_load_prompt_with_category_falls_back_to_default_locale_root(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "你好，{user_name}")

    rendered = load_prompt("replyer", locale="en-US", category="chat", prompts_root=prompts_root, user_name="Mai")

    assert rendered == "你好，Mai"


def test_load_prompt_prefers_custom_prompt_override(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    custom_prompts_root = tmp_path / "data" / "custom_prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "Base {user_name}")
    write_prompt(custom_prompts_root, "zh-CN", "replyer", "Custom {user_name}")

    rendered = load_prompt(
        "replyer",
        locale="zh-CN",
        prompts_root=prompts_root,
        custom_prompts_root=custom_prompts_root,
        user_name="Mai",
    )

    assert rendered == "Custom Mai"


def test_load_prompt_prefers_custom_prompt_requested_locale(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    custom_prompts_root = tmp_path / "data" / "custom_prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "Base zh {user_name}")
    write_prompt(prompts_root, "en-US", "replyer", "Base en {user_name}")
    write_prompt(custom_prompts_root, "zh-CN", "replyer", "Custom zh {user_name}")
    write_prompt(custom_prompts_root, "en-US", "replyer", "Custom en {user_name}")

    rendered = load_prompt(
        "replyer",
        locale="en-US",
        prompts_root=prompts_root,
        custom_prompts_root=custom_prompts_root,
        user_name="Mai",
    )

    assert rendered == "Custom en Mai"


def test_load_prompt_uses_requested_locale_source_before_default_custom(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    custom_prompts_root = tmp_path / "data" / "custom_prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "Base zh {user_name}")
    write_prompt(prompts_root, "en-US", "replyer", "Base en {user_name}")
    write_prompt(custom_prompts_root, "zh-CN", "replyer", "Custom zh {user_name}")

    rendered = load_prompt(
        "replyer",
        locale="en-US",
        prompts_root=prompts_root,
        custom_prompts_root=custom_prompts_root,
        user_name="Mai",
    )

    assert rendered == "Base en Mai"


def test_load_prompt_strict_mode_raises_on_missing_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "你好，{user_name}，现在是 {current_time}")
    monkeypatch.setenv("MAIBOT_PROMPT_I18N_STRICT", "1")

    with pytest.raises(KeyError) as exc_info:
        load_prompt("replyer", locale="zh-CN", prompts_root=prompts_root, user_name="Mai")

    assert "current_time" in str(exc_info.value)


def test_load_prompt_rejects_path_traversal(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "你好")

    with pytest.raises(ValueError):
        load_prompt("../replyer", locale="zh-CN", prompts_root=prompts_root)


def test_list_prompt_templates_prefers_locale_specific_files(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "中文")
    write_prompt(prompts_root, "en-US", "replyer", "English")
    set_locale("en-US")

    prompt_templates = list_prompt_templates(prompts_root=prompts_root)

    assert prompt_templates["replyer"].path.read_text(encoding="utf-8") == "English"


def test_list_prompt_templates_loads_directory_metadata(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "中文")
    metadata_path = prompts_root / "zh-CN" / ".meta.toml"
    metadata_path.write_text(
        """
[replyer]
display_name = "回复器"
advanced = true
description = "用于生成回复的主模板"
""".strip(),
        encoding="utf-8",
    )

    prompt_templates = list_prompt_templates(prompts_root=prompts_root)
    metadata = prompt_templates["replyer"].metadata

    assert metadata.display_name == "回复器"
    assert metadata.advanced is True
    assert metadata.description == "用于生成回复的主模板"


def test_list_prompt_templates_loads_prompt_specific_metadata(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    write_prompt(prompts_root, "zh-CN", "replyer", "中文")
    metadata_path = prompts_root / "zh-CN" / "replyer.meta.json"
    metadata_path.write_text(
        '{"display_name": "Replyer", "advanced": false, "description": "Prompt specific metadata"}',
        encoding="utf-8",
    )

    prompt_templates = list_prompt_templates(prompts_root=prompts_root)
    metadata = prompt_templates["replyer"].metadata

    assert metadata.display_name == "Replyer"
    assert metadata.advanced is False
    assert metadata.description == "Prompt specific metadata"


def test_builtin_prompt_templates_have_intro_metadata() -> None:
    for locale_dir in sorted(path for path in PROMPTS_ROOT.iterdir() if path.is_dir()):
        prompt_templates = list_prompt_templates(locale=locale_dir.name)
        for prompt_file in iter_prompt_files(locale_dir, recursive=False):
            metadata = prompt_templates[prompt_file.stem].metadata
            assert metadata.display_name, f"{prompt_file} 缺少 display_name 元信息"
            assert metadata.description, f"{prompt_file} 缺少 description 元信息"


def test_builtin_emoji_content_analysis_templates_are_localized_consistently() -> None:
    expected_placeholders = {"image_type"}

    for locale in ("zh-CN", "en-US", "ja-JP"):
        prompt_path = PROMPTS_ROOT / locale / "emoji_content_analysis.prompt"

        assert prompt_path.is_file()
        assert extract_prompt_placeholders(prompt_path.read_text(encoding="utf-8")) == expected_placeholders

        template_info = list_prompt_templates(locale=locale)["emoji_content_analysis"]
        assert template_info.path == prompt_path
        assert template_info.metadata.display_name
        assert template_info.metadata.advanced is True
        assert template_info.metadata.description


def test_list_prompt_templates_reports_duplicate_name_with_custom_root(tmp_path: Path) -> None:
    prompts_root = tmp_path / "prompts"
    first_dir = prompts_root / "zh-CN" / "chat"
    second_dir = prompts_root / "zh-CN" / "system"
    first_dir.mkdir(parents=True, exist_ok=True)
    second_dir.mkdir(parents=True, exist_ok=True)
    (first_dir / "replyer.prompt").write_text("chat", encoding="utf-8")
    (second_dir / "replyer.prompt").write_text("system", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        list_prompt_templates(prompts_root=prompts_root)

    assert "zh-CN/chat/replyer.prompt" in str(exc_info.value)
    assert "zh-CN/system/replyer.prompt" in str(exc_info.value)


def test_prompt_manager_load_prompts_prefers_locale_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts_root = tmp_path / "prompts"
    custom_prompts_root = tmp_path / "data" / "custom_prompts"
    custom_prompts_root.mkdir(parents=True, exist_ok=True)
    write_prompt(prompts_root, "zh-CN", "replyer", "中文模板")
    write_prompt(prompts_root, "en-US", "replyer", "English template")
    set_locale("en-US")

    monkeypatch.setattr("src.prompt.prompt_manager.PROMPTS_DIR", prompts_root, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.CUSTOM_PROMPTS_DIR", custom_prompts_root, raising=False)
    monkeypatch.setattr("src.prompt.prompt_manager.SUFFIX_PROMPT", ".prompt", raising=False)

    manager = PromptManager()
    manager.load_prompts()

    assert manager.get_prompt("replyer").template == "English template"
