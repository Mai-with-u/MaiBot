# MCP 桥接插件 - 开发文档

本文档面向 AI 助手或开发者进行插件开发/维护。

## 前置知识

本插件基于 MaiBot 插件系统开发，需要了解：
- MaiBot 插件框架：`BasePlugin`, `BaseTool`, `BaseCommand`, `BaseEventHandler`
- 配置系统：`ConfigField`, `config_schema`
- 组件注册：`component_registry.register_component()`

详见项目根目录 `.kiro/steering/plugin-dev.md`。

---

## 版本历史

| 版本 | 主要功能 |
|------|----------|
| v1.5.4 | 易用性优化：新增 MCP 服务器获取快捷入口 |
| v1.5.3 | 配置优化：新增智能心跳 WebUI 配置项 |
| v1.5.2 | 性能优化：智能心跳间隔，根据服务器稳定性动态调整 |
| v1.5.1 | 易用性优化：新增「快速添加服务器」表单式配置 |
| v1.5.0 | 性能优化：服务器并行连接，大幅减少启动时间 |
| v1.4.4 | 修复首次生成默认配置文件时多行字符串导致 TOML 解析失败 |
| v1.4.3 | 修复 WebUI 保存配置后多行字符串格式错误导致配置文件无法读取 |
| v1.4.2 | HTTP 鉴权头支持（headers 字段） |
| v1.4.0 | 工具禁用、调用追踪、缓存、权限控制、WebUI 易用性改进 |
| v1.3.0 | 结果后处理（LLM 摘要提炼） |
| v1.2.0 | Resources/Prompts 支持（实验性） |
| v1.1.x | 心跳检测、自动重连、调用统计、`/mcp` 命令 |
| v1.0.0 | 基础 MCP 桥接 |

---

## 项目结构

```
MCPBridgePlugin/
├── plugin.py             # 主插件逻辑（1800+ 行）
├── mcp_client.py         # MCP 客户端封装（800+ 行）
├── _manifest.json        # 插件清单
├── config.example.toml   # 配置示例
├── requirements.txt      # 依赖：mcp>=1.0.0
├── README.md             # 用户文档
└── DEVELOPMENT.md        # 开发文档（本文件）
```

---

## 核心模块详解

### 1. mcp_client.py - MCP 客户端

负责与 MCP 服务器通信，可独立于 MaiBot 运行测试。

#### 数据类

```python
class TransportType(Enum):
    STDIO = "stdio"              # 本地进程
    SSE = "sse"                  # Server-Sent Events
    HTTP = "http"                # HTTP
    STREAMABLE_HTTP = "streamable_http"  # HTTP Streamable（推荐）

@dataclass
class MCPServerConfig:
    name: str                    # 服务器唯一标识
    enabled: bool = True
    transport: TransportType = TransportType.STDIO
    command: str = ""            # stdio: 启动命令
    args: List[str] = field(default_factory=list)  # stdio: 参数
    env: Dict[str, str] = field(default_factory=dict)  # stdio: 环境变量
    url: str = ""                # http/sse: 服务器 URL

@dataclass
class MCPToolInfo:
    name: str                    # 工具原始名称
    description: str
    input_schema: Dict[str, Any] # JSON Schema
    server_name: str

@dataclass
class MCPCallResult:
    success: bool
    content: str = ""
    error: Optional[str] = None
    duration_ms: float = 0.0

@dataclass
class MCPResourceInfo:
    uri: str
    name: str
    description: str = ""
    mime_type: Optional[str] = None
    server_name: str = ""

@dataclass
class MCPPromptInfo:
    name: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)
    server_name: str = ""
```

#### MCPClientSession

管理单个 MCP 服务器连接。

```python
class MCPClientSession:
    def __init__(self, config: MCPServerConfig): ...
    
    async def connect(self) -> bool:
        """连接服务器，返回是否成功"""
    
    async def disconnect(self) -> None:
        """断开连接"""
    
    async def call_tool(self, tool_name: str, arguments: Dict) -> MCPCallResult:
        """调用工具"""
    
    async def check_health(self) -> bool:
        """健康检查（用于心跳）"""
    
    async def fetch_resources(self) -> bool:
        """获取资源列表"""
    
    async def read_resource(self, uri: str) -> MCPCallResult:
        """读取资源"""
    
    async def fetch_prompts(self) -> bool:
        """获取提示模板列表"""
    
    async def get_prompt(self, name: str, arguments: Optional[Dict]) -> MCPCallResult:
        """获取提示模板"""
    
    @property
    def tools(self) -> List[MCPToolInfo]: ...
    @property
    def resources(self) -> List[MCPResourceInfo]: ...
    @property
    def prompts(self) -> List[MCPPromptInfo]: ...
    @property
    def is_connected(self) -> bool: ...
```

#### MCPClientManager

全局单例，管理多服务器。

```python
class MCPClientManager:
    def configure(self, settings: Dict) -> None:
        """配置超时、重试等参数"""
    
    async def add_server(self, config: MCPServerConfig) -> bool:
        """添加并连接服务器"""
    
    async def remove_server(self, server_name: str) -> bool:
        """移除服务器"""
    
    async def reconnect_server(self, server_name: str) -> bool:
        """重连服务器"""
    
    async def call_tool(self, tool_key: str, arguments: Dict) -> MCPCallResult:
        """调用工具，tool_key 格式: mcp_{server}_{tool}"""
    
    async def start_heartbeat(self) -> None:
        """启动心跳检测"""
    
    async def shutdown(self) -> None:
        """关闭所有连接"""
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
    
    def get_all_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
    
    def set_status_change_callback(self, callback: Callable) -> None:
        """设置状态变化回调"""
    
    @property
    def all_tools(self) -> Dict[str, Tuple[MCPToolInfo, MCPClientSession]]: ...
    @property
    def all_resources(self) -> Dict[str, Tuple[MCPResourceInfo, MCPClientSession]]: ...
    @property
    def all_prompts(self) -> Dict[str, Tuple[MCPPromptInfo, MCPClientSession]]: ...
    @property
    def disconnected_servers(self) -> List[str]: ...

# 全局单例
mcp_manager = MCPClientManager()
```

---

### 2. plugin.py - MaiBot 插件

#### v1.4.0 新增模块

```python
# ============ 调用追踪 ============
@dataclass
class ToolCallRecord:
    call_id: str              # UUID
    timestamp: float
    tool_name: str
    server_name: str
    chat_id: str = ""
    user_id: str = ""
    user_query: str = ""
    arguments: Dict = field(default_factory=dict)
    raw_result: str = ""
    processed_result: str = ""
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""
    post_processed: bool = False
    cache_hit: bool = False

class ToolCallTracer:
    def configure(self, enabled: bool, max_records: int, log_enabled: bool, log_path: Path): ...
    def record(self, record: ToolCallRecord) -> None: ...
    def get_recent(self, n: int = 10) -> List[ToolCallRecord]: ...
    def get_by_tool(self, tool_name: str) -> List[ToolCallRecord]: ...
    def clear(self) -> None: ...

tool_call_tracer = ToolCallTracer()

# ============ 调用缓存 ============
@dataclass
class CacheEntry:
    tool_name: str
    args_hash: str            # MD5(tool_name + sorted_json_args)
    result: str
    created_at: float
    expires_at: float
    hit_count: int = 0

class ToolCallCache:
    def configure(self, enabled: bool, ttl: int, max_entries: int, exclude_tools: str): ...
    def get(self, tool_name: str, args: Dict) -> Optional[str]: ...
    def set(self, tool_name: str, args: Dict, result: str) -> None: ...
    def clear(self) -> None: ...
    def get_stats(self) -> Dict[str, Any]: ...

tool_call_cache = ToolCallCache()

# ============ 权限控制 ============
class PermissionChecker:
    def configure(self, enabled: bool, default_mode: str, rules_json: str,
                  quick_deny_groups: str = "", quick_allow_users: str = ""): ...
    def check(self, tool_name: str, chat_id: str, user_id: str, is_group: bool) -> bool: ...
    def get_rules_for_tool(self, tool_name: str) -> List[Dict]: ...

permission_checker = PermissionChecker()
```

#### 工具代理

```python
class MCPToolProxy(BaseTool):
    """所有 MCP 工具的基类"""
    
    # 类属性（动态子类覆盖）
    name: str = ""
    description: str = ""
    parameters: List[Tuple] = []
    available_for_llm: bool = True
    
    # MCP 属性
    _mcp_tool_key: str = ""
    _mcp_original_name: str = ""
    _mcp_server_name: str = ""
    
    async def execute(self, function_args: Dict) -> Dict[str, Any]:
        """执行流程：
        1. 权限检查 → 拒绝则返回错误
        2. 缓存检查 → 命中则返回缓存
        3. 调用 MCP 服务器
        4. 存入缓存
        5. 后处理（可选）
        6. 记录追踪
        7. 返回结果
        """

def create_mcp_tool_class(tool_key: str, tool_info: MCPToolInfo, 
                          tool_prefix: str, disabled: bool = False) -> Type[MCPToolProxy]:
    """动态创建工具类"""
```

#### 内置工具

```python
class MCPStatusTool(BaseTool):
    """mcp_status - 查询状态/工具/资源/模板/统计/追踪/缓存"""
    name = "mcp_status"
    parameters = [
        ("query_type", STRING, "查询类型", False, 
         ["status", "tools", "resources", "prompts", "stats", "trace", "cache", "all"]),
        ("server_name", STRING, "服务器名称", False, None),
    ]

class MCPReadResourceTool(BaseTool):
    """mcp_read_resource - 读取资源"""
    name = "mcp_read_resource"

class MCPGetPromptTool(BaseTool):
    """mcp_get_prompt - 获取提示模板"""
    name = "mcp_get_prompt"
```

#### 命令

```python
class MCPStatusCommand(BaseCommand):
    """处理 /mcp 命令"""
    command_pattern = r"^[/／]mcp(?:\s+(?P<subcommand>status|tools|stats|reconnect|trace|cache|perm))?(?:\s+(?P<arg>\S+))?$"
    
    # 子命令处理
    async def _handle_reconnect(self, server_name): ...
    async def _handle_trace(self, arg): ...
    async def _handle_cache(self, arg): ...
    async def _handle_perm(self, arg): ...
```

#### 事件处理器

```python
class MCPStartupHandler(BaseEventHandler):
    """ON_START - 连接服务器、注册工具"""
    event_type = EventType.ON_START

class MCPStopHandler(BaseEventHandler):
    """ON_STOP - 关闭连接"""
    event_type = EventType.ON_STOP
```

#### 主插件类

```python
@register_plugin
class MCPBridgePlugin(BasePlugin):
    plugin_name = "mcp_bridge_plugin"
    python_dependencies = ["mcp"]
    
    config_section_descriptions = {
        "guide": "📖 快速入门",
        "servers": "🔌 服务器配置",
        "status": "📊 运行状态",
        "plugin": "插件开关",
        "settings": "⚙️ 高级设置",
        "tools": "🔧 工具管理",
        "permissions": "🔐 权限控制",
    }
    
    config_schema = {
        "guide": { "quick_start": ConfigField(...) },
        "plugin": { "enabled": ConfigField(...) },
        "settings": {
            # 基础：tool_prefix, connect_timeout, call_timeout, auto_connect, retry_*
            # 心跳：heartbeat_enabled, heartbeat_interval, auto_reconnect, max_reconnect_attempts
            # 高级：enable_resources, enable_prompts
            # 后处理：post_process_*
            # 追踪：trace_*
            # 缓存：cache_*
        },
        "tools": { "tool_list", "disabled_tools" },
        "permissions": { "perm_enabled", "perm_default_mode", "quick_deny_groups", "quick_allow_users", "perm_rules" },
        "servers": { "list" },
        "status": { "connection_status" },
    }
    
    def __init__(self):
        # 配置 mcp_manager, tool_call_tracer, tool_call_cache, permission_checker
    
    async def _async_connect_servers(self):
        # 解析配置 → 连接服务器 → 注册工具（检查禁用列表）
    
    def _update_status_display(self):
        # 更新 WebUI 状态显示
    
    def _update_tool_list_display(self):
        # 更新工具清单显示
```

---

## 数据流

```
MaiBot 启动
    │
    ▼
MCPBridgePlugin.__init__()
    ├─ mcp_manager.configure(settings)
    ├─ tool_call_tracer.configure(...)
    ├─ tool_call_cache.configure(...)
    └─ permission_checker.configure(...)
    │
    ▼
ON_START 事件 → MCPStartupHandler.execute()
    │
    ▼
_async_connect_servers()
    ├─ 解析 servers.list JSON
    ├─ 遍历服务器配置
    │   ├─ mcp_manager.add_server(config)
    │   ├─ 获取工具列表
    │   ├─ 检查 disabled_tools
    │   └─ component_registry.register_component(tool_info, tool_class)
    ├─ _update_status_display()
    └─ _update_tool_list_display()
    │
    ▼
mcp_manager.start_heartbeat()
    │
    ▼
LLM 调用工具 → MCPToolProxy.execute(function_args)
    ├─ 1. permission_checker.check() → 拒绝则返回错误
    ├─ 2. tool_call_cache.get() → 命中则跳到步骤 5
    ├─ 3. mcp_manager.call_tool()
    ├─ 4. tool_call_cache.set()
    ├─ 5. _post_process_result() (如果启用且超过阈值)
    ├─ 6. tool_call_tracer.record()
    └─ 7. 返回 {"name": ..., "content": ...}
    │
    ▼
ON_STOP 事件 → MCPStopHandler.execute()
    │
    ▼
mcp_manager.shutdown()
mcp_tool_registry.clear()
```

---

## 配置项速查

### settings（高级设置）

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| tool_prefix | str | "mcp" | 工具名前缀 |
| connect_timeout | float | 30.0 | 连接超时（秒） |
| call_timeout | float | 60.0 | 调用超时（秒） |
| auto_connect | bool | true | 自动连接 |
| retry_attempts | int | 3 | 重试次数 |
| retry_interval | float | 5.0 | 重试间隔 |
| heartbeat_enabled | bool | true | 心跳检测 |
| heartbeat_interval | float | 60.0 | 心跳间隔 |
| auto_reconnect | bool | true | 自动重连 |
| max_reconnect_attempts | int | 3 | 最大重连次数 |
| enable_resources | bool | false | Resources 支持 |
| enable_prompts | bool | false | Prompts 支持 |
| post_process_enabled | bool | false | 结果后处理 |
| post_process_threshold | int | 500 | 后处理阈值 |
| trace_enabled | bool | true | 调用追踪 |
| trace_max_records | int | 100 | 追踪记录上限 |
| cache_enabled | bool | false | 调用缓存 |
| cache_ttl | int | 300 | 缓存 TTL |
| cache_max_entries | int | 200 | 最大缓存条目 |

### permissions（权限控制）

| 配置项 | 说明 |
|--------|------|
| perm_enabled | 启用权限控制 |
| perm_default_mode | allow_all / deny_all |
| quick_deny_groups | 禁用群列表（每行一个群号） |
| quick_allow_users | 管理员白名单（每行一个 QQ 号） |
| perm_rules | 高级规则 JSON |

---

## 扩展开发示例

### 添加新命令子命令

```python
# 1. 修改 command_pattern
command_pattern = r"^[/／]mcp(?:\s+(?P<subcommand>status|...|newcmd))?..."

# 2. 在 execute() 添加分支
if subcommand == "newcmd":
    return await self._handle_newcmd(arg)

# 3. 实现处理方法
async def _handle_newcmd(self, arg: str = None):
    # 处理逻辑
    await self.send_text("结果")
    return (True, None, True)
```

### 添加新配置项

```python
# 1. config_schema 添加
"settings": {
    "new_option": ConfigField(
        type=bool,
        default=False,
        description="新选项说明",
        label="🆕 新选项",
        order=50,
    ),
}

# 2. 在 __init__ 或相应方法中读取
new_option = settings.get("new_option", False)
```

### 添加新的全局模块

```python
# 1. 定义数据类和管理类
@dataclass
class NewRecord:
    ...

class NewManager:
    def configure(self, ...): ...
    def do_something(self, ...): ...

new_manager = NewManager()

# 2. 在 MCPBridgePlugin.__init__ 中配置
new_manager.configure(...)

# 3. 在 MCPToolProxy.execute() 中使用
result = new_manager.do_something(...)
```

---

## 调试

```python
# 导入
from plugins.MCPBridgePlugin.mcp_client import mcp_manager
from plugins.MCPBridgePlugin.plugin import tool_call_tracer, tool_call_cache, permission_checker

# 检查状态
mcp_manager.get_status()
mcp_manager.get_all_stats()

# 追踪记录
tool_call_tracer.get_recent(10)

# 缓存状态
tool_call_cache.get_stats()

# 手动调用
result = await mcp_manager.call_tool("mcp_server_tool", {"arg": "value"})
```

---

## 依赖

- MaiBot >= 0.11.6
- Python >= 3.10
- mcp >= 1.0.0

## 许可证

AGPL-3.0
