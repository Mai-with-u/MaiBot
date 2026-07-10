import ast
from pathlib import Path


def test_a_memorix_has_no_silent_broad_exception_handlers() -> None:
    violations = []
    source_root = Path("src/A_memorix")
    for path in source_root.rglob("*.py"):
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
