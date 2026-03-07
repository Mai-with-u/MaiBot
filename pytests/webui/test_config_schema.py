from src.config.official_configs import ChatConfig
from src.config.config import Config
from src.webui.config_schema import ConfigSchemaGenerator


def test_field_docs_in_schema():
    """Test that field descriptions are correctly extracted from field_docs (docstrings)."""
    schema = ConfigSchemaGenerator.generate_schema(ChatConfig)
    talk_value = next(f for f in schema["fields"] if f["name"] == "talk_value")

    # Verify description field exists
    assert "description" in talk_value
    # Verify description contains expected Chinese text from the docstring
    assert "聊天频率" in talk_value["description"]


def test_json_schema_extra_merged():
    """Test that json_schema_extra fields are correctly merged into output."""
    schema = ConfigSchemaGenerator.generate_schema(ChatConfig)
    talk_value = next(f for f in schema["fields"] if f["name"] == "talk_value")

    # Verify UI metadata fields from json_schema_extra exist
    assert talk_value.get("x-widget") == "slider"
    assert talk_value.get("x-icon") == "message-circle"
    assert talk_value.get("step") == 0.1


def test_pydantic_constraints_mapped():
    """Test that Pydantic constraints (ge/le) are correctly mapped to minValue/maxValue."""
    schema = ConfigSchemaGenerator.generate_schema(ChatConfig)
    talk_value = next(f for f in schema["fields"] if f["name"] == "talk_value")

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
    talk_value = next(f for f in chat_schema["fields"] if f["name"] == "talk_value")
    assert "description" in talk_value
    assert talk_value.get("x-widget") == "slider"
    assert talk_value.get("minValue") == 0


def test_field_without_extra_metadata():
    """Test that fields without json_schema_extra still generate valid schema."""
    schema = ConfigSchemaGenerator.generate_schema(ChatConfig)
    max_context_size = next(f for f in schema["fields"] if f["name"] == "max_context_size")

    # Verify basic fields are generated
    assert "name" in max_context_size
    assert max_context_size["name"] == "max_context_size"
    assert "type" in max_context_size
    assert max_context_size["type"] == "integer"
    assert "label" in max_context_size
    assert "required" in max_context_size

    # Verify no x-widget or x-icon from json_schema_extra (since field has none)
    # These fields should only be present if explicitly defined in json_schema_extra
    assert not max_context_size.get("x-widget")
    assert not max_context_size.get("x-icon")
