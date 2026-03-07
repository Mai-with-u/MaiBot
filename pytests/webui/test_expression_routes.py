"""Expression routes pytest tests"""

from typing import Generator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from src.common.database.database_model import Expression


def create_test_app() -> FastAPI:
    """Create minimal test app with only expression router"""
    app = FastAPI(title="Test App")
    from src.webui.routers.expression import router as expression_router

    main_router = APIRouter(prefix="/api/webui")
    main_router.include_router(expression_router)
    app.include_router(main_router)

    return app


app = create_test_app()


# Test database setup
@pytest.fixture(name="test_engine")
def test_engine_fixture():
    """Create in-memory SQLite database for testing"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="test_session")
def test_session_fixture(test_engine) -> Generator[Session, None, None]:
    """Create a test database session with transaction rollback"""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(name="client")
def client_fixture(test_session: Session, monkeypatch) -> Generator[TestClient, None, None]:
    """Create TestClient with overridden database session"""
    from contextlib import contextmanager

    @contextmanager
    def get_test_db_session():
        yield test_session

    monkeypatch.setattr("src.webui.routers.expression.get_db_session", get_test_db_session)

    with TestClient(app) as client:
        yield client


@pytest.fixture(name="mock_auth")
def mock_auth_fixture(monkeypatch):
    """Mock authentication to always return True"""
    mock_verify = MagicMock(return_value=True)
    monkeypatch.setattr("src.webui.routers.expression.verify_auth_token_from_cookie_or_header", mock_verify)


@pytest.fixture(name="sample_expression")
def sample_expression_fixture(test_session: Session) -> Expression:
    """Insert a sample expression into test database"""
    test_session.execute(
        text(
            "INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
            "VALUES (1, '测试情景', '测试风格', '测试上下文', '测试上文', '[\"测试内容1\", \"测试内容2\"]', 10, '2026-02-17 12:00:00', '2026-02-15 10:00:00', 'test_chat_001')"
        )
    )
    test_session.commit()

    expression = test_session.exec(select(Expression).where(Expression.id == 1)).first()
    assert expression is not None
    return expression


# ============ Tests ============


def test_list_expressions_empty(client: TestClient, mock_auth):
    """Test GET /expression/list with empty database"""
    response = client.get("/api/webui/expression/list")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["data"] == []


def test_list_expressions_with_data(client: TestClient, mock_auth, sample_expression: Expression):
    """Test GET /expression/list returns expression data"""
    response = client.get("/api/webui/expression/list")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["total"] == 1
    assert len(data["data"]) == 1

    expr_data = data["data"][0]
    assert expr_data["id"] == sample_expression.id
    assert expr_data["situation"] == "测试情景"
    assert expr_data["style"] == "测试风格"
    assert expr_data["chat_id"] == "test_chat_001"


def test_list_expressions_pagination(client: TestClient, mock_auth, test_session: Session):
    """Test GET /expression/list pagination works correctly"""
    for i in range(5):
        test_session.execute(
            text(
                f"INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
                f"VALUES ({i + 1}, '情景{i}', '风格{i}', '', '', '[]', 0, '2026-02-17 12:0{i}:00', '2026-02-15 10:00:00', 'chat_{i}')"
            )
        )
    test_session.commit()

    # Request page 1 with page_size=2
    response = client.get("/api/webui/expression/list?page=1&page_size=2")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 5
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["data"]) == 2

    # Request page 2
    response = client.get("/api/webui/expression/list?page=2&page_size=2")
    data = response.json()
    assert data["page"] == 2
    assert len(data["data"]) == 2


def test_list_expressions_search(client: TestClient, mock_auth, test_session: Session):
    """Test GET /expression/list with search filter"""
    test_session.execute(
        text(
            "INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
            "VALUES (1, '找人吃饭', '热情', '', '', '[]', 0, datetime('now'), datetime('now'), 'chat_001')"
        )
    )
    test_session.execute(
        text(
            "INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
            "VALUES (2, '拒绝邀请', '礼貌', '', '', '[]', 0, datetime('now'), datetime('now'), 'chat_002')"
        )
    )
    test_session.commit()

    # Search for "吃饭"
    response = client.get("/api/webui/expression/list?search=吃饭")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 1
    assert data["data"][0]["situation"] == "找人吃饭"


def test_list_expressions_chat_filter(client: TestClient, mock_auth, test_session: Session):
    """Test GET /expression/list with chat_id filter"""
    test_session.execute(
        text(
            "INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
            "VALUES (1, '情景A', '风格A', '', '', '[]', 0, datetime('now'), datetime('now'), 'chat_A')"
        )
    )
    test_session.execute(
        text(
            "INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
            "VALUES (2, '情景B', '风格B', '', '', '[]', 0, datetime('now'), datetime('now'), 'chat_B')"
        )
    )
    test_session.commit()

    # Filter by chat_A
    response = client.get("/api/webui/expression/list?chat_id=chat_A")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 1
    assert data["data"][0]["situation"] == "情景A"
    assert data["data"][0]["chat_id"] == "chat_A"


def test_get_expression_detail_success(client: TestClient, mock_auth, sample_expression: Expression):
    """Test GET /expression/{id} returns correct detail"""
    response = client.get(f"/api/webui/expression/{sample_expression.id}")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["id"] == sample_expression.id
    assert data["data"]["situation"] == "测试情景"
    assert data["data"]["style"] == "测试风格"
    assert data["data"]["chat_id"] == "test_chat_001"


def test_get_expression_detail_not_found(client: TestClient, mock_auth):
    """Test GET /expression/{id} returns 404 for non-existent ID"""
    response = client.get("/api/webui/expression/99999")
    assert response.status_code == 404

    data = response.json()
    assert "未找到" in data["detail"]


def test_expression_response_has_legacy_fields(client: TestClient, mock_auth, sample_expression: Expression):
    """Test that ExpressionResponse includes legacy fields (checked/rejected/modified_by)"""
    response = client.get(f"/api/webui/expression/{sample_expression.id}")
    assert response.status_code == 200

    data = response.json()["data"]

    # Verify legacy fields exist and have default values
    assert "checked" in data
    assert "rejected" in data
    assert "modified_by" in data

    # Verify hardcoded default values (from expression_to_response)
    assert data["checked"] is False
    assert data["rejected"] is False
    assert data["modified_by"] is None


def test_update_expression_without_removed_fields(client: TestClient, mock_auth, sample_expression: Expression):
    """Test PATCH /expression/{id} does not accept checked/rejected fields"""
    # Valid update request (only allowed fields)
    update_payload = {
        "situation": "更新后的情景",
        "style": "更新后的风格",
    }

    response = client.patch(f"/api/webui/expression/{sample_expression.id}", json=update_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["situation"] == "更新后的情景"
    assert data["data"]["style"] == "更新后的风格"

    # Verify legacy fields still returned (hardcoded values)
    assert data["data"]["checked"] is False
    assert data["data"]["rejected"] is False


def test_update_expression_ignores_invalid_fields(client: TestClient, mock_auth, sample_expression: Expression):
    """Test PATCH /expression/{id} ignores fields not in ExpressionUpdateRequest"""
    # Request with invalid field (checked not in schema)
    update_payload = {
        "situation": "新情景",
        "checked": True,  # This field should be ignored by Pydantic
        "rejected": True,  # This field should be ignored
    }

    response = client.patch(f"/api/webui/expression/{sample_expression.id}", json=update_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["situation"] == "新情景"

    # Response should have hardcoded False values (not True from request)
    assert data["data"]["checked"] is False
    assert data["data"]["rejected"] is False


def test_update_expression_chat_id_mapping(client: TestClient, mock_auth, sample_expression: Expression):
    """Test PATCH /expression/{id} correctly maps chat_id to session_id"""
    update_payload = {"chat_id": "updated_chat_999"}

    response = client.patch(f"/api/webui/expression/{sample_expression.id}", json=update_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True

    # Verify chat_id is returned in response (mapped from session_id)
    assert data["data"]["chat_id"] == "updated_chat_999"


def test_update_expression_not_found(client: TestClient, mock_auth):
    """Test PATCH /expression/{id} returns 404 for non-existent ID"""
    update_payload = {"situation": "新情景"}

    response = client.patch("/api/webui/expression/99999", json=update_payload)
    assert response.status_code == 404

    data = response.json()
    assert "未找到" in data["detail"]


def test_update_expression_empty_request(client: TestClient, mock_auth, sample_expression: Expression):
    """Test PATCH /expression/{id} returns 400 for empty update request"""
    update_payload = {}

    response = client.patch(f"/api/webui/expression/{sample_expression.id}", json=update_payload)
    assert response.status_code == 400

    data = response.json()
    assert "未提供任何需要更新的字段" in data["detail"]


def test_delete_expression_success(client: TestClient, mock_auth, sample_expression: Expression):
    """Test DELETE /expression/{id} successfully deletes expression"""
    expression_id = sample_expression.id

    response = client.delete(f"/api/webui/expression/{expression_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "成功删除" in data["message"]

    # Verify expression is deleted
    get_response = client.get(f"/api/webui/expression/{expression_id}")
    assert get_response.status_code == 404


def test_delete_expression_not_found(client: TestClient, mock_auth):
    """Test DELETE /expression/{id} returns 404 for non-existent ID"""
    response = client.delete("/api/webui/expression/99999")
    assert response.status_code == 404

    data = response.json()
    assert "未找到" in data["detail"]


def test_create_expression_success(client: TestClient, mock_auth):
    """Test POST /expression/ successfully creates expression"""
    create_payload = {
        "situation": "新建情景",
        "style": "新建风格",
        "chat_id": "new_chat_123",
    }

    response = client.post("/api/webui/expression/", json=create_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "创建成功" in data["message"]
    assert data["data"]["situation"] == "新建情景"
    assert data["data"]["style"] == "新建风格"
    assert data["data"]["chat_id"] == "new_chat_123"

    # Verify legacy fields
    assert data["data"]["checked"] is False
    assert data["data"]["rejected"] is False
    assert data["data"]["modified_by"] is None


def test_batch_delete_expressions_success(client: TestClient, mock_auth, test_session: Session):
    """Test POST /expression/batch/delete successfully deletes multiple expressions"""
    expression_ids = []
    for i in range(3):
        test_session.execute(
            text(
                f"INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
                f"VALUES ({i + 1}, '批量删除{i}', '风格{i}', '', '', '[]', 0, datetime('now'), datetime('now'), 'chat_{i}')"
            )
        )
        expression_ids.append(i + 1)
    test_session.commit()

    delete_payload = {"ids": expression_ids}
    response = client.post("/api/webui/expression/batch/delete", json=delete_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "成功删除 3 个" in data["message"]

    for expr_id in expression_ids:
        get_response = client.get(f"/api/webui/expression/{expr_id}")
        assert get_response.status_code == 404


def test_batch_delete_partial_not_found(client: TestClient, mock_auth, sample_expression: Expression):
    """Test POST /expression/batch/delete handles partial not found IDs"""
    delete_payload = {"ids": [sample_expression.id, 88888, 99999]}

    response = client.post("/api/webui/expression/batch/delete", json=delete_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    # Should delete only the 1 valid ID
    assert "成功删除 1 个" in data["message"]


def test_get_expression_stats(client: TestClient, mock_auth, test_session: Session):
    """Test GET /expression/stats/summary returns correct statistics"""
    for i in range(3):
        test_session.execute(
            text(
                f"INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
                f"VALUES ({i + 1}, '情景{i}', '风格{i}', '', '', '[]', 0, datetime('now'), datetime('now'), 'chat_{i % 2}')"
            )
        )
    test_session.commit()

    response = client.get("/api/webui/expression/stats/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["total"] == 3
    assert data["data"]["chat_count"] == 2


def test_get_review_stats(client: TestClient, mock_auth, test_session: Session):
    """Test GET /expression/review/stats returns hardcoded 0 counts"""
    test_session.execute(
        text(
            "INSERT INTO expressions (id, situation, style, context, up_content, content_list, count, last_active_time, create_time, session_id) "
            "VALUES (1, '待审核', '风格', '', '', '[]', 0, datetime('now'), datetime('now'), 'chat_001')"
        )
    )
    test_session.commit()

    response = client.get("/api/webui/expression/review/stats")
    assert response.status_code == 200

    data = response.json()
    # Verify all review counts are 0 (hardcoded in refactored code)
    assert data["total"] == 1  # Total expressions exists
    assert data["unchecked"] == 0
    assert data["passed"] == 0
    assert data["rejected"] == 0
    assert data["ai_checked"] == 0
    assert data["user_checked"] == 0


def test_get_review_list_filter_unchecked(client: TestClient, mock_auth, sample_expression: Expression):
    """Test GET /expression/review/list with filter_type=unchecked returns empty (legacy behavior)"""
    # filter_type=unchecked should return no results (legacy removed)
    response = client.get("/api/webui/expression/review/list?filter_type=unchecked")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["total"] == 0  # No results (legacy fields removed)


def test_get_review_list_filter_all(client: TestClient, mock_auth, sample_expression: Expression):
    """Test GET /expression/review/list with filter_type=all returns all expressions"""
    response = client.get("/api/webui/expression/review/list?filter_type=all")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["total"] == 1
    assert len(data["data"]) == 1


def test_batch_review_expressions_unsupported(client: TestClient, mock_auth, sample_expression: Expression):
    """Test POST /expression/review/batch returns failure for require_unchecked=True"""
    review_payload = {"items": [{"id": sample_expression.id, "rejected": False, "require_unchecked": True}]}

    response = client.post("/api/webui/expression/review/batch", json=review_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["failed"] == 1  # Should fail because require_unchecked=True
    assert "不支持审核状态过滤" in data["results"][0]["message"]


def test_batch_review_expressions_no_unchecked_check(client: TestClient, mock_auth, sample_expression: Expression):
    """Test POST /expression/review/batch succeeds when require_unchecked=False"""
    review_payload = {"items": [{"id": sample_expression.id, "rejected": False, "require_unchecked": False}]}

    response = client.post("/api/webui/expression/review/batch", json=review_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["succeeded"] == 1
    assert data["results"][0]["success"] is True
