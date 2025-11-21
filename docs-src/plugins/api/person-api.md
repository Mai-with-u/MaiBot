# 个人信息API

个人信息API模块提供用户信息查询和管理功能，让插件能够获取和使用用户的相关信息。

## 导入方式

```python
from src.plugin_system.apis import person_api
# 或者
from src.plugin_system import person_api
```

## 主要功能

### 1. Person ID 获取
```python
def get_person_id(platform: str, user_id: int) -> str:
```
根据平台和用户ID获取person_id

**Args:**
- `platform`：平台名称，如 "qq", "telegram" 等
- `user_id`：用户ID

**Returns:**
- `str`：唯一的person_id（MD5哈希值）

#### 示例
```python
person_id = person_api.get_person_id("qq", 123456)
```

### 2. 用户信息查询
```python
async def get_person_value(person_id: str, field_name: str, default: Any = None) -> Any:
```
查询单个用户信息字段值

**Args:**
- `person_id`：用户的唯一标识ID
- `field_name`：要获取的字段名
- `default`：字段值不存在时的默认值

**Returns:**
- `Any`：字段值或默认值

#### 示例
```python
nickname = await person_api.get_person_value(person_id, "nickname", "未知用户")
impression = await person_api.get_person_value(person_id, "impression")
```

### 3. 批量用户信息查询
```python
async def get_person_values(person_id: str, field_names: list, default_dict: Optional[dict] = None) -> dict:
```
批量获取用户信息字段值

**Args:**
- `person_id`：用户的唯一标识ID
- `field_names`：要获取的字段名列表
- `default_dict`：默认值字典，键为字段名，值为默认值

**Returns:**
- `dict`：字段名到值的映射字典

#### 示例
```python
values = await person_api.get_person_values(
    person_id,
    ["nickname", "impression", "know_times"],
    {"nickname": "未知用户", "know_times": 0}
)
```

### 4. 判断用户是否已知
```python
async def is_person_known(platform: str, user_id: int) -> bool:
```
判断是否认识某个用户

**Args:**
- `platform`：平台名称
- `user_id`：用户ID

**Returns:**
- `bool`：是否认识该用户

### 5. 根据用户名获取Person ID
```python
def get_person_id_by_name(person_name: str) -> str:
```
根据用户名获取person_id

**Args:**
- `person_name`：用户名

**Returns:**
- `str`：person_id，如果未找到返回空字符串

## 常用字段说明

### 基础信息字段
- `nickname`：用户昵称
- `platform`：平台信息
- `user_id`：用户ID

### 关系信息字段
- `impression`：对用户的印象
- `points`: 用户特征点

其他字段可以参考`PersonInfo`类的属性（位于`src.common.database.database_model`）

## 注意事项

1. **异步操作**：部分查询函数都是异步的，需要使用`await`
2. **性能考虑**：批量查询优于单个查询
3. **隐私保护**：确保用户信息的使用符合隐私政策
4. **数据一致性**：person_id是用户的唯一标识，应妥善保存和使用