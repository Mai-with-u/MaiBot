# 组件管理API

组件管理API模块提供了对插件组件的查询和管理功能，使得插件能够获取和使用组件相关的信息。

## 导入方式
```python
from src.plugin_system.apis import component_manage_api
# 或者
from src.plugin_system import component_manage_api
```

## 功能概述

组件管理API主要提供以下功能：
- **插件信息查询** - 获取所有插件或指定插件的信息。
- **组件查询** - 按名称或类型查询组件信息。
- **组件管理** - 启用或禁用组件，支持全局和局部操作。

## 主要功能

### 1. 获取所有插件信息
```python
def get_all_plugin_info() -> Dict[str, PluginInfo]:
```
获取所有插件的信息。

**Returns:**
- `Dict[str, PluginInfo]` - 包含所有插件信息的字典，键为插件名称，值为 `PluginInfo` 对象。

### 2. 获取指定插件信息
```python
def get_plugin_info(plugin_name: str) -> Optional[PluginInfo]:
```
获取指定插件的信息。

**Args:**
- `plugin_name` (str): 插件名称。

**Returns:**
- `Optional[PluginInfo]`: 插件信息对象，如果插件不存在则返回 `None`。

### 3. 获取指定组件信息
```python
def get_component_info(component_name: str, component_type: ComponentType) -> Optional[Union[CommandInfo, ActionInfo, EventHandlerInfo]]:
```
获取指定组件的信息。

**Args:**
- `component_name` (str): 组件名称。
- `component_type` (ComponentType): 组件类型。

**Returns:**
- `Optional[Union[CommandInfo, ActionInfo, EventHandlerInfo]]`: 组件信息对象，如果组件不存在则返回 `None`。

### 4. 获取指定类型的所有组件信息
```python
def get_components_info_by_type(component_type: ComponentType) -> Dict[str, Union[CommandInfo, ActionInfo, EventHandlerInfo]]:
```
获取指定类型的所有组件信息。

**Args:**
- `component_type` (ComponentType): 组件类型。

**Returns:**
- `Dict[str, Union[CommandInfo, ActionInfo, EventHandlerInfo]]`: 包含指定类型组件信息的字典，键为组件名称，值为对应的组件信息对象。

### 5. 获取指定类型的所有启用的组件信息
```python
def get_enabled_components_info_by_type(component_type: ComponentType) -> Dict[str, Union[CommandInfo, ActionInfo, EventHandlerInfo]]:
```
获取指定类型的所有启用的组件信息。

**Args:**
- `component_type` (ComponentType): 组件类型。

**Returns:**
- `Dict[str, Union[CommandInfo, ActionInfo, EventHandlerInfo]]`: 包含指定类型启用组件信息的字典，键为组件名称，值为对应的组件信息对象。

### 6. 获取指定 Action 的注册信息
```python
def get_registered_action_info(action_name: str) -> Optional[ActionInfo]:
```
获取指定 Action 的注册信息。

**Args:**
- `action_name` (str): Action 名称。

**Returns:**
- `Optional[ActionInfo]` - Action 信息对象，如果 Action 不存在则返回 `None`。

### 7. 获取指定 Command 的注册信息
```python
def get_registered_command_info(command_name: str) -> Optional[CommandInfo]:
```
获取指定 Command 的注册信息。

**Args:**
- `command_name` (str): Command 名称。

**Returns:**
- `Optional[CommandInfo]` - Command 信息对象，如果 Command 不存在则返回 `None`。

### 8. 获取指定 Tool 的注册信息
```python
def get_registered_tool_info(tool_name: str) -> Optional[ToolInfo]:
```
获取指定 Tool 的注册信息。

**Args:**
- `tool_name` (str): Tool 名称。

**Returns:**
- `Optional[ToolInfo]` - Tool 信息对象，如果 Tool 不存在则返回 `None`。

### 9. 获取指定 EventHandler 的注册信息
```python
def get_registered_event_handler_info(event_handler_name: str) -> Optional[EventHandlerInfo]:
```
获取指定 EventHandler 的注册信息。

**Args:**
- `event_handler_name` (str): EventHandler 名称。

**Returns:**
- `Optional[EventHandlerInfo]` - EventHandler 信息对象，如果 EventHandler 不存在则返回 `None`。

### 10. 全局启用指定组件
```python
def globally_enable_component(component_name: str, component_type: ComponentType) -> bool:
```
全局启用指定组件。

**Args:**
- `component_name` (str): 组件名称。
- `component_type` (ComponentType): 组件类型。

**Returns:**
- `bool` - 启用成功返回 `True`，否则返回 `False`。

### 11. 全局禁用指定组件
```python
async def globally_disable_component(component_name: str, component_type: ComponentType) -> bool:
```
全局禁用指定组件。

**此函数是异步的，确保在异步环境中调用。**

**Args:**
- `component_name` (str): 组件名称。
- `component_type` (ComponentType): 组件类型。

**Returns:**
- `bool` - 禁用成功返回 `True`，否则返回 `False`。

### 12. 局部启用指定组件
```python
def locally_enable_component(component_name: str, component_type: ComponentType, stream_id: str) -> bool:
```
局部启用指定组件。

**Args:**
- `component_name` (str): 组件名称。
- `component_type` (ComponentType): 组件类型。
- `stream_id` (str): 消息流 ID。

**Returns:**
- `bool` - 启用成功返回 `True`，否则返回 `False`。

### 13. 局部禁用指定组件
```python
def locally_disable_component(component_name: str, component_type: ComponentType, stream_id: str) -> bool:
```
局部禁用指定组件。

**Args:**
- `component_name` (str): 组件名称。
- `component_type` (ComponentType): 组件类型。
- `stream_id` (str): 消息流 ID。

**Returns:**
- `bool` - 禁用成功返回 `True`，否则返回 `False`。

### 14. 获取指定消息流中禁用的组件列表
```python
def get_locally_disabled_components(stream_id: str, component_type: ComponentType) -> list[str]:
```
获取指定消息流中禁用的组件列表。

**Args:**
- `stream_id` (str): 消息流 ID。
- `component_type` (ComponentType): 组件类型。

**Returns:**
- `list[str]` - 禁用的组件名称列表。
