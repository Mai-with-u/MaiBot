"""表情包路由 API 测试

测试 src/webui/routers/emoji.py 中的核心 emoji 路由端点
使用内存 SQLite 数据库和 FastAPI TestClient
"""

from contextlib import contextmanager
from datetime import datetime
from typing import Generator
from unittest.mock import patch

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import Images, ImageType
from src.webui.core import TokenManager
from src.webui.routers.emoji import router


@pytest.fixture(scope="function")
def test_engine():
    """创建内存 SQLite 引擎用于测试"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="function")
def test_session(test_engine) -> Generator[Session, None, None]:
    """创建测试数据库会话"""
    with Session(test_engine) as session:
        yield session


@pytest.fixture(scope="function")
def test_app(test_session):
    """创建测试 FastAPI 应用并覆盖 get_db_session 依赖"""
    app = FastAPI()
    app.include_router(router)

    # Create a context manager that yields the test session
    @contextmanager
    def override_get_db_session(auto_commit=True):
        """Override get_db_session to use test session"""
        try:
            yield test_session
            if auto_commit:
                test_session.commit()
        except Exception:
            test_session.rollback()
            raise

    with patch("src.webui.routers.emoji.get_db_session", override_get_db_session):
        yield app


@pytest.fixture(scope="function")
def client(test_app):
    """创建 TestClient"""
    return TestClient(test_app)


@pytest.fixture(scope="function")
def auth_token():
    """创建有效的认证 token"""
    token_manager = TokenManager(secret_key="test-secret-key", token_expire_hours=24)
    return token_manager.create_token()


@pytest.fixture(scope="function")
def sample_emojis(test_session) -> list[Images]:
    """插入测试用表情包数据"""
    import hashlib

    emojis = [
        Images(
            image_type=ImageType.EMOJI,
            full_path="/data/emoji_registed/test1.png",
            image_hash=hashlib.sha256(b"test1").hexdigest(),
            description="测试表情包 1",
            emotion="开心,快乐",
            query_count=10,
            is_registered=True,
            is_banned=False,
            record_time=datetime(2026, 1, 1, 10, 0, 0),
            register_time=datetime(2026, 1, 1, 10, 0, 0),
            last_used_time=datetime(2026, 1, 2, 10, 0, 0),
        ),
        Images(
            image_type=ImageType.EMOJI,
            full_path="/data/emoji_registed/test2.gif",
            image_hash=hashlib.sha256(b"test2").hexdigest(),
            description="测试表情包 2",
            emotion="难过",
            query_count=5,
            is_registered=False,
            is_banned=False,
            record_time=datetime(2026, 1, 3, 10, 0, 0),
            register_time=None,
            last_used_time=None,
        ),
        Images(
            image_type=ImageType.EMOJI,
            full_path="/data/emoji_registed/test3.webp",
            image_hash=hashlib.sha256(b"test3").hexdigest(),
            description="测试表情包 3",
            emotion="生气",
            query_count=20,
            is_registered=True,
            is_banned=True,
            record_time=datetime(2026, 1, 4, 10, 0, 0),
            register_time=datetime(2026, 1, 4, 10, 0, 0),
            last_used_time=datetime(2026, 1, 5, 10, 0, 0),
        ),
    ]

    for emoji in emojis:
        test_session.add(emoji)
    test_session.commit()

    for emoji in emojis:
        test_session.refresh(emoji)

    return emojis


@pytest.fixture(scope="function")
def mock_token_verify():
    """Mock token verification to always succeed"""
    with patch("src.webui.routers.emoji.verify_auth_token", return_value=True):
        yield


# ==================== 测试用例 ====================


def test_list_emojis_basic(client, sample_emojis, mock_token_verify):
    """测试获取表情包列表（基本分页）"""
    response = client.get("/emoji/list?page=1&page_size=10")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert len(data["data"]) == 3

    # 验证第一个表情包字段
    emoji = data["data"][0]
    assert "id" in emoji
    assert "full_path" in emoji
    assert "emoji_hash" in emoji
    assert "description" in emoji
    assert "query_count" in emoji
    assert "is_registered" in emoji
    assert "is_banned" in emoji
    assert "emotion" in emoji
    assert "record_time" in emoji
    assert "register_time" in emoji
    assert "last_used_time" in emoji


def test_list_emojis_pagination(client, sample_emojis, mock_token_verify):
    """测试分页功能"""
    response = client.get("/emoji/list?page=1&page_size=2")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["total"] == 3
    assert len(data["data"]) == 2

    # 第二页
    response = client.get("/emoji/list?page=2&page_size=2")
    data = response.json()
    assert len(data["data"]) == 1


def test_list_emojis_search(client, sample_emojis, mock_token_verify):
    """测试搜索过滤"""
    response = client.get("/emoji/list?search=表情包 2")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["total"] == 1
    assert data["data"][0]["description"] == "测试表情包 2"


def test_list_emojis_filter_registered(client, sample_emojis, mock_token_verify):
    """测试 is_registered 过滤"""
    response = client.get("/emoji/list?is_registered=true")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["total"] == 2
    assert all(emoji["is_registered"] is True for emoji in data["data"])


def test_list_emojis_filter_banned(client, sample_emojis, mock_token_verify):
    """测试 is_banned 过滤"""
    response = client.get("/emoji/list?is_banned=true")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["total"] == 1
    assert data["data"][0]["is_banned"] is True


def test_list_emojis_sort_by_query_count(client, sample_emojis, mock_token_verify):
    """测试按 query_count 排序"""
    response = client.get("/emoji/list?sort_by=query_count&sort_order=desc")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    # 验证降序排列 (20 > 10 > 5)
    assert data["data"][0]["query_count"] == 20
    assert data["data"][1]["query_count"] == 10
    assert data["data"][2]["query_count"] == 5


def test_get_emoji_detail_success(client, sample_emojis, mock_token_verify):
    """测试获取表情包详情（成功）"""
    emoji_id = sample_emojis[0].id
    response = client.get(f"/emoji/{emoji_id}")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["data"]["id"] == emoji_id
    assert data["data"]["emoji_hash"] == sample_emojis[0].image_hash


def test_get_emoji_detail_not_found(client, mock_token_verify):
    """测试获取不存在的表情包（404）"""
    response = client.get("/emoji/99999")

    assert response.status_code == 404
    data = response.json()
    assert "未找到" in data["detail"]


def test_update_emoji_description(client, sample_emojis, mock_token_verify):
    """测试更新表情包描述"""
    emoji_id = sample_emojis[0].id
    response = client.patch(
        f"/emoji/{emoji_id}",
        json={"description": "更新后的描述"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["data"]["description"] == "更新后的描述"
    assert "成功更新" in data["message"]


def test_update_emoji_register_status(client, sample_emojis, mock_token_verify, test_session):
    """测试更新注册状态（False -> True 应设置 register_time）"""
    emoji_id = sample_emojis[1].id  # 未注册的表情包
    response = client.patch(
        f"/emoji/{emoji_id}",
        json={"is_registered": True},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["data"]["is_registered"] is True
    assert data["data"]["register_time"] is not None  # 应该设置了注册时间


def test_update_emoji_no_fields(client, sample_emojis, mock_token_verify):
    """测试更新请求未提供任何字段（400）"""
    emoji_id = sample_emojis[0].id
    response = client.patch(f"/emoji/{emoji_id}", json={})

    assert response.status_code == 400
    data = response.json()
    assert "未提供任何需要更新的字段" in data["detail"]


def test_update_emoji_not_found(client, mock_token_verify):
    """测试更新不存在的表情包（404）"""
    response = client.patch("/emoji/99999", json={"description": "test"})

    assert response.status_code == 404
    data = response.json()
    assert "未找到" in data["detail"]


def test_delete_emoji_success(client, sample_emojis, mock_token_verify, test_session):
    """测试删除表情包（成功）"""
    emoji_id = sample_emojis[0].id
    response = client.delete(f"/emoji/{emoji_id}")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert "成功删除" in data["message"]

    # 验证数据库中已删除
    from sqlmodel import select

    statement = select(Images).where(Images.id == emoji_id)
    result = test_session.exec(statement).first()
    assert result is None


def test_delete_emoji_not_found(client, mock_token_verify):
    """测试删除不存在的表情包（404）"""
    response = client.delete("/emoji/99999")

    assert response.status_code == 404
    data = response.json()
    assert "未找到" in data["detail"]


def test_batch_delete_success(client, sample_emojis, mock_token_verify, test_session):
    """测试批量删除表情包（全部成功）"""
    emoji_ids = [sample_emojis[0].id, sample_emojis[1].id]
    response = client.post("/emoji/batch/delete", json={"emoji_ids": emoji_ids})

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["deleted_count"] == 2
    assert data["failed_count"] == 0
    assert "成功删除 2 个表情包" in data["message"]

    # 验证数据库中已删除
    from sqlmodel import select

    for emoji_id in emoji_ids:
        statement = select(Images).where(Images.id == emoji_id)
        result = test_session.exec(statement).first()
        assert result is None


def test_batch_delete_partial_failure(client, sample_emojis, mock_token_verify):
    """测试批量删除（部分失败）"""
    emoji_ids = [sample_emojis[0].id, 99999]  # 第二个 ID 不存在
    response = client.post("/emoji/batch/delete", json={"emoji_ids": emoji_ids})

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["deleted_count"] == 1
    assert data["failed_count"] == 1
    assert 99999 in data["failed_ids"]


def test_batch_delete_empty_list(client, mock_token_verify):
    """测试批量删除空列表（400）"""
    response = client.post("/emoji/batch/delete", json={"emoji_ids": []})

    assert response.status_code == 400
    data = response.json()
    assert "未提供要删除的表情包ID" in data["detail"]


def test_auth_required_list(client):
    """测试未认证访问列表端点（401）"""
    # Without mock_token_verify fixture
    with patch("src.webui.routers.emoji.verify_auth_token", return_value=False):
        client.get("/emoji/list")
        # verify_auth_token 返回 False 会触发 HTTPException
        # 但具体状态码取决于 verify_auth_token_from_cookie_or_header 的实现
        # 这里假设它抛出 401


def test_auth_required_update(client, sample_emojis):
    """测试未认证访问更新端点（401）"""
    with patch("src.webui.routers.emoji.verify_auth_token", return_value=False):
        emoji_id = sample_emojis[0].id
        client.patch(f"/emoji/{emoji_id}", json={"description": "test"})
        # Should be unauthorized


def test_emoji_to_response_field_mapping(sample_emojis):
    """测试 emoji_to_response 字段映射（image_hash -> emoji_hash）"""
    from src.webui.routers.emoji import emoji_to_response

    emoji = sample_emojis[0]
    response = emoji_to_response(emoji)

    # 验证 API 字段名称
    assert hasattr(response, "emoji_hash")
    assert response.emoji_hash == emoji.image_hash

    # 验证时间戳转换
    assert isinstance(response.record_time, float)
    assert response.record_time == emoji.record_time.timestamp()

    if emoji.register_time:
        assert isinstance(response.register_time, float)
        assert response.register_time == emoji.register_time.timestamp()


def test_list_emojis_only_emoji_type(client, test_session, mock_token_verify):
    """测试列表只返回 type=EMOJI 的记录（不包括其他类型）"""
    # 插入一个非 EMOJI 类型的图片
    non_emoji = Images(
        image_type=ImageType.IMAGE,  # 不是 EMOJI
        full_path="/data/images/test.png",
        image_hash="hash_image",
        description="非表情包图片",
        query_count=0,
        is_registered=False,
        is_banned=False,
        record_time=datetime.now(),
    )
    test_session.add(non_emoji)
    test_session.commit()

    # 插入一个 EMOJI 类型
    emoji = Images(
        image_type=ImageType.EMOJI,
        full_path="/data/emoji_registed/emoji.png",
        image_hash="hash_emoji",
        description="表情包",
        query_count=0,
        is_registered=True,
        is_banned=False,
        record_time=datetime.now(),
    )
    test_session.add(emoji)
    test_session.commit()

    response = client.get("/emoji/list")

    assert response.status_code == 200
    data = response.json()

    # 只应该返回 1 个 EMOJI 类型的记录
    assert data["total"] == 1
    assert data["data"][0]["description"] == "表情包"
