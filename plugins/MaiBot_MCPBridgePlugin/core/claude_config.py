import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


class ClaudeConfigError(ValueError):
    pass


Transport = Literal["stdio", "sse", "http", "streamable_http"]


@dataclass(frozen=True)
class ClaudeMcpServer:
    name: str
    transport: Transport
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True


def _normalize_transport(value: Optional[str]) -> Transport:
    if not value:
        return "streamable_http"
    v = value.strip().lower().replace("-", "_")
    if v in ("streamable_http", "streamablehttp", "streamable"):
        return "streamable_http"
    if v in ("http",):
        return "http"
    if v in ("sse",):
        return "sse"
    if v in ("stdio",):
        return "stdio"
    raise ClaudeConfigError(f"unsupported transport: {value}")


def _coerce_str_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ClaudeConfigError(f"{field_name} must be a list")


def _coerce_str_dict(value: Any, field_name: str) -> Dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    raise ClaudeConfigError(f"{field_name} must be an object")


def parse_claude_mcp_config(config_json: str) -> List[ClaudeMcpServer]:
    """Parse Claude Desktop style MCP config JSON.

    Supported:
    - Full object: {"mcpServers": {...}}
    - Direct mapping: {...} treated as mcpServers
    """
    text = (config_json or "").strip()
    if not text:
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeConfigError(f"invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ClaudeConfigError("config must be a JSON object")

    servers_obj = data.get("mcpServers", data)
    if not isinstance(servers_obj, dict):
        raise ClaudeConfigError("mcpServers must be an object")

    servers: List[ClaudeMcpServer] = []
    for name, raw in servers_obj.items():
        if not isinstance(name, str) or not name.strip():
            raise ClaudeConfigError("server name must be a non-empty string")
        if not isinstance(raw, dict):
            raise ClaudeConfigError(f"server '{name}' must be an object")

        enabled = bool(raw.get("enabled", True))
        command = str(raw.get("command", "") or "")
        url = str(raw.get("url", "") or "")
        args = _coerce_str_list(raw.get("args"), "args")
        env = _coerce_str_dict(raw.get("env"), "env")
        headers = _coerce_str_dict(raw.get("headers"), "headers")

        transport_hint = raw.get("transport", raw.get("type"))

        if command:
            transport: Transport = "stdio"
        elif url:
            try:
                transport = _normalize_transport(str(transport_hint) if transport_hint is not None else None)
            except ClaudeConfigError:
                transport = "streamable_http"
        else:
            raise ClaudeConfigError(f"server '{name}' must have either 'command' or 'url'")

        servers.append(
            ClaudeMcpServer(
                name=name,
                transport=transport,
                command=command,
                args=args,
                env=env,
                url=url,
                headers=headers,
                enabled=enabled,
            )
        )

    return servers


def legacy_servers_list_to_claude_config(servers_list_json: str) -> str:
    """Convert legacy v1.x servers list (JSON array) to Claude mcpServers JSON.

    Legacy item schema:
      {"name","enabled","transport","url","headers","command","args","env"}
    """
    text = (servers_list_json or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return ""

    mcp_servers: Dict[str, Any] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        enabled = bool(item.get("enabled", True))
        transport = str(item.get("transport", "") or "").strip().lower().replace("-", "_")

        if transport == "stdio" or item.get("command"):
            entry: Dict[str, Any] = {
                "enabled": enabled,
                "command": item.get("command", "") or "",
                "args": item.get("args", []) or [],
            }
            if item.get("env"):
                entry["env"] = item.get("env")
            mcp_servers[name] = entry
            continue

        entry = {"enabled": enabled, "url": item.get("url", "") or ""}
        if item.get("headers"):
            entry["headers"] = item.get("headers")
        if transport:
            entry["transport"] = transport
        mcp_servers[name] = entry

    if not mcp_servers:
        return ""
    return json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False, indent=2)
