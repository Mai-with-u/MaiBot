from src.config.official_configs import AMemorixIntegrationConfig
from src.webui.config_schema import ConfigSchemaGenerator


def test_person_profile_integration_config_fields_are_exposed() -> None:
    schema = ConfigSchemaGenerator.generate_schema(AMemorixIntegrationConfig)

    profile_tool_field = next(
        field for field in schema["fields"] if field["name"] == "enable_person_profile_query_tool"
    )
    profile_injection_field = next(
        field for field in schema["fields"] if field["name"] == "enable_person_profile_injection"
    )
    profile_limit_field = next(
        field for field in schema["fields"] if field["name"] == "person_profile_injection_max_profiles"
    )

    assert profile_tool_field["type"] == "boolean"
    assert profile_tool_field["default"] is True
    assert profile_tool_field.get("x-widget") == "switch"
    assert profile_tool_field.get("x-icon") == "user-round-search"

    assert profile_injection_field["type"] == "boolean"
    assert profile_injection_field["default"] is True
    assert profile_injection_field.get("x-widget") == "switch"
    assert profile_injection_field.get("x-icon") == "user-round-check"

    assert profile_limit_field["type"] == "integer"
    assert profile_limit_field["default"] == 3
    assert profile_limit_field.get("x-widget") == "input"
    assert profile_limit_field.get("x-icon") == "users"
    assert profile_limit_field.get("minValue") == 1
    assert profile_limit_field.get("maxValue") == 5
