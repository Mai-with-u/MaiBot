"""绑定删除的多文件提交；中断后先完成提交，再加载适配器身份。"""

from pathlib import Path
from typing import Dict

import json
import os


FILES = {"id_map.json", "auto_user_profiles.json", "union_user_ids.json"}
JOURNAL = "binding_delete_pending.json"


def _write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def recover_binding_delete(directory: Path) -> None:
    journal = directory / JOURNAL
    if not journal.exists():
        return
    contents = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(contents, dict) or set(contents) != FILES or any(not isinstance(value, str) for value in contents.values()):
        raise ValueError("绑定删除记录无效")
    for name, content in contents.items():
        _write(directory / name, content)
    journal.unlink()


def commit_binding_delete(directory: Path, contents: Dict[str, str]) -> None:
    if set(contents) != FILES:
        raise ValueError("绑定删除记录缺少文件")
    directory.mkdir(parents=True, exist_ok=True)
    recover_binding_delete(directory)
    _write(directory / JOURNAL, json.dumps(contents, ensure_ascii=False))
    recover_binding_delete(directory)
