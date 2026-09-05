"""锁住 Host capability 注册名与 SDK 字面量的一致性。

Host 注册表与 SDK 能力代理/unwrap 表应使用同一套 ``foo.bar`` 名称。
unwrap 表是故意子集：并非每个能力都需要把 RPC 包装结果拆成单一字段。
"""

from pathlib import Path

import ast
import re

from src.plugin_runtime.capabilities.registry import register_capability_impls

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOST_REGISTRY_PATH = _REPO_ROOT / "src" / "plugin_runtime" / "capabilities" / "registry.py"
_SDK_ROOT = _REPO_ROOT / "packages" / "maibot-plugin-sdk" / "maibot_sdk"
_CAPABILITY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# SDK 字面量里会出现、但不是 cap.call 能力名的 Host RPC 方法。
SDK_DOTTED_NAMES_THAT_ARE_NOT_CAPABILITIES = frozenset(
    {
        "cap.call",
        "host.route_message",
        "host.update_message_gateway_state",
    }
)

# Host 已注册、SDK context / 能力代理源码尚未写出的能力名。
# ComponentCapability 没有 update_plugin_config 包装；内置 plugin_management
# 通过 PluginContext.call_capability 直接调用该名称。
HOST_CAPABILITIES_MISSING_FROM_SDK_LITERALS = frozenset(
    {
        "component.update_plugin_config",
    }
)


class _FakeCapabilityService:
    def __init__(self) -> None:
        self.names: set[str] = set()

    def register_capability(self, name: str, impl: object) -> None:
        self.names.add(name)


class _FakeSupervisor:
    def __init__(self) -> None:
        self.capability_service = _FakeCapabilityService()


class _FakeManager:
    def __getattr__(self, name: str) -> object:
        return object()


def _format_names(names: set[str]) -> str:
    if not names:
        return "(empty)"
    return ", ".join(sorted(names))


def _format_set_diff(*, host_only: set[str], sdk_only: set[str]) -> str:
    return f"Host 有而 SDK 无: {_format_names(host_only)}; SDK 有而 Host 无: {_format_names(sdk_only)}"


def _iter_string_constants(node: ast.AST) -> list[str]:
    values: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.append(child.value)
    return values


def _module_bound_value(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if node.value is None:
                raise AssertionError(f"模块级赋值缺少值: {name}")
            return node.value
    raise AssertionError(f"未找到模块级赋值: {name}")


def _string_set_from_value(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Dict):
        names = {key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
        if len(names) != len([key for key in node.keys if key is not None]):
            raise AssertionError("字典键不全是字符串字面量")
        return names
    return {value for value in _iter_string_constants(node) if _CAPABILITY_NAME_RE.fullmatch(value)}


def _host_registered_names() -> set[str]:
    supervisor = _FakeSupervisor()
    register_capability_impls(_FakeManager(), supervisor)  # type: ignore[arg-type]
    return set(supervisor.capability_service.names)


def _host_registry_literals() -> set[str]:
    tree = ast.parse(_HOST_REGISTRY_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_register" or not node.args:
            continue
        name_node = node.args[0]
        if not isinstance(name_node, ast.Constant) or not isinstance(name_node.value, str):
            raise AssertionError("_register 的能力名必须是字符串字面量")
        names.add(name_node.value)
    return names


def _sdk_scan_paths() -> list[Path]:
    capability_dir = _SDK_ROOT / "capabilities"
    return [_SDK_ROOT / "context.py", *sorted(capability_dir.glob("*.py"))]


def _sdk_dotted_literals() -> set[str]:
    names: set[str] = set()
    for path in _sdk_scan_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names.update(value for value in _iter_string_constants(tree) if _CAPABILITY_NAME_RE.fullmatch(value))
    return names


def _load_sdk_context_tree() -> ast.Module:
    tree = ast.parse((_SDK_ROOT / "context.py").read_text(encoding="utf-8"))
    if not isinstance(tree, ast.Module):
        raise AssertionError("context.py 不是模块 AST")
    return tree


def test_host_registry_literals_match_registered_names() -> None:
    registered = _host_registered_names()
    literals = _host_registry_literals()
    assert registered == literals, _format_set_diff(
        host_only=registered - literals,
        sdk_only=literals - registered,
    )


def test_sdk_unwrap_tables_are_subsets_of_host_registry() -> None:
    host_names = _host_registered_names()
    context_tree = _load_sdk_context_tree()
    unwrap_names = _string_set_from_value(_module_bound_value(context_tree, "_CAPABILITY_RESULT_KEYS"))
    boolean_names = _string_set_from_value(_module_bound_value(context_tree, "_BOOLEAN_SUCCESS_CAPABILITIES"))
    detailed_send_names = _string_set_from_value(_module_bound_value(context_tree, "_DETAILED_SEND_CAPABILITIES"))

    extra_unwrap = unwrap_names - host_names
    extra_boolean = boolean_names - host_names
    extra_detailed_send = detailed_send_names - host_names
    assert extra_unwrap == set(), f"unwrap 表出现 Host 未注册的能力: {_format_names(extra_unwrap)}"
    assert extra_boolean == set(), f"boolean unwrap 表出现 Host 未注册的能力: {_format_names(extra_boolean)}"
    assert extra_detailed_send == set(), (
        f"detailed send unwrap 表出现 Host 未注册的能力: {_format_names(extra_detailed_send)}"
    )


def test_host_registry_matches_sdk_capability_literals() -> None:
    host_names = _host_registered_names()
    sdk_names = _sdk_dotted_literals()
    host_only = host_names - sdk_names
    sdk_only = sdk_names - host_names

    assert host_only == set(HOST_CAPABILITIES_MISSING_FROM_SDK_LITERALS) and sdk_only == set(
        SDK_DOTTED_NAMES_THAT_ARE_NOT_CAPABILITIES
    ), _format_set_diff(host_only=host_only, sdk_only=sdk_only)
