from copy import deepcopy
from pathlib import Path
from typing import List

import pytest
import tomlkit

from src.platform_io.adapter_policy import AdapterIdentity, AdapterPolicyManager


@pytest.mark.parametrize(
    "list_type,ids",
    [
        ("whitelist", ["100"]),
        ("blacklist", ["100"]),
        ("whitelist", ["*"]),
        ("blacklist", ["*"]),
    ],
)
def test_group_creation_preserves_legacy_policy(tmp_path: Path, list_type: str, ids: List[str]) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        tomlkit.dumps(
            {
                "adapters": [
                    {
                        "plugin_id": "adapter.qq",
                        "group": {
                            "list_type": list_type,
                            "ids": ids,
                            "allow_ids": ["200"],
                            "deny_ids": ["300"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)
    identity = AdapterIdentity(plugin_id="adapter.qq")
    before = [
        manager.evaluate(identity, chat_type="group", target_id=target).allowed
        for target in ("100", "200", "300", "400")
    ]
    policy = manager.get_adapter_policy(identity)
    manager.set_adapter_policy(
        identity, policy, policy_groups=[{"id": "default", "name": "默认分组", **policy}], active_group="default"
    )
    after = [
        manager.evaluate(identity, chat_type="group", target_id=target).allowed
        for target in ("100", "200", "300", "400")
    ]
    assert before == after


def test_policy_groups_switch_and_survive_reload_and_chat_edits(tmp_path: Path) -> None:
    manager = AdapterPolicyManager(tmp_path / "adapter_policy.toml")
    identity = AdapterIdentity(plugin_id="adapter.qq", platform="qq", account_id="bot", scope="main")
    original = {
        "group": {"default_action": "allow", "allow_ids": [], "deny_ids": ["100"]},
        "private": {"default_action": "block", "allow_ids": ["200"], "deny_ids": []},
    }
    manager.set_adapter_policy(identity, original)
    groups = [
        {"id": "default", "name": "默认分组", **deepcopy(manager.get_adapter_policy(identity))},
        {
            "id": "testing",
            "name": "测试",
            "group": {
                "default_action": "block",
                "allow_ids": ["100"],
                "deny_ids": [],
            },
            "private": {"default_action": "block", "allow_ids": [], "deny_ids": []},
        },
    ]
    manager.set_adapter_policy(identity, groups[1], policy_groups=groups, active_group="testing")
    manager = AdapterPolicyManager(manager.policy_path)
    assert manager.evaluate(identity, chat_type="group", target_id="100").allowed is True
    assert manager.evaluate(identity, chat_type="private", target_id="200").allowed is False
    # 同账号其他配置域与其他账号均不受当前分组影响。
    for other_identity in (
        AdapterIdentity(plugin_id="adapter.qq", platform="qq", account_id="other", scope="main"),
        AdapterIdentity(plugin_id="adapter.qq", platform="qq", account_id="bot", scope="other"),
    ):
        assert manager.get_adapter_policy(other_identity)["group"]["default_action"] == "inherit"
        assert "policy_groups" not in manager.get_adapter_policy(other_identity)

    manager.set_chat_override(identity, chat_type="private", target_id="300", action="allow")
    current = manager.get_adapter_policy(identity)
    assert current["policy_groups"][1]["private"]["allow_ids"] == ["300"]
    manager.set_adapter_policy(
        identity, current["policy_groups"][0], policy_groups=current["policy_groups"], active_group="default"
    )
    assert manager.evaluate(identity, chat_type="group", target_id="100").allowed is False
    assert manager.evaluate(identity, chat_type="private", target_id="200").allowed is True
    # 删除聊天流应清理未启用分组中的覆盖，避免切回来时复活。
    manager.remove_chat_overrides(chat_type="private", target_id="300", platform="qq", account_id="bot", scope="main")
    current = manager.get_adapter_policy(identity)
    assert current["policy_groups"][1]["private"]["allow_ids"] == []
    manager.set_adapter_policy(identity, current, policy_groups=[current["policy_groups"][0]], active_group="default")
    assert len(manager.get_adapter_policy(identity)["policy_groups"]) == 1


@pytest.mark.parametrize("invalid", ["empty", "unknown", "duplicate", "conflict", "mismatch"])
def test_invalid_policy_groups_do_not_change_file(tmp_path: Path, invalid: str) -> None:
    manager = AdapterPolicyManager(tmp_path / "adapter_policy.toml")
    identity = AdapterIdentity(plugin_id="adapter.qq")
    policy = {
        "group": {"default_action": "inherit", "allow_ids": [], "deny_ids": []},
        "private": {"default_action": "inherit", "allow_ids": [], "deny_ids": []},
    }
    manager.set_adapter_policy(identity, policy)
    before = manager.policy_path.read_bytes()
    groups = [{"id": "default", "name": "默认分组", **deepcopy(policy)}]
    active = "default"
    if invalid == "empty":
        groups = []
    elif invalid == "unknown":
        active = "missing"
    elif invalid == "duplicate":
        groups.append(deepcopy(groups[0]))
    elif invalid == "conflict":
        groups[0]["group"].update(allow_ids=["100"], deny_ids=["100"])
    else:
        groups[0]["group"]["default_action"] = "block"
    with pytest.raises(ValueError):
        manager.set_adapter_policy(identity, policy, policy_groups=groups, active_group=active)
    assert manager.policy_path.read_bytes() == before


def test_adapter_policy_missing_file_defaults_to_allow(tmp_path: Path) -> None:
    manager = AdapterPolicyManager(tmp_path / "missing.toml")

    result = manager.evaluate(
        AdapterIdentity(plugin_id="maibot-team.snowluma-adapter", gateway_name="snowluma_gateway", platform="qq"),
        chat_type="group",
        target_id="123",
    )

    assert result.allowed is True
    assert result.configured is False
    assert result.source == "implicit_default"
    assert result.reason == "default_allow"


def test_adapter_policy_group_and_private_defaults_are_independent(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    manager = AdapterPolicyManager(policy_path)

    manager.set_default_action("group", "allow")
    manager.set_default_action("private", "block")

    group_result = manager.evaluate(AdapterIdentity(platform="qq"), chat_type="group", target_id="10001")
    private_result = manager.evaluate(AdapterIdentity(platform="qq"), chat_type="private", target_id="20001")

    assert manager.get_default_actions() == {"group": "allow", "private": "block"}
    assert group_result.allowed is True
    assert group_result.reason == "default_allow"
    assert private_result.allowed is False
    assert private_result.reason == "default_block"


def test_adapter_policy_rejects_unknown_chat_type(tmp_path: Path) -> None:
    manager = AdapterPolicyManager(tmp_path / "adapter_policy.toml")

    try:
        manager.evaluate(AdapterIdentity(platform="qq"), chat_type="channel", target_id="10001")
    except ValueError as exc:
        assert "不支持的聊天类型" in str(exc)
    else:
        raise AssertionError("未知聊天类型必须被拒绝")


def test_adapter_policy_default_whitelist(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[defaults.group]
list_type = "whitelist"
ids = ["10001"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)

    allowed = manager.evaluate(AdapterIdentity(platform="qq"), chat_type="group", target_id="10001")
    denied = manager.evaluate(AdapterIdentity(platform="qq"), chat_type="group", target_id="10002")

    assert allowed.allowed is True
    assert allowed.configured is True
    assert allowed.source == "defaults"
    assert denied.allowed is False
    assert denied.reason == "not_in_whitelist"


def test_adapter_policy_adapter_specific_blacklist_overrides_default(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[defaults.group]
list_type = "whitelist"
ids = ["*"]

[[adapters]]
plugin_id = "maibot-team.snowluma-adapter"
gateway_name = "snowluma_gateway"

[adapters.group]
list_type = "blacklist"
ids = ["10001"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)
    identity = AdapterIdentity(
        plugin_id="maibot-team.snowluma-adapter",
        gateway_name="snowluma_gateway",
        platform="qq",
    )

    denied = manager.evaluate(identity, chat_type="group", target_id="10001")
    allowed = manager.evaluate(identity, chat_type="group", target_id="10002")

    assert denied.allowed is False
    assert denied.source == "adapter"
    assert denied.reason == "matched_blacklist"
    assert allowed.allowed is True


def test_adapter_policy_chat_override_can_allow_block_and_inherit(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[defaults.group]
list_type = "whitelist"
ids = ["10002"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)
    identity = AdapterIdentity(
        adapter_id="adapter.snowluma.gateway",
        plugin_id="maibot-team.snowluma-adapter",
        gateway_name="snowluma_gateway",
        platform="qq",
    )

    manager.set_chat_override(identity, chat_type="group", target_id="10001", action="allow")
    allowed = manager.evaluate(identity, chat_type="group", target_id="10001")

    manager.set_chat_override(identity, chat_type="group", target_id="10001", action="block")
    blocked = manager.evaluate(identity, chat_type="group", target_id="10001")

    manager.set_chat_override(identity, chat_type="group", target_id="10001", action="inherit")
    inherited = manager.evaluate(identity, chat_type="group", target_id="10001")

    assert allowed.allowed is True
    assert allowed.reason == "matched_allow_override"
    assert blocked.allowed is False
    assert blocked.reason == "matched_deny_override"
    assert inherited.allowed is False
    assert inherited.source == "defaults"
    assert inherited.reason == "not_in_whitelist"


def test_adapter_plugin_policy_can_edit_group_and_private_rules(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    manager = AdapterPolicyManager(policy_path)
    plugin_identity = AdapterIdentity(plugin_id="maibot-team.snowluma-adapter")
    runtime_identity = AdapterIdentity(
        adapter_id="gateway:maibot-team.snowluma-adapter:snowluma_gateway",
        plugin_id="maibot-team.snowluma-adapter",
        gateway_name="snowluma_gateway",
        platform="qq",
        account_id="2814567326",
    )

    manager.set_adapter_policy(
        plugin_identity,
        {
            "group": {
                "default_action": "block",
                "allow_ids": ["10001"],
                "deny_ids": ["10002"],
            },
            "private": {
                "default_action": "inherit",
                "allow_ids": [],
                "deny_ids": ["20002"],
            },
        },
    )

    assert manager.get_adapter_policy(plugin_identity) == {
        "group": {
            "default_action": "block",
            "allow_ids": ["10001"],
            "deny_ids": ["10002"],
        },
        "private": {
            "default_action": "inherit",
            "allow_ids": [],
            "deny_ids": ["20002"],
        },
    }
    assert manager.evaluate(runtime_identity, chat_type="group", target_id="10001").allowed is True
    assert manager.evaluate(runtime_identity, chat_type="group", target_id="10002").allowed is False
    assert manager.evaluate(runtime_identity, chat_type="group", target_id="10003").allowed is False
    assert manager.evaluate(runtime_identity, chat_type="private", target_id="20001").allowed is True
    assert manager.evaluate(runtime_identity, chat_type="private", target_id="20002").allowed is False
    assert manager.evaluate(AdapterIdentity(plugin_id="another-adapter"), chat_type="group", target_id="10003").allowed


def test_adapter_plugin_policy_rejects_conflicting_ids_without_writing(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    manager = AdapterPolicyManager(policy_path)

    try:
        manager.set_adapter_policy(
            AdapterIdentity(plugin_id="maibot-team.snowluma-adapter"),
            {
                "group": {
                    "default_action": "inherit",
                    "allow_ids": ["10001"],
                    "deny_ids": ["10001"],
                },
                "private": {
                    "default_action": "inherit",
                    "allow_ids": [],
                    "deny_ids": [],
                },
            },
        )
    except ValueError as exc:
        assert "不能同时放行和拒绝" in str(exc)
    else:
        raise AssertionError("冲突 ID 必须被拒绝")

    assert not policy_path.exists()


LAYERED_POLICY_TOML = """
[[adapters]]
adapter_id = "gateway:maibot-team.snowluma-adapter:snowluma_gateway"
plugin_id = "maibot-team.snowluma-adapter"
platform = "qq"
account_id = "2814567326"

[adapters.group]
default_action = "block"
allow_ids = ["10001"]

[defaults.group]
default_action = "block"

[defaults.private]
default_action = "block"
""".strip()


def _make_runtime_identity() -> AdapterIdentity:
    return AdapterIdentity(
        adapter_id="gateway:maibot-team.snowluma-adapter:snowluma_gateway",
        plugin_id="maibot-team.snowluma-adapter",
        gateway_name="snowluma_gateway",
        platform="qq",
        account_id="2814567326",
    )


def test_adapter_policy_get_reads_best_matching_entry_instead_of_empty_template(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(LAYERED_POLICY_TOML, encoding="utf-8")
    manager = AdapterPolicyManager(policy_path)

    expected = {
        "group": {"default_action": "block", "allow_ids": ["10001"], "deny_ids": []},
        "private": {"default_action": "inherit", "allow_ids": [], "deny_ids": []},
    }
    assert manager.get_adapter_policy(_make_runtime_identity()) == expected
    # 适配器未运行、只能提供 plugin_id 时，也应展示该插件名下真实生效的条目
    assert manager.get_adapter_policy(AdapterIdentity(plugin_id="maibot-team.snowluma-adapter")) == expected


def test_adapter_policy_set_updates_existing_best_match_without_creating_shadow(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(LAYERED_POLICY_TOML, encoding="utf-8")
    manager = AdapterPolicyManager(policy_path)

    manager.set_adapter_policy(
        _make_runtime_identity(),
        {
            "group": {"default_action": "inherit", "allow_ids": [], "deny_ids": ["10002"]},
            "private": {"default_action": "inherit", "allow_ids": [], "deny_ids": []},
        },
    )

    # 必须更新既有条目而不是追加一条会被遮蔽的新条目
    assert policy_path.read_text(encoding="utf-8").count("[[adapters]]") == 1
    assert manager.evaluate(_make_runtime_identity(), chat_type="group", target_id="10001").allowed is False
    denied = manager.evaluate(_make_runtime_identity(), chat_type="group", target_id="10002")
    assert denied.allowed is False
    assert denied.reason == "matched_deny_override"


def test_adapter_policy_chat_override_writes_into_existing_best_match(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(LAYERED_POLICY_TOML, encoding="utf-8")
    manager = AdapterPolicyManager(policy_path)

    manager.set_chat_override(
        _make_runtime_identity(),
        chat_type="group",
        target_id="10001",
        action="block",
    )

    assert policy_path.read_text(encoding="utf-8").count("[[adapters]]") == 1
    blocked = manager.evaluate(_make_runtime_identity(), chat_type="group", target_id="10001")
    assert blocked.allowed is False
    assert blocked.reason == "matched_deny_override"


def test_adapter_policy_lists_plugin_entries_for_account_switch(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(LAYERED_POLICY_TOML, encoding="utf-8")
    manager = AdapterPolicyManager(policy_path)

    # 换号后新身份没有专属条目，但旧账号的条目仍留在配置里；面板需要两者信息
    changed_account = AdapterIdentity(
        adapter_id="gateway:maibot-team.snowluma-adapter:snowluma_gateway",
        plugin_id="maibot-team.snowluma-adapter",
        gateway_name="snowluma_gateway",
        platform="qq",
        account_id="9999999999",
    )
    assert manager.has_adapter_policy_entry(changed_account) is False
    assert manager.has_adapter_policy_entry(_make_runtime_identity()) is True
    entries = manager.list_plugin_entries("maibot-team.snowluma-adapter")
    assert len(entries) == 1
    assert entries[0]["account_id"] == "2814567326"
    assert entries[0]["platform"] == "qq"
