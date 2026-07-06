from types import SimpleNamespace

from src.maisaka import attention_drift


def _set_attention_drift_config(monkeypatch, **kwargs) -> None:
    defaults = {
        "enabled": False,
        "drift_level": "active",
        "anchor_policy": "balanced",
        "reaction_style": "natural",
    }
    defaults.update(kwargs)
    experimental = SimpleNamespace(attention_drift=SimpleNamespace(**defaults))
    monkeypatch.setattr(attention_drift, "global_config", SimpleNamespace(experimental=experimental))
    monkeypatch.setattr(attention_drift, "get_locale", lambda: "zh-CN")


def test_attention_drift_prompt_block_disabled(monkeypatch):
    _set_attention_drift_config(monkeypatch, enabled=False)

    assert attention_drift.build_attention_drift_prompt_block() == ""


def test_attention_drift_prompt_block_enabled(monkeypatch):
    _set_attention_drift_config(
        monkeypatch,
        enabled=True,
        drift_level="scattered",
        anchor_policy="strict",
        reaction_style="lively",
    )

    prompt_block = attention_drift.build_attention_drift_prompt_block()

    assert "注意力漂移风格" in prompt_block
    assert "不要自称 ADHD" in prompt_block
    assert "漂移档位：明显发散" in prompt_block
    assert "回钩策略：严格回钩" in prompt_block
    assert "短反应风格：活泼短反应" in prompt_block
    assert "0." not in prompt_block
