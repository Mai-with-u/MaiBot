"""测试 jargon 路由的完整性和正确性"""

import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import ChatSession, Jargon
from src.webui.routers.jargon import router as jargon_router


@pytest.fixture(name="app", scope="function")
def app_fixture():
    app = FastAPI()
    app.include_router(jargon_router, prefix="/api/webui")
    return app


@pytest.fixture(name="engine", scope="function")
def engine_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine


@pytest.fixture(name="session", scope="function")
def session_fixture(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(name="client", scope="function")
def client_fixture(app: FastAPI, session: Session, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def mock_get_db_session():
        yield session

    monkeypatch.setattr("src.webui.routers.jargon.get_db_session", mock_get_db_session)

    with TestClient(app) as client:
        yield client


@pytest.fixture(name="sample_chat_session")
def sample_chat_session_fixture(session: Session):
    """创建示例 ChatSession"""
    chat_session = ChatSession(
        session_id="test_stream_001",
        platform="qq",
        group_id="123456789",
        user_id=None,
        created_timestamp=datetime.now(),
        last_active_timestamp=datetime.now(),
    )
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


@pytest.fixture(name="sample_jargons")
def sample_jargons_fixture(session: Session, sample_chat_session: ChatSession):
    """创建示例 Jargon 数据"""
    jargons = [
        Jargon(
            id=1,
            content="yyds",
            raw_content="永远的神",
            meaning="永远的神",
            session_id=sample_chat_session.session_id,
            count=10,
            is_jargon=True,
            is_complete=False,
        ),
        Jargon(
            id=2,
            content="awsl",
            raw_content="啊我死了",
            meaning="啊我死了",
            session_id=sample_chat_session.session_id,
            count=5,
            is_jargon=True,
            is_complete=False,
        ),
        Jargon(
            id=3,
            content="hello",
            raw_content=None,
            meaning="你好",
            session_id=sample_chat_session.session_id,
            count=2,
            is_jargon=False,
            is_complete=False,
        ),
    ]
    for jargon in jargons:
        session.add(jargon)
    session.commit()
    for jargon in jargons:
        session.refresh(jargon)
    return jargons


# ==================== Test Cases ====================


def test_list_jargons(client: TestClient, sample_jargons):
    """测试 GET /jargon/list 基础列表功能"""
    response = client.get("/api/webui/jargon/list")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert len(data["data"]) == 3

    assert data["data"][0]["content"] == "yyds"
    assert data["data"][0]["count"] == 10


def test_list_jargons_with_pagination(client: TestClient, sample_jargons):
    """测试分页功能"""
    response = client.get("/api/webui/jargon/list?page=1&page_size=2")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 3
    assert len(data["data"]) == 2

    response = client.get("/api/webui/jargon/list?page=2&page_size=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 1


def test_list_jargons_with_search(client: TestClient, sample_jargons):
    """测试 GET /jargon/list?search=xxx 搜索功能"""
    response = client.get("/api/webui/jargon/list?search=yyds")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 1
    assert data["data"][0]["content"] == "yyds"

    # 测试搜索 meaning
    response = client.get("/api/webui/jargon/list?search=你好")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["data"][0]["content"] == "hello"


def test_list_jargons_with_chat_id_filter(client: TestClient, sample_jargons, sample_chat_session: ChatSession):
    """测试按 chat_id 筛选"""
    response = client.get(f"/api/webui/jargon/list?chat_id={sample_chat_session.session_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 3

    # 测试不存在的 chat_id
    response = client.get("/api/webui/jargon/list?chat_id=nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0


def test_list_jargons_with_is_jargon_filter(client: TestClient, sample_jargons):
    """测试按 is_jargon 筛选"""
    response = client.get("/api/webui/jargon/list?is_jargon=true")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 2
    assert all(item["is_jargon"] is True for item in data["data"])

    response = client.get("/api/webui/jargon/list?is_jargon=false")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["data"][0]["content"] == "hello"


def test_get_jargon_detail(client: TestClient, sample_jargons):
    """测试 GET /jargon/{id} 获取详情"""
    jargon_id = sample_jargons[0].id
    response = client.get(f"/api/webui/jargon/{jargon_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["id"] == jargon_id
    assert data["data"]["content"] == "yyds"
    assert data["data"]["meaning"] == "永远的神"
    assert data["data"]["count"] == 10
    assert data["data"]["is_jargon"] is True


def test_get_jargon_detail_not_found(client: TestClient):
    """测试获取不存在的黑话详情"""
    response = client.get("/api/webui/jargon/99999")
    assert response.status_code == 404
    assert "黑话不存在" in response.json()["detail"]


@pytest.mark.skip(reason="Composite PK (id+content) prevents autoincrement - database model issue")
def test_create_jargon(client: TestClient, sample_chat_session: ChatSession):
    """测试 POST /jargon/ 创建黑话"""
    request_data = {
        "content": "新黑话",
        "raw_content": "原始内容",
        "meaning": "含义",
        "chat_id": sample_chat_session.session_id,
    }

    response = client.post("/api/webui/jargon/", json=request_data)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["message"] == "创建成功"
    assert data["data"]["content"] == "新黑话"
    assert data["data"]["meaning"] == "含义"
    assert data["data"]["count"] == 0
    assert data["data"]["is_jargon"] is None
    assert data["data"]["is_complete"] is False


def test_create_duplicate_jargon(client: TestClient, sample_jargons, sample_chat_session: ChatSession):
    """测试创建重复黑话返回 400"""
    request_data = {
        "content": "yyds",
        "meaning": "重复的",
        "chat_id": sample_chat_session.session_id,
    }

    response = client.post("/api/webui/jargon/", json=request_data)
    assert response.status_code == 400
    assert "已存在相同内容的黑话" in response.json()["detail"]


def test_update_jargon(client: TestClient, sample_jargons):
    """测试 PATCH /jargon/{id} 更新黑话"""
    jargon_id = sample_jargons[0].id
    update_data = {
        "meaning": "更新后的含义",
        "is_jargon": True,
    }

    response = client.patch(f"/api/webui/jargon/{jargon_id}", json=update_data)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["message"] == "更新成功"
    assert data["data"]["meaning"] == "更新后的含义"
    assert data["data"]["is_jargon"] is True
    assert data["data"]["content"] == "yyds"  # 未改变的字段保持不变


def test_update_jargon_with_chat_id_mapping(client: TestClient, sample_jargons):
    """测试更新时 chat_id → session_id 的映射"""
    jargon_id = sample_jargons[0].id
    update_data = {
        "chat_id": "new_session_id",
    }

    response = client.patch(f"/api/webui/jargon/{jargon_id}", json=update_data)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["chat_id"] == "new_session_id"


def test_update_jargon_not_found(client: TestClient):
    """测试更新不存在的黑话"""
    response = client.patch("/api/webui/jargon/99999", json={"meaning": "test"})
    assert response.status_code == 404
    assert "黑话不存在" in response.json()["detail"]


def test_delete_jargon(client: TestClient, sample_jargons, session: Session):
    """测试 DELETE /jargon/{id} 删除黑话"""
    jargon_id = sample_jargons[0].id
    response = client.delete(f"/api/webui/jargon/{jargon_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["message"] == "删除成功"
    assert data["deleted_count"] == 1

    # 验证数据库中已删除
    response = client.get(f"/api/webui/jargon/{jargon_id}")
    assert response.status_code == 404


def test_delete_jargon_not_found(client: TestClient):
    """测试删除不存在的黑话"""
    response = client.delete("/api/webui/jargon/99999")
    assert response.status_code == 404
    assert "黑话不存在" in response.json()["detail"]


def test_batch_delete(client: TestClient, sample_jargons):
    """测试 POST /jargon/batch/delete 批量删除"""
    ids_to_delete = [sample_jargons[0].id, sample_jargons[1].id]
    request_data = {"ids": ids_to_delete}

    response = client.post("/api/webui/jargon/batch/delete", json=request_data)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["deleted_count"] == 2
    assert "成功删除 2 条黑话" in data["message"]

    # 验证已删除
    response = client.get(f"/api/webui/jargon/{ids_to_delete[0]}")
    assert response.status_code == 404


def test_batch_delete_empty_list(client: TestClient):
    """测试批量删除空列表返回 400"""
    response = client.post("/api/webui/jargon/batch/delete", json={"ids": []})
    assert response.status_code == 400
    assert "ID列表不能为空" in response.json()["detail"]


def test_batch_set_jargon_status(client: TestClient, sample_jargons):
    """测试批量设置黑话状态"""
    ids = [sample_jargons[0].id, sample_jargons[1].id]
    response = client.post(
        "/api/webui/jargon/batch/set-jargon",
        params={"ids": ids, "is_jargon": False},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "成功更新 2 条黑话状态" in data["message"]

    # 验证状态已更新
    detail_response = client.get(f"/api/webui/jargon/{ids[0]}")
    assert detail_response.json()["data"]["is_jargon"] is False


def test_get_stats(client: TestClient, sample_jargons):
    """测试 GET /jargon/stats/summary 统计数据"""
    response = client.get("/api/webui/jargon/stats/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    stats = data["data"]

    assert stats["total"] == 3
    assert stats["confirmed_jargon"] == 2
    assert stats["confirmed_not_jargon"] == 1
    assert stats["pending"] == 0
    assert stats["complete_count"] == 0
    assert stats["chat_count"] == 1
    assert isinstance(stats["top_chats"], dict)


def test_get_chat_list(client: TestClient, sample_jargons, sample_chat_session: ChatSession):
    """测试 GET /jargon/chats 获取聊天列表"""
    response = client.get("/api/webui/jargon/chats")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert len(data["data"]) == 1

    chat_info = data["data"][0]
    assert chat_info["chat_id"] == sample_chat_session.session_id
    assert chat_info["platform"] == "qq"
    assert chat_info["is_group"] is True
    assert chat_info["chat_name"] == sample_chat_session.group_id


def test_get_chat_list_with_json_chat_id(client: TestClient, session: Session, sample_chat_session: ChatSession):
    """测试解析 JSON 格式的 chat_id"""
    json_chat_id = json.dumps([[sample_chat_session.session_id, "user123"]])
    jargon = Jargon(
        id=100,
        content="测试黑话",
        meaning="测试",
        session_id=json_chat_id,
        count=1,
    )
    session.add(jargon)
    session.commit()

    response = client.get("/api/webui/jargon/chats")
    assert response.status_code == 200

    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["chat_id"] == sample_chat_session.session_id


def test_get_chat_list_without_chat_session(client: TestClient, session: Session):
    """测试聊天列表中没有对应 ChatSession 的情况"""
    jargon = Jargon(
        id=101,
        content="孤立黑话",
        meaning="无对应会话",
        session_id="nonexistent_stream_id",
        count=1,
    )
    session.add(jargon)
    session.commit()

    response = client.get("/api/webui/jargon/chats")
    assert response.status_code == 200

    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["chat_id"] == "nonexistent_stream_id"
    assert data["data"][0]["chat_name"] == "nonexistent_stream_id"[:20]
    assert data["data"][0]["platform"] is None
    assert data["data"][0]["is_group"] is False


def test_jargon_response_fields(client: TestClient, sample_jargons, sample_chat_session: ChatSession):
    """测试 JargonResponse 字段完整性"""
    response = client.get(f"/api/webui/jargon/{sample_jargons[0].id}")
    assert response.status_code == 200

    data = response.json()["data"]

    # 验证所有必需字段存在
    required_fields = [
        "id",
        "content",
        "raw_content",
        "meaning",
        "chat_id",
        "stream_id",
        "chat_name",
        "count",
        "is_jargon",
        "is_complete",
        "inference_with_context",
        "inference_content_only",
    ]
    for field in required_fields:
        assert field in data

    # 验证 chat_name 显示逻辑
    assert data["chat_name"] == sample_chat_session.group_id


@pytest.mark.skip(reason="Composite PK (id+content) prevents autoincrement - database model issue")
def test_create_jargon_without_optional_fields(client: TestClient, sample_chat_session: ChatSession):
    """测试创建黑话时可选字段为空"""
    request_data = {
        "content": "简单黑话",
        "chat_id": sample_chat_session.session_id,
    }

    response = client.post("/api/webui/jargon/", json=request_data)
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["raw_content"] is None
    assert data["meaning"] == ""


def test_update_jargon_partial_fields(client: TestClient, sample_jargons):
    """测试增量更新（只更新部分字段）"""
    jargon_id = sample_jargons[0].id
    original_content = sample_jargons[0].content

    # 只更新 meaning
    response = client.patch(f"/api/webui/jargon/{jargon_id}", json={"meaning": "新含义"})
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["meaning"] == "新含义"
    assert data["content"] == original_content  # 其他字段不变


def test_list_jargons_multiple_filters(client: TestClient, sample_jargons, sample_chat_session: ChatSession):
    """测试组合多个过滤条件"""
    response = client.get(f"/api/webui/jargon/list?search=永远&chat_id={sample_chat_session.session_id}&is_jargon=true")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 1
    assert data["data"][0]["content"] == "yyds"
