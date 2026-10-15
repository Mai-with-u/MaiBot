import pytest

from src.common.i18n import use_locale
from src.config.config import global_config
from src.maisaka import chat_loop_service
from src.maisaka.chat_loop_service import (
    PLANNER_OPTIMIZED_FINAL_USER_REMINDER,
    MaisakaChatLoopService,
)


@pytest.fixture
def optimized_service(monkeypatch: pytest.MonkeyPatch) -> MaisakaChatLoopService:
    monkeypatch.setattr(global_config.experimental, "planner_optimized_prompt", True)
    monkeypatch.setattr(global_config.bot, "nickname", "麦麦")
    monkeypatch.setattr(global_config.bot, "alias_names", ["麦叠", "牢麦"])
    monkeypatch.setattr(global_config.personality, "behavior_style", "测试行为风格")
    monkeypatch.setattr(
        chat_loop_service.focus_mode_manager, "is_enabled_for_session", lambda session_id, is_group_chat: False
    )
    return MaisakaChatLoopService(is_group_chat=True)


def test_optimized_prompt_describes_bot_in_second_person(optimized_service: MaisakaChatLoopService) -> None:
    with use_locale("zh-CN"):
        system_prompt = optimized_service._build_chat_system_prompt()

    assert system_prompt.startswith("你的名字是麦麦，还有人叫你麦叠、牢麦。你的行为风格是：测试行为风格\n\n")
    assert "你不是 麦麦 本人" not in system_prompt
    assert "你需要先搜集能够帮助麦麦进行下一步行动的信息，然后继续思考" in system_prompt
    assert "其他情况（包括回复）都需要调用工具" in system_prompt
    assert optimized_service._build_planner_final_user_reminder() == PLANNER_OPTIMIZED_FINAL_USER_REMINDER


def test_optimized_prompt_omits_alias_clause_without_aliases(
    optimized_service: MaisakaChatLoopService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(global_config.bot, "alias_names", [])

    with use_locale("zh-CN"):
        system_prompt = optimized_service._build_chat_system_prompt()

    assert system_prompt.startswith("你的名字是麦麦。你的行为风格是：测试行为风格\n\n")


def test_optimized_prompt_only_mentions_nicknames_that_differ_from_name(
    optimized_service: MaisakaChatLoopService,
) -> None:
    optimized_service._bot_platform = "qq"
    optimized_service._bot_platform_nickname = "麦麦"
    optimized_service._bot_group_cardname = "我不是麦麦"

    with use_locale("zh-CN"):
        items, _ = optimized_service._build_request_messages([], enable_visual_message=False)
        system_prompt = items[0].parts[0].text

        optimized_service._bot_group_cardname = "麦麦"
        same_name_items, _ = optimized_service._build_request_messages([], enable_visual_message=False)
        same_name_prompt = same_name_items[0].parts[0].text

    assert system_prompt.startswith(
        "你的名字是麦麦，还有人叫你麦叠、牢麦。你的行为风格是：测试行为风格\n你在本群的昵称是“我不是麦麦”。\n\n"
    )
    assert "平台的昵称" not in system_prompt
    assert same_name_prompt.startswith("你的名字是麦麦，还有人叫你麦叠、牢麦。你的行为风格是：测试行为风格\n\n")


def test_focus_mode_keeps_its_own_prompt(optimized_service: MaisakaChatLoopService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        chat_loop_service.focus_mode_manager, "is_enabled_for_session", lambda session_id, is_group_chat: True
    )

    assert optimized_service._get_chat_prompt_name() == "maisaka_chat_focus"
    assert optimized_service._build_planner_final_user_reminder() != PLANNER_OPTIMIZED_FINAL_USER_REMINDER


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_optimized_prompt_renders_in_every_locale(optimized_service: MaisakaChatLoopService, locale: str) -> None:
    with use_locale(locale):
        system_prompt = optimized_service._build_chat_system_prompt(bot_nickname_notice="昵称说明")

    assert "{" not in system_prompt.split("# Using your tools")[0]
    assert "测试行为风格\n昵称说明\n\n" in system_prompt
    assert "麦叠" in system_prompt


@pytest.mark.parametrize("prompt_name", ["maisaka_chat", "maisaka_chat_focus", "maisaka_chat_optimized"])
def test_subagent_rule_follows_switches(monkeypatch: pytest.MonkeyPatch, prompt_name: str) -> None:
    service = MaisakaChatLoopService(is_group_chat=True)
    monkeypatch.setattr(service, "_get_chat_prompt_name", lambda: prompt_name)

    with use_locale("zh-CN"):
        monkeypatch.setattr(global_config.experimental, "planner_subagent", False)
        monkeypatch.setattr(global_config.experimental, "advanced_sub_agent", True)
        disabled_prompt = service._build_chat_system_prompt()

        monkeypatch.setattr(global_config.experimental, "planner_subagent", True)
        monkeypatch.setattr(global_config.experimental, "advanced_sub_agent", False)
        enabled_prompt = service._build_chat_system_prompt()

        monkeypatch.setattr(global_config.experimental, "advanced_sub_agent", True)
        background_prompt = service._build_chat_system_prompt()

    assert "subagent()" not in disabled_prompt
    # 开启时恰好多出一行规则；关闭时不留下占位符或多余的空行
    subagent_lines = [line for line in enabled_prompt.split("\n") if line.startswith("- subagent()")]
    assert len(subagent_lines) == 1
    assert subagent_lines[0].endswith("只有一步就能完成的事才直接调用对应工具。")
    assert enabled_prompt.replace("\n" + subagent_lines[0], "") == disabled_prompt
    assert "子代理在后台运行" not in enabled_prompt
    assert "子代理在后台运行，派发后不用等它" in background_prompt
