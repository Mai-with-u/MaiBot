# 插件配置目标选择

宿主支持 `x-widget: target`，用于搜索并多选已知群或用户，无须用户填写 ID。
字段类型为 `List[目标模型]`，目标模型继承 `PluginConfigBase`，包含：

- `platform`：平台，默认值用于限制搜索；空字符串表示不限平台。
- `type`：`Literal["group"]` 或 `Literal["user"]`，必须有对应默认值。
- `id`：目标在平台上的真实 ID。
- `name`：显示名称，可选，不参与身份或权限判断。

```python
class GroupTarget(PluginConfigBase):
    platform: Literal["qq"] = "qq"
    type: Literal["group"] = "group"
    id: str = ""
    name: str = ""

allowed_groups: List[GroupTarget] = Field(
    default_factory=list,
    json_schema_extra={"x-widget": "target", "label": "允许群"},
)
```

SDK 已支持透传 `x-widget` 和列表元素定义，不需扩展 SDK 协议。插件应校验列表中的 ID 非空，并按 platform、type、id 匹配；名称仅供展示。
该目标表示平台群或账号，不表示某个机器人的投递路由，不能用它替代包含 account_id、scope 的会话身份。

群从已有 ChatSession 去重查询；用户直接查询 PersonInfo，包含没有私聊的用户。搜索在服务端执行，每次最多显示 50 项，继续输入名称可以缩小范围。尚未被宿主记录的目标不会出现。

QQ 官方适配器的禁言、撤回、踢人允许群以及禁言、踢人保护用户已接入。旧字符串列表加载时转换为 QQ 目标；保存配置后写入结构化列表。映射关闭时仍只用 OpenID 判断用户保护，开启时沿用关联身份匹配规则。
