# 配置API

配置API模块提供了配置读取功能，让插件能够安全地访问全局配置和插件配置。

## 导入方式

```python
from src.plugin_system.apis import config_api
# 或者
from src.plugin_system import config_api
```

## 主要功能

### 1. 访问全局配置

```python
def get_global_config(key: str, default: Any = None) -> Any:
```

**Args**:
- `key`: 命名空间式配置键名，使用嵌套访问，如 "section.subsection.key"，大小写敏感
- `default`: 如果配置不存在时返回的默认值

**Returns**:
- `Any`: 配置值或默认值

#### 示例：
获取机器人昵称
```python
bot_name = config_api.get_global_config("bot.nickname", "MaiBot")
```

### 2. 获取插件配置

```python
def get_plugin_config(plugin_config: dict, key: str, default: Any = None) -> Any:
```
**Args**:
- `plugin_config`: 插件配置字典
- `key`: 配置键名，支持嵌套访问如 "section.subsection.key"，大小写敏感
- `default`: 如果配置不存在时返回的默认值

**Returns**:
- `Any`: 配置值或默认值

## 注意事项

1. **只读访问**：配置API只提供读取功能，插件不能修改全局配置
2. **错误处理**：所有函数都有错误处理，失败时会记录日志并返回默认值
3. **安全性**：插件通过此API访问配置是安全和隔离的
4. **性能**：频繁访问的配置建议在插件初始化时获取并缓存