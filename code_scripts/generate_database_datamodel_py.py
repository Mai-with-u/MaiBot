from pathlib import Path
import ast
import subprocess
import sys

base_file_path = Path(__file__).parent.parent.absolute().resolve() / "src" / "common" / "database" / "database_model.py"
target_file_path = (
    Path(__file__).parent.parent.absolute().resolve() / "src" / "common" / "database" / "database_datamodel.py"
)

with open(base_file_path, "r", encoding="utf-8") as f:
    source_text = f.read()
source_lines = source_text.splitlines()

try:
    tree = ast.parse(source_text)
except SyntaxError as e:
    raise e

code_lines = [
    "from typing import Optional",
    "from pydantic import BaseModel",
    "from datetime import datetime",
    "from .database_model import ModelUser, ImageType",
]


def src(node):
    seg = ast.get_source_segment(source_text, node)
    return seg if seg is not None else ast.unparse(node)


for node in tree.body:
    if not isinstance(node, ast.ClassDef):
        continue
    # 判断是否 SQLModel 且 table=True
    has_sqlmodel = any(
        (isinstance(b, ast.Name) and b.id == "SQLModel") or (isinstance(b, ast.Attribute) and b.attr == "SQLModel")
        for b in node.bases
    )
    has_table_kw = any(
        (kw.arg == "table" and isinstance(kw.value, ast.Constant) and kw.value.value is True) for kw in node.keywords
    )
    if not (has_sqlmodel and has_table_kw):
        continue

    class_name = node.name
    code_lines.append("")
    code_lines.append(f"class {class_name}(BaseModel):")

    fields_added = 0
    for item in node.body:
        # 跳过 __tablename__ 等
        if isinstance(item, ast.Assign):
            if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                continue
            name = item.targets[0].id
            if name == "__tablename__":
                continue
            value_src = src(item.value)
            line = f"    {name} = {value_src}"
            fields_added += 1
            lineno = getattr(item, "lineno", None)
        elif isinstance(item, ast.AnnAssign):
            # 注解赋值
            if not isinstance(item.target, ast.Name):
                continue
            name = item.target.id
            ann = src(item.annotation) if item.annotation is not None else None
            if item.value is None:
                line = f"    {name}: {ann}" if ann else f"    {name}"
            elif isinstance(item.value, ast.Call) and (
                (isinstance(item.value.func, ast.Name) and item.value.func.id == "Field")
                or (isinstance(item.value.func, ast.Attribute) and item.value.func.attr == "Field")
            ):
                default_kw = next((kw for kw in item.value.keywords if kw.arg == "default"), None)
                if default_kw is None:
                    # 没有 default，保留类型但不赋值
                    line = f"    {name}: {ann}" if ann else f"    {name}"
                else:
                    default_src = src(default_kw.value)
                    line = f"    {name}: {ann} = {default_src}"
            else:
                value_src = src(item.value)
                line = f"    {name}: {ann} = {value_src}" if ann else f"    {name} = {value_src}"
            fields_added += 1
            lineno = getattr(item, "lineno", None)
        else:
            continue

        # 提取同一行的行内注释作为字段说明（如果存在）
        comment = None
        if lineno is not None:
            src_line = source_lines[lineno - 1]
            if "#" in src_line:
                # 取第一个 #
                comment = src_line.split("#", 1)[1].strip()
                # 避免三引号冲突
                comment = comment.replace('"""', '\\"""')

        code_lines.append(line)
        if comment:
            code_lines.append(f'    """{comment}"""')
        else:
            print(f"Warning: No comment found for field '{name}' in class '{class_name}'.")

    if fields_added == 0:
        code_lines.append("    pass")

with open(target_file_path, "w", encoding="utf-8") as f:
    f.write("\n".join(code_lines) + "\n")

try:
    result = subprocess.run(["ruff", "format", str(target_file_path)], capture_output=True, text=True)
except FileNotFoundError:
    print("ruff 未找到，请安装 ruff 并确保其在 PATH 中（例如：pip install ruff）", file=sys.stderr)
    sys.exit(127)

# 输出 ruff 的 stdout/stderr
if result.stdout:
    print(result.stdout, end="")
if result.stderr:
    print(result.stderr, file=sys.stderr, end="")

if result.returncode != 0:
    print(f"ruff 检查失败，退出码：{result.returncode}", file=sys.stderr)
    sys.exit(result.returncode)
