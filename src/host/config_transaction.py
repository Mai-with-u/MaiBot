"""公共配置与 Bot 配置的文件事务；启动时回滚未完成的提交。"""

from pathlib import Path
from typing import Dict

import json

from src.common.atomic_file import quarantine_file, sync_directory
from src.common.atomic_file import write_private as write_private
from src.common.logger import get_logger

logger = get_logger("host.config_transaction")


def recover_config_transaction(host: Path) -> None:
    journal = host / "model-config-transaction.json"
    try:
        originals = json.loads(journal.read_text(encoding="utf-8"))
        # 确认整份记录可用后再回滚，避免遇到坏条目时只恢复了部分文件。
        if not isinstance(originals, dict) or any(
            not Path(filename).is_absolute() or "\x00" in filename
            or (original is not None and not isinstance(original, str))
            for filename, original in originals.items()
        ):
            raise ValueError("配置事务记录必须使用绝对路径，原内容必须是字符串或 null")
    except FileNotFoundError:
        return
    except (ValueError, RecursionError):
        backup = quarantine_file(journal)
        logger.warning(f"配置事务记录损坏，已放弃本次回滚并保留原记录：{backup}", exc_info=True)
        return

    for filename, original in originals.items():
        path = Path(filename)
        if original is None:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            sync_directory(path.parent)
        else:
            write_private(path, original)
    journal.unlink()
    sync_directory(host)


def commit_config_files(host: Path, files: Dict[Path, str]) -> None:
    """调用方持有公共模型写锁；提交前先处理上次遗留的事务记录。"""
    recover_config_transaction(host)
    journal = host / "model-config-transaction.json"
    originals = {str(path.resolve()): path.read_bytes().decode("utf-8") if path.exists() else None for path in files}
    write_private(journal, json.dumps(originals, ensure_ascii=False))
    try:
        for path, content in files.items():
            write_private(path, content)
    except BaseException:
        recover_config_transaction(host)
        raise
    journal.unlink()
    sync_directory(host)
