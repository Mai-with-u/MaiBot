"""验证 SQLite 页缓存默认值已降低，防止 RSS 阶梯式上涨（#2191）。"""

import sqlite3
import tempfile
from pathlib import Path

from src.A_memorix.core.storage.sqlite_connection import SQLiteConnectionManager


def test_sqlite_cache_size_not_64mb() -> None:
    """SQLite 连接不应再使用 64MB 页缓存（原 PRAGMA cache_size=-64000）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        manager = SQLiteConnectionManager(db_path)
        conn = manager.connection()

        # 查询当前 cache_size 值（负值表示 KB）
        result = conn.execute("PRAGMA cache_size").fetchone()
        cache_size_kb = result[0]  # type: ignore[index]

        # 64MB = 64000KB，修复后应为 8192KB（8MB）或更低
        # 注意：cache_size 为负值（KB），-8192 > -64000，所以用 abs() 判断更清晰
        assert abs(cache_size_kb) <= 16384, (
            f"SQLite cache_size 仍为 {cache_size_kb}KB（-{abs(cache_size_kb)//1024}MB），"
            f"绝对值应 ≤ 16384（-16MB）。原 64000KB（-64MB）会导致每连接 RSS 膨胀，引发 #2191。"
        )

        # 具体验证：应为 -8192（8MB）
        assert cache_size_kb == -8192, (
            f"预期 cache_size = -8192（8MB），实际为 {cache_size_kb}"
        )

        manager.close_all()


def test_main_database_cache_size_not_64mb() -> None:
    """主数据库连接的 cache_size 不应为 64MB（#2191）。"""
    # 直接测试 set_sqlite_pragma 函数对连接的影响
    from src.common.database.database import set_sqlite_pragma

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)

        # 应用 PRAGMA
        set_sqlite_pragma(conn, None)

        # 查询 cache_size
        result = conn.execute("PRAGMA cache_size").fetchone()
        cache_size_kb = result[0]  # type: ignore[index]

        # 64MB = 64000KB，修复后应为 8192KB（8MB）或更低
        assert abs(cache_size_kb) <= 16384, (
            f"主数据库 cache_size 仍为 {cache_size_kb}KB（-{abs(cache_size_kb)//1024}MB），"
            f"绝对值应 ≤ 16384（-16MB）。原 64000KB（-64MB）会导致每连接 RSS 膨胀，引发 #2191。"
        )

        # 具体验证：应为 -8192（8MB）
        assert cache_size_kb == -8192, (
            f"预期 cache_size = -8192（8MB），实际为 {cache_size_kb}"
        )

        conn.close()
