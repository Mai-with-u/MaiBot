from src.config.official_configs import AMemorixConfig
from src.webui.config_schema import ConfigSchemaGenerator


def _field_names(schema):
    return {field["name"] for field in schema.get("fields", [])}


def test_flat_a_memorix_schema_keeps_visible_container_fields():
    schema = ConfigSchemaGenerator.generate_config_schema(AMemorixConfig, include_nested=False)

    names = _field_names(schema)
    assert "embedding" in names
    assert "filter" in names
    assert "retrieval" in names
