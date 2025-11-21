# 工具API

工具API模块提供了获取和管理工具实例的功能，让插件能够访问系统中注册的工具。

## 导入方式

```python
from src.plugin_system.apis import tool_api
# 或者
from src.plugin_system import tool_api
```

## 主要功能

### 1. 获取工具实例

```python
def get_tool_instance(tool_name: str) -> Optional[BaseTool]:
```

获取指定名称的工具实例。

**Args**:
- `tool_name`: 工具名称字符串

**Returns**:
- `Optional[BaseTool]`: 工具实例，如果工具不存在则返回 None

### 2. 获取LLM可用的工具定义

```python
def get_llm_available_tool_definitions():
```

获取所有LLM可用的工具定义列表。

**Returns**:
- `List[Tuple[str, Dict[str, Any]]]`: 工具定义列表，每个元素为 `(工具名称, 工具定义字典)` 的元组
  - 其具体定义请参照[tool-components.md](../tool-components.md#属性说明)中的工具定义格式。
#### 示例：

```python
# 获取所有LLM可用的工具定义
tools = tool_api.get_llm_available_tool_definitions()
for tool_name, tool_definition in tools:
    print(f"工具: {tool_name}")
    print(f"定义: {tool_definition}")
```

## 注意事项

1. **工具存在性检查**：使用前请检查工具实例是否为 None
2. **权限控制**：某些工具可能有使用权限限制
3. **异步调用**：大多数工具方法是异步的，需要使用 await
4. **错误处理**：调用工具时请做好异常处理
