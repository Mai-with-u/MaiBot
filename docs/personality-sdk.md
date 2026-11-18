# 插件人设 SDK

需要支持人设能力的 Host 与 maibot-plugin-sdk 2.20.0+。旧版 SDK 也可通过 `ctx.call_capability(...)` 调用同名 Host 能力，返回原始 `{success, result}` 结构。开发环境可使用 `MAIBOT_PLUGIN_SDK_PATH` 指向更新后的 SDK 仓库。

插件 `_manifest.json` 声明实际使用的能力：

```json
{
  "capabilities": [
    "personality.list",
    "personality.get_current",
    "personality.switch"
  ]
}
```

```python
available = await self.ctx.personality.list()
names = [profile["name"] for profile in available["profiles"]]
current = await self.ctx.personality.get_current()
result = await self.ctx.personality.switch("温柔的麦麦")
```

| SDK 方法 | Host 能力 | 返回值 |
| --- | --- | --- |
| `list()` | `personality.list` | `{"profiles": [人设对象, ...]}` |
| `get_current()` | `personality.get_current` | 当前人设对象 |
| `switch(name)` | `personality.switch`，参数 `{"name": "人设名称"}` | `{"changed": true, "current": 人设对象}` |

人设对象含 `name`、`personality`、`behavior_style`、`reply_style`、`group_chat_prompt`、`private_chat_prompts`、`multiple_reply_style`、`multiple_probability`。当前人设未命名时 `name` 为空字符串。列表与当前查询读取运行时快照；WebUI 的配置保存和热重载完成后可读到最新内容。

切换按名称精确匹配，作用于全局所有会话。先将切换前的已保存编辑留在人设库中，再写入目标人设并热重载。尚未入库的当前人设自动以“切换前人设”留档。切换到当前名称不覆盖当前编辑，返回 `changed: false`，仍检查热重载是否成功。浏览器中尚未保存的编辑不会被 SDK 读取。

未知人设、缺少能力授权、非法参数、写文件失败或热重载失败都会抛出能力调用异常。热重载失败时，配置可能已写入磁盘，异常消息会明确说明；可查询当前状态并重试。已开始生成的回复不会被此接口取消。

旧 SDK 调用示例（需本次更新后的 Host）：

```python
response = await self.ctx.call_capability("personality.switch", name="温柔的麦麦")
result = response["result"]
```

群聊和私聊提示词随人设一起保存、切换，分别写入 `chat.reply_style.group_chat_prompt` 与 `chat.reply_style.private_chat_prompts`。两项字段必须提供字符串，空字符串表示清空。SDK 2.20.0 的调用方式不变，需要搭配更新后的 Host。
