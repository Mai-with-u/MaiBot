import inspect
from typing import get_args, get_origin

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.config import Config
from src.config.config_base import AttributeData, ConfigBase, Field
from src.config.official_configs import AMemorixConfig, ChatConfig, MessageReceiveConfig
from src.webui.dependencies import require_auth
from src.webui.config_schema import (
    AMEMORIX_ADVANCED_FIELD_PATHS,
    AMEMORIX_BASIC_FIELD_PATHS,
    AMEMORIX_EXCLUDED_FIELD_PATHS,
    ConfigSchemaGenerator,
)
from src.webui.routers import get_all_routers


def _get_reply_timing_schema(config_schema):
    return config_schema["nested"]["reply_timing"]


def _get_talk_value_field(config_schema):
    reply_timing_schema = _get_reply_timing_schema(config_schema)
    return next(f for f in reply_timing_schema["fields"] if f["name"] == "talk_value")


def test_field_docs_in_schema():
    """Test that field descriptions are correctly extracted from field_docs (docstrings)."""
    schema = ConfigSchemaGenerator.generate_schema(ChatConfig)
    talk_value = _get_talk_value_field(schema)

    # Verify description field exists
    assert "description" in talk_value
    # Verify description contains expected Chinese text from the docstring
    assert "频率" in talk_value["description"]


def test_json_schema_extra_merged():
    """Test that json_schema_extra fields are correctly merged into output."""
    schema = ConfigSchemaGenerator.generate_schema(ChatConfig)
    talk_value = _get_talk_value_field(schema)

    # Verify UI metadata fields from json_schema_extra exist
    assert talk_value.get("x-widget") == "slider"
    assert talk_value.get("x-icon") == "message-circle"
    assert talk_value.get("step") == 0.001


def test_pydantic_constraints_mapped():
    """Test that Pydantic constraints (ge/le) are correctly mapped to minValue/maxValue."""
    schema = ConfigSchemaGenerator.generate_schema(ChatConfig)
    talk_value = _get_talk_value_field(schema)

    # Verify constraints are mapped to frontend naming convention
    assert "minValue" in talk_value
    assert "maxValue" in talk_value
    assert talk_value["minValue"] == 0  # From ge=0
    assert talk_value["maxValue"] == 1  # From le=1


def test_nested_model_schema():
    """Test that nested models (ConfigBase fields) are correctly handled."""
    schema = ConfigSchemaGenerator.generate_schema(Config)

    # Verify nested structure exists
    assert "nested" in schema
    assert "chat" in schema["nested"]

    # Verify nested chat schema is complete
    chat_schema = schema["nested"]["chat"]
    assert chat_schema["className"] == "ChatConfig"
    assert "fields" in chat_schema

    # Verify nested schema fields include description and metadata
    talk_value = _get_talk_value_field(chat_schema)
    assert "description" in talk_value
    assert talk_value.get("x-widget") == "slider"
    assert talk_value.get("minValue") == 0


def test_config_subtab_metadata_is_exposed():
    """配置子 Tab 元数据应由 schema 提供，而不是由 WebUI 硬编码。"""
    schema = ConfigSchemaGenerator.generate_schema(Config)

    chat_schema = schema["nested"]["chat"]
    expression_schema = schema["nested"]["expression"]
    jargon_schema = schema["nested"]["jargon"]

    assert chat_schema.get("uiUseSubTabs") is True
    assert chat_schema.get("uiRootSubLabel") == "基础设置"
    assert chat_schema["nested"]["reply_timing"].get("uiLabel") == "什么时候发言"
    assert chat_schema["nested"]["reply_style"].get("uiLabel") == "如何发言"

    assert expression_schema.get("uiUseSubTabs") is True
    assert expression_schema.get("uiSubLabel") == "表达"
    assert jargon_schema.get("uiSubLabel") == "黑话"


def test_field_without_extra_metadata():
    """Test that fields without json_schema_extra still generate valid schema."""
    class PlainExampleConfig(ConfigBase):
        plain_field: str = Field(default="visible")
        """普通字段"""

    schema = ConfigSchemaGenerator.generate_schema(PlainExampleConfig)
    plain_field = next(f for f in schema["fields"] if f["name"] == "plain_field")

    # Verify basic fields are generated
    assert "name" in plain_field
    assert plain_field["name"] == "plain_field"
    assert "type" in plain_field
    assert plain_field["type"] == "string"
    assert "label" in plain_field
    assert "required" in plain_field

    # Verify no x-widget or x-icon from json_schema_extra (since field has none)
    # These fields should only be present if explicitly defined in json_schema_extra
    assert not plain_field.get("x-widget")
    assert not plain_field.get("x-icon")


def test_all_top_level_sections_have_ui_metadata():
    """所有顶层配置节都必须声明 uiParent 或独立 Tab 的标签与图标。"""
    schema = ConfigSchemaGenerator.generate_schema(Config)

    for section_name, section_schema in schema["nested"].items():
        has_parent = bool(section_schema.get("uiParent"))
        has_host_meta = bool(section_schema.get("uiLabel"))
        assert has_parent or has_host_meta, f"{section_name} 缺少 UI 元数据"


def test_tab_advanced_visibility_comes_from_config_metadata():
    """配置页 Tab 是否默认收起应由配置类元信息决定。"""
    schema = ConfigSchemaGenerator.generate_schema(Config)

    assert schema["nested"]["bot"].get("uiAdvanced") is False
    assert schema["nested"]["experimental"].get("uiAdvanced") is True
    assert schema["nested"]["message_receive"].get("uiAdvanced") is True


def test_mcp_parent_metadata_matches_current_config_shape():
    """当前配置模型没有 maisaka 宿主节，MCP 仍保留既有父级元数据。"""
    schema = ConfigSchemaGenerator.generate_schema(Config)

    mcp_schema = schema["nested"]["mcp"]

    assert "maisaka" not in schema["nested"]
    assert mcp_schema.get("uiParent") == "maisaka"


def test_memory_query_config_fields_are_exposed():
    """query_memory 开关和默认条数应出现在记忆配置 schema 中。"""
    schema = ConfigSchemaGenerator.generate_schema(Config)
    memory_schema = schema["nested"]["a_memorix"]["nested"]["integration"]

    assert schema["nested"]["a_memorix"].get("uiLabel") == "记忆"

    enable_field = next(field for field in memory_schema["fields"] if field["name"] == "enable_memory_query_tool")
    limit_field = next(field for field in memory_schema["fields"] if field["name"] == "memory_query_default_limit")

    assert enable_field["type"] == "boolean"
    assert enable_field.get("x-widget") == "switch"
    assert enable_field.get("x-icon") == "database"

    assert limit_field["type"] == "integer"
    assert limit_field.get("x-widget") == "input"
    assert limit_field.get("x-icon") == "hash"
    assert limit_field.get("minValue") == 1
    assert limit_field.get("maxValue") == 20


def test_set_field_is_mapped_as_array():
    """set[str] 应映射为前端可识别的 array。"""
    schema = ConfigSchemaGenerator.generate_schema(MessageReceiveConfig)
    ban_words = next(field for field in schema["fields"] if field["name"] == "ban_words")

    assert ban_words["type"] == "array"
    assert ban_words["items"]["type"] == "string"


def test_advanced_fields_are_marked_in_webui_schema():
    """advanced=True 的字段应保留标记，供前端高级设置开关控制。"""

    class AdvancedExampleConfig(ConfigBase):
        normal_field: str = Field(default="visible")
        """普通字段"""

        advanced_field: str = Field(default="hidden", json_schema_extra={"advanced": True})
        """高级字段"""

    schema = ConfigSchemaGenerator.generate_schema(AdvancedExampleConfig)
    field_by_name = {field["name"]: field for field in schema["fields"]}

    assert "normal_field" in field_by_name
    assert "advanced_field" in field_by_name
    assert field_by_name["advanced_field"]["advanced"] is True


def _field_names(schema):
    return {field["name"] for field in schema["fields"]}


def _schema_field(schema, name):
    return next(field for field in schema["fields"] if field["name"] == name)


def _schema_field_at_path(schema, field_path):
    current_schema = schema
    path_parts = field_path.split(".")
    for part in path_parts[:-1]:
        current_schema = current_schema.get("nested", {}).get(part)
        if current_schema is None:
            return None
    return next((field for field in current_schema.get("fields", []) if field["name"] == path_parts[-1]), None)


def _iter_config_leaf_paths(config_class, prefix):
    internal_fields = {"field_docs", "_validate_any", "suppress_any_warning"}
    for field_name, field_info in config_class.model_fields.items():
        if field_name in internal_fields:
            continue
        field_path = f"{prefix}.{field_name}" if prefix else field_name
        annotation = field_info.annotation
        origin = get_origin(annotation)
        args = get_args(annotation)

        if inspect.isclass(annotation) and issubclass(annotation, ConfigBase):
            yield from _iter_config_leaf_paths(annotation, field_path)
            continue
        if origin in {list, set, tuple} and args:
            first_arg = args[0]
            if inspect.isclass(first_arg) and issubclass(first_arg, ConfigBase):
                yield from _iter_config_leaf_paths(first_arg, f"{field_path}[]")
                continue

        yield field_path



def test_a_memorix_visibility_policy_marks_and_filters_fields():
    """A_Memorix 配置按四类进入普通、高级或 TOML-only。"""
    schema = ConfigSchemaGenerator.generate_schema(AMemorixConfig)

    assert "storage" not in _field_names(schema)
    assert _schema_field(schema, "global_memory_sharing_enabled").get("advanced") is not True
    assert _schema_field(schema, "shared_memory_groups").get("advanced") is not True

    integration_schema = schema["nested"]["integration"]
    assert _schema_field(integration_schema, "enable_memory_query_tool").get("advanced") is not True
    assert _schema_field(integration_schema, "heuristic_memory_recall_window_size")["advanced"] is True
    assert "heuristic_memory_cross_chat_enabled" not in _field_names(integration_schema)
    assert "heuristic_memory_private_to_group_enabled" not in _field_names(integration_schema)

    retrieval_schema = schema["nested"]["retrieval"]
    assert "alpha" not in _field_names(retrieval_schema)
    assert "fusion" not in _field_names(retrieval_schema)
    assert _schema_field(retrieval_schema, "top_k_final")["advanced"] is True
    smart_fallback_schema = retrieval_schema["nested"]["search"]["nested"]["smart_fallback"]
    assert _schema_field(smart_fallback_schema, "enabled")["advanced"] is True

    web_import_schema = schema["nested"]["web"]["nested"]["import_config"]
    assert _schema_field(web_import_schema, "enabled")["advanced"] is True
    assert _schema_field(web_import_schema, "enabled")["default"] is True
    assert _schema_field(web_import_schema, "max_files_per_task")["advanced"] is True
    assert "max_queue_size" not in _field_names(web_import_schema)


def test_a_memorix_visibility_policy_classifies_all_official_fields():
    """新增 A_Memorix 官方字段时，必须明确进入 basic、advanced 或 excluded。"""
    classified_paths = AMEMORIX_BASIC_FIELD_PATHS | AMEMORIX_ADVANCED_FIELD_PATHS | AMEMORIX_EXCLUDED_FIELD_PATHS
    leaf_paths = set(_iter_config_leaf_paths(AMemorixConfig, "a_memorix"))

    assert not (AMEMORIX_BASIC_FIELD_PATHS & AMEMORIX_ADVANCED_FIELD_PATHS)
    assert not (AMEMORIX_BASIC_FIELD_PATHS & AMEMORIX_EXCLUDED_FIELD_PATHS)
    assert not (AMEMORIX_ADVANCED_FIELD_PATHS & AMEMORIX_EXCLUDED_FIELD_PATHS)
    assert leaf_paths - classified_paths == set()
    assert {
        path
        for path in classified_paths - leaf_paths
        if path not in {"a_memorix.shared_memory_groups", "a_memorix.shared_memory_groups[].targets"}
    } == set()


def test_a_memorix_excluded_fields_are_still_loaded_by_official_model():
    """TOML-only 字段不进可视化 schema，但官方配置模型仍应接收。"""
    attribute_data = AttributeData()
    config = AMemorixConfig.from_dict(
        attribute_data,
        {
            "storage": {"data_dir": "data/custom-a-memorix"},
            "embedding": {"dimension": 1536, "quantization_type": "int8"},
            "retrieval": {
                "alpha": 0.4,
                "fusion": {"method": "alpha_legacy", "rrf_k": 42},
                "vector_pools": {"mode": "single", "graph_weight": 0.2},
            },
            "advanced": {"enable_auto_save": False},
        },
    )

    assert attribute_data.redundant_attributes == []
    assert config.storage.data_dir == "data/custom-a-memorix"
    assert config.embedding.dimension == 1536
    assert config.retrieval.alpha == 0.4
    assert config.retrieval.fusion.method == "alpha_legacy"
    assert config.retrieval.vector_pools.mode == "single"
    assert config.advanced.enable_auto_save is False


def test_compat_config_schema_route_keeps_a_memorix_visibility_policy():
    """旧版 /api/config/schema 应返回同一套可视化边界。"""
    app = FastAPI()
    for api_router in get_all_routers():
        app.include_router(api_router)
    app.dependency_overrides[require_auth] = lambda: "test-token"

    response = TestClient(app).get("/api/config/schema")

    assert response.status_code == 200
    schema = response.json()["schema"]
    a_memorix_schema = schema["nested"]["a_memorix"]

    assert _schema_field_at_path(a_memorix_schema, "plugin.enabled") is not None
    assert _schema_field_at_path(a_memorix_schema, "storage.data_dir") is None
    assert _schema_field_at_path(a_memorix_schema, "retrieval.fusion.rrf_k") is None
    assert _schema_field_at_path(a_memorix_schema, "retrieval.search.smart_fallback.enabled")["advanced"] is True


