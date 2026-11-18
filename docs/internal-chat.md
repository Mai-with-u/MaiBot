# 内部聊天

网页聊天室普通模式和桌宠统一使用 `platform=internal`、`user_id=owner`、机器人账号 `self`。
已有对应聊天流时复用，没有则由正常会话创建链路创建。
插件通过 SDK 2.17.0 的 `ctx.internal_chat.identity()` 查询身份，
通过 `ctx.internal_chat.send(text)` 发言，不应自行计算 session_id。

发言昵称由主程序统一保存在 `data/internal_chat_profile.json`。第一次网页普通聊天连接时采用该浏览器已有昵称，之后以服务端保存的值为准；在输入框旁修改会同步到其他网页，桌宠每次发言读取同一昵称。虚拟身份昵称独立处理。

旧 `webui` 私聊保留原归属，不自动迁移、合并，也不作为 `internal` 的别名读取。
启动时不会为这一变更备份数据库或写入迁移任务。
虚拟群聊继续使用其配置的平台和群身份。

已经执行过此前迁移的实例继续使用已迁入 `internal` 的数据；移除迁移代码不会自动回退数据，
也不会删除此前生成的备份或维护任务记录。

## SDK 开发联调

主程序当前要求 `maibot-plugin-sdk>=2.23.0`。该版本将 Tool 和回复扩展的 `platforms`、
`chat_scope`、`allowed_session` 统一为组件声明顶层字段，分别控制平台、聊天类型和指定会话，
不进入模型调用参数。升级 SDK 时应同时更新 MaiBot，确保 Host 读取同一声明格式。

联调尚未发布到 PyPI 的 SDK 时，先安装主程序所需的本地 SDK，再设置源码路径：

```powershell
uv pip install -e ../maibot-plugin-sdk
$env:MAIBOT_PLUGIN_SDK_PATH = (Resolve-Path ../maibot-plugin-sdk).Path
uv run --no-sync bot.py
```

Runner 和版本校验会显式使用本地 SDK。使用 SDK 2.23.0 作用域声明的插件 manifest
也应要求 `maibot-plugin-sdk>=2.23.0`，不满足时会报告版本不兼容。
桌宠已[内置到主程序](desktop-pet.md)，自身不再依赖插件 SDK；插件可通过 `ctx.desktop_pet` 控制它。
