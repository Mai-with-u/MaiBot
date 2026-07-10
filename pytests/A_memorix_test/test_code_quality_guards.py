import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_kernel_method_call(node: ast.AST, method_name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "kernel"
        and node.func.attr == method_name
    )


def test_a_memorix_has_no_silent_broad_exception_handlers() -> None:
    violations = []
    source_paths = list((REPO_ROOT / "src" / "A_memorix").rglob("*.py"))
    assert source_paths

    for path in source_paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
                continue
            if node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
            ):
                violations.append(f"{path}:{node.lineno}")

    assert violations == []


def test_migration_scripts_shutdown_initialized_kernels_asynchronously() -> None:
    violations = []
    script_root = REPO_ROOT / "src" / "A_memorix" / "scripts"
    for script_name in ("migrate_chat_history.py", "migrate_person_memory_points.py"):
        path = script_root / script_name
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        if any(_is_kernel_method_call(node, "close") for node in ast.walk(tree)):
            violations.append(f"{path}: 不应同步关闭活动内核")

        shutdown_in_finally = any(
            isinstance(descendant, ast.Await) and _is_kernel_method_call(descendant.value, "shutdown")
            for try_node in ast.walk(tree)
            if isinstance(try_node, ast.Try)
            for final_node in try_node.finalbody
            for descendant in ast.walk(final_node)
        )
        if not shutdown_in_finally:
            violations.append(f"{path}: 缺少 finally 中的异步关闭")

    assert violations == []
