# 插件管理API

插件管理API模块提供了对插件的加载、卸载、重新加载以及目录管理功能。

## 导入方式
```python
from src.plugin_system.apis import plugin_manage_api
# 或者
from src.plugin_system import plugin_manage_api
```

## 功能概述

插件管理API主要提供以下功能：
- **插件查询** - 列出当前加载的插件或已注册的插件。
- **插件管理** - 加载、卸载、重新加载插件。
- **插件目录管理** - 添加插件目录并重新扫描。

## 主要功能

### 1. 列出当前加载的插件
```python
def list_loaded_plugins() -> List[str]:
```
列出所有当前加载的插件。

**Returns:**
- `List[str]` - 当前加载的插件名称列表。

### 2. 列出所有已注册的插件
```python
def list_registered_plugins() -> List[str]:
```
列出所有已注册的插件。

**Returns:**
- `List[str]` - 已注册的插件名称列表。

### 3. 获取插件路径
```python
def get_plugin_path(plugin_name: str) -> str:
```
获取指定插件的路径。

**Args:**
- `plugin_name` (str): 要查询的插件名称。
**Returns:**
- `str` - 插件的路径，如果插件不存在则 raise ValueError。

### 4. 卸载指定的插件
```python
async def remove_plugin(plugin_name: str) -> bool:
```
卸载指定的插件。

**Args:**
- `plugin_name` (str): 要卸载的插件名称。

**Returns:**
- `bool` - 卸载是否成功。

### 5. 重新加载指定的插件
```python
async def reload_plugin(plugin_name: str) -> bool:
```
重新加载指定的插件。

**Args:**
- `plugin_name` (str): 要重新加载的插件名称。

**Returns:**
- `bool` - 重新加载是否成功。

### 6. 加载指定的插件
```python
def load_plugin(plugin_name: str) -> Tuple[bool, int]:
```
加载指定的插件。

**Args:**
- `plugin_name` (str): 要加载的插件名称。

**Returns:**
- `Tuple[bool, int]` - 加载是否成功，成功或失败的个数。

### 7. 添加插件目录
```python
def add_plugin_directory(plugin_directory: str) -> bool:
```
添加插件目录。

**Args:**
- `plugin_directory` (str): 要添加的插件目录路径。

**Returns:**
- `bool` - 添加是否成功。

### 8. 重新扫描插件目录
```python
def rescan_plugin_directory() -> Tuple[int, int]:
```
重新扫描插件目录，加载新插件。

**Returns:**
- `Tuple[int, int]` - 成功加载的插件数量和失败的插件数量。