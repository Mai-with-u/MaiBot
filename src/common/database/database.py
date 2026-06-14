from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import ContextManager, Generator, TYPE_CHECKING

from rich.traceback import install
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session, create_engine

import threading

from src.common.database.migrations import create_database_migration_bootstrapper
from src.common.logger import get_logger

if TYPE_CHECKING:
    from sqlite3 import Connection as SQLite3Connection

install(extra_lines=3)

logger = get_logger("database")


# 定义数据库文件路径
ROOT_PATH = Path(__file__).parent.parent.parent.parent.absolute().resolve()
_DB_DIR = ROOT_PATH / "data"
_DB_FILE = _DB_DIR / "MaiBot.db"

# 确保数据库目录存在
_DB_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite:///{_DB_FILE}"


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection: "SQLite3Connection", connection_record):
    """
    为每个新的数据库连接设置 SQLite PRAGMA。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA cache_size=-64000")  # 负值表示KB,64000KB = 64MB
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=1000")  # 1秒超时
    cursor.close()


# 连接数据库
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)

# 创建会话工厂（使用 sqlmodel.Session）
# 说明: 设置 expire_on_commit=False，避免在 with get_db_session() 块自动 commit 时，
# 已加载到内存中的 ORM 实例属性被标记为过期。否则会话关闭后实例进入 detached 状态，
# 任何属性访问都会触发刷新而抛出
# "Instance ... is not bound to a Session; attribute refresh operation cannot proceed".
# 当前数据库模型不包含 SQLAlchemy Relationship，因此不会引入 lazy-load 副作用。
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
    expire_on_commit=False,
)
_migration_bootstrapper = create_database_migration_bootstrapper(engine)

_db_initialized = False
_db_initialize_lock = threading.Lock()


def initialize_database() -> None:
    """初始化数据库连接、结构与启动期迁移。

    当前初始化流程遵循以下顺序：
        1. 确保数据库目录存在；
        2. 加载 SQLModel 模型定义；
        3. 执行已注册的启动期迁移；
        4. 兜底执行 ``create_all`` 确保当前模型定义已建表。
    """
    global _db_initialized
    if _db_initialized:
        return

    with _db_initialize_lock:
        if _db_initialized:
            return

        _DB_DIR.mkdir(parents=True, exist_ok=True)
        import src.common.database.database_model  # noqa: F401

        migration_state = _migration_bootstrapper.prepare_database()
        logger.info(
            "数据库迁移准备完成，"
            f" 当前版本={migration_state.resolved_version.version}，目标版本={migration_state.target_version}"
        )
        create_all_start_time = perf_counter()
        SQLModel.metadata.create_all(engine)
        logger.info(f"数据库模型建表检查完成，耗时={int((perf_counter() - create_all_start_time) * 1000)}ms")
        finalize_start_time = perf_counter()
        _migration_bootstrapper.finalize_database(migration_state)
        logger.info(f"数据库 schema 版本收尾完成，耗时={int((perf_counter() - finalize_start_time) * 1000)}ms")
        _db_initialized = True


@contextmanager
def get_db_session(auto_commit: bool = True) -> Generator[Session, None, None]:
    """
    获取数据库会话的上下文管理器 (推荐使用,自动提交)。

    Examples:
    ----
    .. code-block:: python
        # 方式1: 自动提交 (推荐 - 默认行为)
        with get_db_session() as session:
            user = User(name="张三", age=25)
            session.add(user)
            # 退出时自动 commit,无需手动调用

        # 方式2: 手动控制事务 (高级用法)
        with get_db_session(auto_commit=False) as session:
            user1 = User(name="张三", age=25)
            user2 = User(name="李四", age=30)
            session.add_all([user1, user2])
            session.commit()  # 手动提交

    Args:
        auto_commit (bool): 是否在退出上下文时自动提交（默认: True）。

    Yields:
        Session: SQLAlchemy 数据库会话

    注意:
        - 会话会在退出上下文时自动关闭
        - 如果发生异常，会自动回滚事务
        - auto_commit=True 时,成功执行完会自动提交
        - auto_commit=False 时,需要手动调用 session.commit()
    """
    initialize_database()
    session = SessionLocal()
    try:
        yield session
        if auto_commit:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session_manual() -> ContextManager[Session]:
    """获取数据库会话的上下文管理器 (手动提交模式)。

    Returns:
        ContextManager[Session]: 手动提交模式的数据库会话上下文管理器。
    """
    return get_db_session(auto_commit=False)


def get_db() -> Generator[Session, None, None]:
    """
    获取数据库会话的生成器函数。

    适用于依赖注入场景(如 FastAPI)。

    使用示例 (FastAPI):
    ----
    .. code-block:: python
        @app.get("/users/{user_id}")
        def read_user(user_id: int, db: Session = Depends(get_db)):
            return db.get(User, user_id)

    Yields:
        Session: SQLAlchemy 数据库会话
    """
    initialize_database()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
