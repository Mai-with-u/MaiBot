from rich.traceback import install
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session, create_engine

if TYPE_CHECKING:
    from sqlite3 import Connection as SQLite3Connection

install(extra_lines=3)


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
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
)

_db_initialized = False


def initialize_database() -> None:
    global _db_initialized
    if _db_initialized:
        return
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    import src.common.database.database_model  # noqa: F401

    SQLModel.metadata.create_all(engine)
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


def get_db_session_manual():
    """获取数据库会话的上下文管理器 (手动提交模式)。"""
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
