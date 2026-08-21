from pathlib import Path

from src.platform_io.adapter_policy import AdapterIdentity, AdapterPolicyManager


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
