import json
from pathlib import Path

from src.config.config_base import AttributeData
from src.config.official_configs import AMemorixConfig, AMemorixFilterConfig, AMemorixRetrievalConfig
from src.webui.config_schema import AMEMORIX_EXCLUDED_FIELD_PATHS, ConfigSchemaGenerator


def test_retrieval_type_filter_defaults_are_disabled() -> None:
    config = AMemorixConfig()

    assert config.filter.retrieval.chat_stream.enabled is False
    assert config.filter.retrieval.chat_summary.enabled is False
    assert config.filter.retrieval.episode.enabled is False
    assert config.filter.retrieval.chat_stream.mode == "blacklist"
    assert config.filter.retrieval.chat_summary.chats == []


def test_retrieval_type_filter_config_schema_is_exposed() -> None:
    schema = ConfigSchemaGenerator.generate_schema(AMemorixFilterConfig)

    assert "retrieval" in schema["nested"]
    retrieval_parent_field = next(field for field in schema["fields"] if field["name"] == "retrieval")
    retrieval_schema = schema["nested"]["retrieval"]
    assert set(retrieval_schema["nested"]) >= {"chat_stream", "chat_summary", "episode"}
    chat_stream_field = next(field for field in retrieval_schema["fields"] if field["name"] == "chat_stream")
    chat_summary_schema = retrieval_schema["nested"]["chat_summary"]
    chat_summary_field = next(field for field in retrieval_schema["fields"] if field["name"] == "chat_summary")
    enabled_field = next(field for field in chat_summary_schema["fields"] if field["name"] == "enabled")

    assert retrieval_parent_field["x-collapsed-by-default"] is True
    assert chat_stream_field["x-collapsed-by-default"] is True
    assert chat_summary_field["x-collapsed-by-default"] is True
    assert enabled_field["type"] == "boolean"
    assert enabled_field["default"] is False


def test_relation_vectorization_config_is_loaded_and_exposed() -> None:
    attribute_data = AttributeData()
    payload = {
        "relation_vectorization": {
            "enabled": True,
            "backfill_enabled": True,
            "write_on_import": False,
        }
    }

    config = AMemorixRetrievalConfig.from_dict(attribute_data, payload)
    dumped = config.model_dump(mode="json")
    schema = ConfigSchemaGenerator.generate_schema(AMemorixRetrievalConfig)
    relation_field = next(field for field in schema["fields"] if field["name"] == "relation_vectorization")
    relation_schema = schema["nested"]["relation_vectorization"]

    assert "relation_vectorization" not in attribute_data.redundant_attributes
    relation_config = dumped["relation_vectorization"]
    assert relation_config["enabled"] is True
    assert relation_config["backfill_enabled"] is True
    assert relation_config["write_on_import"] is False
    assert relation_field["type"] == "object"
    assert relation_schema["className"] == "AMemorixRelationVectorizationConfig"
    assert {field["name"] for field in relation_schema["fields"]} == {"write_on_import"}


def test_vector_pools_config_is_loaded_and_exposed() -> None:
    attribute_data = AttributeData()
    payload = {
        "vector_pools": {
            "mode": "dual",
            "graph_top_k": 32,
            "graph_weight": 0.25,
            "relation_intent": {
                "graph_top_k": 64,
                "graph_weight": 0.45,
            },
        }
    }

    config = AMemorixRetrievalConfig.from_dict(attribute_data, payload)
    dumped = config.model_dump(mode="json")
    schema = ConfigSchemaGenerator.generate_schema(AMemorixRetrievalConfig)
    vector_pools_field = next(field for field in schema["fields"] if field["name"] == "vector_pools")
    vector_pools_schema = schema["nested"]["vector_pools"]

    assert "vector_pools" not in attribute_data.redundant_attributes
    vector_pools = dumped["vector_pools"]
    assert vector_pools["mode"] == "dual"
    assert vector_pools["graph_top_k"] == 32
    assert vector_pools["graph_weight"] == 0.25
    assert vector_pools["relation_intent"]["graph_top_k"] == 64
    assert vector_pools["relation_intent"]["graph_weight"] == 0.45
    assert vector_pools_field["type"] == "object"
    assert vector_pools_schema["className"] == "AMemorixVectorPoolsConfig"
    assert "mode" not in {field["name"] for field in vector_pools_schema["fields"]}
    assert "graph_weight" not in {field["name"] for field in vector_pools_schema["fields"]}
    assert "relation_intent" in vector_pools_schema["nested"]
    assert {field["name"] for field in vector_pools_schema["nested"]["relation_intent"]["fields"]} == {"graph_top_k"}


def test_vector_pools_default_mode_is_dual_and_schema_matches() -> None:
    config = AMemorixRetrievalConfig()
    schema = ConfigSchemaGenerator.generate_schema(AMemorixRetrievalConfig)
    vector_pools_schema = schema["nested"]["vector_pools"]
    schema_path = Path("src/A_memorix/config_schema.json")
    persisted_schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert config.vector_pools.mode == "dual"
    assert "mode" not in {field["name"] for field in vector_pools_schema["fields"]}
    assert "mode" not in persisted_schema["sections"]["retrieval.vector_pools"]["fields"]


def test_explicit_single_vector_pool_mode_is_preserved() -> None:
    attribute_data = AttributeData()
    config = AMemorixRetrievalConfig.from_dict(attribute_data, {"vector_pools": {"mode": "single"}})

    assert config.vector_pools.mode == "single"


def test_persisted_plugin_schema_excludes_restricted_fields_and_keeps_advanced_fields() -> None:
    schema_path = Path("src/A_memorix/config_schema.json")
    persisted_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    registered_paths = {
        f"a_memorix.{section_name}.{field_name}"
        for section_name, section in persisted_schema["sections"].items()
        for field_name in section.get("fields", {})
    }
    excluded_paths = {path.replace(".import_config.", ".import.") for path in AMEMORIX_EXCLUDED_FIELD_PATHS}

    assert "storage" not in persisted_schema["sections"]
    assert "retrieval.fusion" not in persisted_schema["sections"]
    assert not (registered_paths & excluded_paths)
    assert "a_memorix.retrieval.top_k_final" in registered_paths
    assert "a_memorix.retrieval.search.smart_fallback.enabled" in registered_paths
    assert "a_memorix.retrieval.sparse.enabled" in registered_paths
    assert "a_memorix.web.import.max_files_per_task" in registered_paths
