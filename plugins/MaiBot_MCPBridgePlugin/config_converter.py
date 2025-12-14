"""
MCP 配置格式转换模块 v1.0.0

支持的格式:
- Claude Desktop (claude_desktop_config.json)
- Kiro MCP (mcp.json)
- MaiBot MCP Bridge Plugin (本插件格式)

转换规则:
- stdio: command + args + env
- sse/http/streamable_http: url + headers
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ConversionResult:
    """转换结果"""

    success: bool
    servers: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


class ConfigConverter:
    """MCP 配置格式转换器"""

    # transport 类型映射 (外部格式 -> 内部格式)
    TRANSPORT_MAP_IN = {
        "sse": "sse",
        "http": "http",
        "streamable-http": "streamable_http",
        "streamable_http": "streamable_http",
        "streamable-http": "streamable_http",
        "stdio": "stdio",
    }

    # 支持的 transport 字段名（有些格式用 type 而不是 transport）
    TRANSPORT_FIELD_NAMES = ["transport", "type"]

    # transport 类型映射 (内部格式 -> Claude 格式)
    TRANSPORT_MAP_OUT = {
        "sse": "sse",
        "http": "http",
        "streamable_http": "streamable-http",
        "stdio": "stdio",
    }

    @classmethod
    def detect_format(cls, config: Dict[str, Any]) -> Optional[str]:
        """检测配置格式类型

        Returns:
            "claude": Claude Desktop 格式 (mcpServers 对象)
            "kiro": Kiro MCP 格式 (mcpServers 对象，与 Claude 相同)
            "maibot": MaiBot 插件格式 (数组)
            None: 无法识别
        """
        if isinstance(config, list):
            # 数组格式，检查是否是 MaiBot 格式
            if len(config) == 0:
                return "maibot"
            if isinstance(config[0], dict) and "name" in config[0]:
                return "maibot"
            return None

        if isinstance(config, dict):
            # 对象格式
            if "mcpServers" in config:
                return "claude"  # Claude 和 Kiro 格式相同
            # 可能是单个服务器配置
            if "name" in config:
                return "maibot_single"
            return None

        return None

    @classmethod
    def parse_json_safe(cls, json_str: str) -> Tuple[Optional[Any], Optional[str]]:
        """安全解析 JSON 字符串

        Returns:
            (解析结果, 错误信息)
        """
        if not json_str or not json_str.strip():
            return None, "输入为空"

        json_str = json_str.strip()

        try:
            return json.loads(json_str), None
        except json.JSONDecodeError as e:
            # 尝试提供更友好的错误信息
            line = e.lineno
            col = e.colno
            return None, f"JSON 解析失败 (行 {line}, 列 {col}): {e.msg}"

    @classmethod
    def validate_server_config(cls, name: str, config: Dict[str, Any]) -> Tuple[bool, Optional[str], List[str]]:
        """验证单个服务器配置

        Args:
            name: 服务器名称
            config: 服务器配置字典

        Returns:
            (是否有效, 错误信息, 警告列表)
        """
        warnings = []

        if not isinstance(config, dict):
            return False, f"服务器 '{name}' 配置必须是对象", []

        has_command = "command" in config
        has_url = "url" in config

        # 必须有 command 或 url 之一
        if not has_command and not has_url:
            return False, f"服务器 '{name}' 缺少 'command' 或 'url' 字段", []

        # 同时有 command 和 url 时给出警告
        if has_command and has_url:
            warnings.append(f"'{name}': 同时存在 command 和 url，将优先使用 stdio 模式")

        # 验证 url 格式
        if has_url and not has_command:
            url = config.get("url", "")
            if not isinstance(url, str):
                return False, f"服务器 '{name}' 的 url 必须是字符串", []
            if not url.startswith(("http://", "https://")):
                warnings.append(f"'{name}': url 不是标准 HTTP(S) 地址")

        # 验证 command 格式
        if has_command:
            command = config.get("command", "")
            if not isinstance(command, str):
                return False, f"服务器 '{name}' 的 command 必须是字符串", []
            if not command.strip():
                return False, f"服务器 '{name}' 的 command 不能为空", []

        # 验证 args 格式
        if "args" in config:
            args = config.get("args")
            if not isinstance(args, list):
                return False, f"服务器 '{name}' 的 args 必须是数组", []
            for i, arg in enumerate(args):
                if not isinstance(arg, str):
                    warnings.append(f"'{name}': args[{i}] 不是字符串，将自动转换")

        # 验证 env 格式
        if "env" in config:
            env = config.get("env")
            if not isinstance(env, dict):
                return False, f"服务器 '{name}' 的 env 必须是对象", []

        # 验证 headers 格式
        if "headers" in config:
            headers = config.get("headers")
            if not isinstance(headers, dict):
                return False, f"服务器 '{name}' 的 headers 必须是对象", []

        # 验证 transport/type 格式
        transport_value = None
        for field_name in cls.TRANSPORT_FIELD_NAMES:
            if field_name in config:
                transport_value = config.get(field_name, "").lower()
                break
        if transport_value and transport_value not in cls.TRANSPORT_MAP_IN:
            warnings.append(f"'{name}': 未知的 transport 类型 '{transport_value}'，将自动推断")

        return True, None, warnings

    @classmethod
    def convert_claude_server(cls, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """将单个 Claude 格式服务器配置转换为 MaiBot 格式

        Args:
            name: 服务器名称
            config: Claude 格式的服务器配置

        Returns:
            MaiBot 格式的服务器配置
        """
        result = {
            "name": name,
            "enabled": True,
        }

        has_command = "command" in config

        if has_command:
            # stdio 模式
            result["transport"] = "stdio"
            result["command"] = config.get("command", "")

            # 处理 args
            args = config.get("args", [])
            if args:
                # 确保所有 args 都是字符串
                result["args"] = [str(arg) for arg in args]

            # 处理 env
            env = config.get("env", {})
            if env and isinstance(env, dict):
                result["env"] = env

        else:
            # 远程模式 (sse/http/streamable_http)
            # 支持 transport 或 type 字段
            transport_raw = None
            for field_name in cls.TRANSPORT_FIELD_NAMES:
                if field_name in config:
                    transport_raw = config.get(field_name, "").lower()
                    break
            if not transport_raw:
                transport_raw = "sse"
            result["transport"] = cls.TRANSPORT_MAP_IN.get(transport_raw, "sse")
            result["url"] = config.get("url", "")

            # 处理 headers
            headers = config.get("headers", {})
            if headers and isinstance(headers, dict):
                result["headers"] = headers

        return result

    @classmethod
    def convert_maibot_server(cls, config: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """将单个 MaiBot 格式服务器配置转换为 Claude 格式

        Args:
            config: MaiBot 格式的服务器配置

        Returns:
            (服务器名称, Claude 格式的服务器配置)
        """
        name = config.get("name", "unnamed")
        result = {}

        transport = config.get("transport", "stdio").lower()

        if transport == "stdio":
            # stdio 模式
            result["command"] = config.get("command", "")

            args = config.get("args", [])
            if args:
                result["args"] = args

            env = config.get("env", {})
            if env:
                result["env"] = env

        else:
            # 远程模式
            result["url"] = config.get("url", "")

            # 转换 transport 名称
            claude_transport = cls.TRANSPORT_MAP_OUT.get(transport, "sse")
            if claude_transport != "sse":  # sse 是默认值，可以省略
                result["transport"] = claude_transport

            headers = config.get("headers", {})
            if headers:
                result["headers"] = headers

        return name, result

    @classmethod
    def from_claude_format(cls, config: Dict[str, Any], existing_names: Optional[set] = None) -> ConversionResult:
        """从 Claude Desktop 格式转换为 MaiBot 格式

        Args:
            config: Claude Desktop 配置 (包含 mcpServers 字段)
            existing_names: 已存在的服务器名称集合，用于跳过重复

        Returns:
            ConversionResult
        """
        result = ConversionResult(success=True)
        existing_names = existing_names or set()

        # 检查格式
        if not isinstance(config, dict):
            result.success = False
            result.errors.append("配置必须是 JSON 对象")
            return result

        mcp_servers = config.get("mcpServers", {})

        if not isinstance(mcp_servers, dict):
            result.success = False
            result.errors.append("mcpServers 必须是对象")
            return result

        if not mcp_servers:
            result.warnings.append("mcpServers 为空，没有服务器可导入")
            return result

        # 转换每个服务器
        for name, srv_config in mcp_servers.items():
            # 检查名称是否已存在
            if name in existing_names:
                result.skipped.append(f"'{name}' (已存在)")
                continue

            # 验证配置
            valid, error, warnings = cls.validate_server_config(name, srv_config)
            result.warnings.extend(warnings)

            if not valid:
                result.errors.append(error)
                continue

            # 转换配置
            try:
                converted = cls.convert_claude_server(name, srv_config)
                result.servers.append(converted)
            except Exception as e:
                result.errors.append(f"转换服务器 '{name}' 失败: {str(e)}")

        # 如果有错误但也有成功的，仍然标记为成功（部分成功）
        if result.errors and not result.servers:
            result.success = False

        return result

    @classmethod
    def to_claude_format(cls, servers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """将 MaiBot 格式转换为 Claude Desktop 格式

        Args:
            servers: MaiBot 格式的服务器列表

        Returns:
            Claude Desktop 格式的配置
        """
        mcp_servers = {}

        for srv in servers:
            if not isinstance(srv, dict):
                continue

            name, config = cls.convert_maibot_server(srv)
            mcp_servers[name] = config

        return {"mcpServers": mcp_servers}

    @classmethod
    def import_from_string(cls, json_str: str, existing_names: Optional[set] = None) -> ConversionResult:
        """从 JSON 字符串导入配置

        自动检测格式并转换为 MaiBot 格式

        Args:
            json_str: JSON 字符串
            existing_names: 已存在的服务器名称集合

        Returns:
            ConversionResult
        """
        result = ConversionResult(success=True)
        existing_names = existing_names or set()

        # 解析 JSON
        parsed, error = cls.parse_json_safe(json_str)
        if error:
            result.success = False
            result.errors.append(error)
            return result

        # 检测格式
        fmt = cls.detect_format(parsed)

        if fmt is None:
            result.success = False
            result.errors.append("无法识别的配置格式")
            return result

        if fmt == "maibot":
            # 已经是 MaiBot 格式，直接验证并返回
            for srv in parsed:
                if not isinstance(srv, dict):
                    result.warnings.append("跳过非对象元素")
                    continue

                name = srv.get("name", "")
                if not name:
                    result.warnings.append("跳过缺少 name 的服务器")
                    continue

                if name in existing_names:
                    result.skipped.append(f"'{name}' (已存在)")
                    continue

                result.servers.append(srv)

        elif fmt == "maibot_single":
            # 单个 MaiBot 格式服务器
            name = parsed.get("name", "")
            if name in existing_names:
                result.skipped.append(f"'{name}' (已存在)")
            else:
                result.servers.append(parsed)

        elif fmt in ("claude", "kiro"):
            # Claude/Kiro 格式
            return cls.from_claude_format(parsed, existing_names)

        return result

    @classmethod
    def export_to_string(cls, servers: List[Dict[str, Any]], format_type: str = "claude", pretty: bool = True) -> str:
        """导出配置为 JSON 字符串

        Args:
            servers: MaiBot 格式的服务器列表
            format_type: 导出格式 ("claude", "kiro", "maibot")
            pretty: 是否格式化输出

        Returns:
            JSON 字符串
        """
        indent = 2 if pretty else None

        if format_type in ("claude", "kiro"):
            config = cls.to_claude_format(servers)
        else:
            config = servers

        return json.dumps(config, ensure_ascii=False, indent=indent)
