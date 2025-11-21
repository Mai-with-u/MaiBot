# LLM API

LLM API模块提供与大语言模型交互的功能，让插件能够使用系统配置的LLM模型进行内容生成。

## 导入方式

```python
from src.plugin_system.apis import llm_api
# 或者
from src.plugin_system import llm_api
```

## 主要功能

### 1. 查询可用模型
```python
def get_available_models() -> Dict[str, TaskConfig]:
```
获取所有可用的模型配置。

**Return：**
- `Dict[str, TaskConfig]`：模型配置字典，key为模型名称，value为模型配置对象。

### 2. 使用模型生成内容
```python
async def generate_with_model(
    prompt: str,
    model_config: TaskConfig,
    request_type: str = "plugin.generate",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[bool, str, str, str]:
```
使用指定模型生成内容。

**Args:**
- `prompt`：提示词。
- `model_config`：模型配置对象（从 `get_available_models` 获取）。
- `request_type`：请求类型标识，默认为 `"plugin.generate"`。
- `temperature`：生成内容的温度设置，影响输出的随机性。
- `max_tokens`：生成内容的最大token数。

**Return：**
- `Tuple[bool, str, str, str]`：返回一个元组，包含（是否成功, 生成的内容, 推理过程, 模型名称）。

### 3. 有Tool情况下使用模型生成内容
```python
async def generate_with_model_with_tools(
    prompt: str,
    model_config: TaskConfig,
    tool_options: List[Dict[str, Any]] | None = None,
    request_type: str = "plugin.generate",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[bool, str, str, str, List[ToolCall] | None]:
```
使用指定模型生成内容，并支持工具调用。

**Args:**
- `prompt`：提示词。
- `model_config`：模型配置对象（从 `get_available_models` 获取）。
- `tool_options`：工具选项列表，包含可用工具的配置，字典为每一个工具的定义，参见[tool-components.md](../tool-components.md#属性说明)，可用`tool_api.get_llm_available_tool_definitions()`获取并选择。
- `request_type`：请求类型标识，默认为 `"plugin.generate"`。
- `temperature`：生成内容的温度设置，影响输出的随机性。
- `max_tokens`：生成内容的最大token数。