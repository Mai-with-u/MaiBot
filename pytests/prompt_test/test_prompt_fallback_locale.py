"""锁定 prompt_i18n 的 locale 回退始终包含 zh-CN。

即使 DEFAULT_LOCALE 被改成其他语言，解析候选列表仍必须包含中文源 locale。
"""

from __future__ import annotations

from pathlib import Path

import pytest

prompt_i18n = pytest.importorskip("src.common.prompt_i18n")


def _patch_default_locale(monkeypatch: pytest.MonkeyPatch, locale: str) -> None:
    """把 UI 的 DEFAULT_LOCALE 改成指定值，确认 prompt 回退不跟随它。"""

    monkeypatch.setattr("src.common.i18n.loaders.DEFAULT_LOCALE", locale)
    # 若 prompt_i18n 重新绑定 DEFAULT_LOCALE，同步覆盖以免测试误过。
    monkeypatch.setattr(prompt_i18n, "DEFAULT_LOCALE", locale, raising=False)


@pytest.mark.parametrize("requested_locale", ["ja-JP", "en-US", "zh-CN"])
def test_iter_locale_candidates_includes_zh_cn_when_default_locale_is_en_us(
    monkeypatch: pytest.MonkeyPatch,
    requested_locale: str,
) -> None:
    """DEFAULT_LOCALE 为 en-US 时，任意请求 locale 的候选列表仍包含 zh-CN。"""

    _patch_default_locale(monkeypatch, "en-US")

    candidates = prompt_i18n._iter_locale_candidates(requested_locale)

    assert prompt_i18n.PROMPT_FALLBACK_LOCALE == "zh-CN"
    assert "zh-CN" in candidates


def test_iter_prompt_template_layers_includes_zh_cn_when_default_locale_is_en_us(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """DEFAULT_LOCALE 为 en-US 时，模板扫描层仍包含 zh-CN 目录。"""

    _patch_default_locale(monkeypatch, "en-US")

    layers = prompt_i18n._iter_prompt_template_layers(tmp_path / "prompts", "ja-JP")

    assert prompt_i18n.PROMPT_FALLBACK_LOCALE == "zh-CN"
    assert any(path.name == "zh-CN" for path in layers)


def test_load_prompt_falls_back_to_zh_cn_when_default_locale_is_en_us(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """DEFAULT_LOCALE 为 en-US 且请求 ja-JP 时，仍回退加载仅存在于 zh-CN 的模板。"""

    _patch_default_locale(monkeypatch, "en-US")

    prompts_root = tmp_path / "prompts"
    locale_dir = prompts_root / "zh-CN"
    locale_dir.mkdir(parents=True)
    (locale_dir / "replyer.prompt").write_text("中文 {user_name}", encoding="utf-8")

    rendered = prompt_i18n.load_prompt(
        "replyer",
        locale="ja-JP",
        prompts_root=prompts_root,
        user_name="Mai",
    )

    assert prompt_i18n.PROMPT_FALLBACK_LOCALE == "zh-CN"
    assert rendered == "中文 Mai"
