"""公共配置与 Bot 配置的文件事务；启动时回滚未完成的提交。"""

from pathlib import Path
from typing import Dict
from uuid import uuid4

import json
import os


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover_config_transaction(host: Path) -> None:
    journal = host / "model-config-transaction.json"
    if not journal.exists():
        return
    for filename, original in json.loads(journal.read_text(encoding="utf-8")).items():
        path = Path(filename)
        if original is None:
            path.unlink(missing_ok=True)
        else:
            write_private(path, original)
    journal.unlink()


def commit_config_files(host: Path, files: Dict[Path, str]) -> None:
    """调用方持有公共模型写锁；任何未提交事务都先恢复原始内容。"""
    recover_config_transaction(host)
    journal = host / "model-config-transaction.json"
    originals = {str(path.resolve()): path.read_bytes().decode("utf-8") if path.exists() else None for path in files}
    write_private(journal, json.dumps(originals, ensure_ascii=False))
    try:
        for path, content in files.items():
            write_private(path, content)
        journal.unlink()
    except BaseException:
        recover_config_transaction(host)
        raise
