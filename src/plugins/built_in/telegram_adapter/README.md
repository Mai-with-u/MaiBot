# MaiBot Telegram 适配器

本适配器随 MaiBot 内置提供，随本体更新，不支持单独更新或卸载。可在适配器管理中配置和启停，市场内容仍通过原有二级界面查看。

升级时自动迁移外部安装的配置和历史插件 ID 的数据库，迁移成功后移除外部安装，原目录保留于隐藏的 `.builtin-backups/` 备份目录。

基于 MaiBot SDK `MessageGateway` 的独立适配器插件，使用 Telegram Bot API 长轮询。一个插件实例连接一个机器人，机器人 ID 由 `getMe` 获取。群/用户白名单及麦麦回复策略统一由 MaiBot 控制，适配器不维护白名单。

## 支持范围

| 内容 | 接收 | 发送 |
| --- | --- | --- |
| 文本 | 私聊、普通群、超级群及论坛话题 | 保留正文，长文本自动分段 |
| 图片 | 照片、图片文件、caption | 上传图片，保留消息段顺序 |
| 静态贴纸 | 转成 PNG 表情段 | 优先复用原贴纸，否则生成 WEBP 贴纸 |
| TGS 动画贴纸 | 渲染成 GIF 表情段 | 优先复用原贴纸，支持直接上传 TGS |
| WEBM 视频贴纸 | 解码成 GIF 表情段 | 优先复用原贴纸，支持直接上传 WEBM |
| 普通动图 | GIF/动画作为表情段 | 使用 `sendAnimation`，不冒充静态贴纸 |
| 语音/音频 | 转成 16 kHz 单声道 WAV，交给宿主 ASR | 转成 OGG/Opus，以语音消息发送 |
| @ | 支持 username 和 `text_mention`，识别 @机器人 | username mention 或用户 ID 的 `text_mention` |
| 回复引用 | 保留目标 ID、发送者、可见文本，识别回复机器人 | 使用 `reply_parameters` |
| 原生转发 | 保留来源；来源隐藏时仅使用 TG 可见信息 | 对可解析的 TG 消息引用使用 `forwardMessage` |
| 合并转发 | 原生转发转换成标准转发节点 | 自建节点的连续图片按相册发送，说明文字作为图片下方的 caption，不添加人工转发标题；其他内容按原顺序发送，支持嵌套 |

频道接入、消息编辑、按钮交互、普通视频和任意文件尚未接入。论坛话题支持收发，回复及拆分后的每段媒体均发送到对应话题。相册中的每张图片作为独立消息接收。群管理员匿名发言以 `sender_chat` 的群身份记录，不伪造个人身份。

## 安装与配置

1. 本适配器位于 `src/plugins/built_in/telegram_adapter`，无需克隆到外部插件目录。
2. 依赖随 MaiBot 安装；各机器人的配置保存于 `config/builtin_plugins/telegram_adapter/config.toml`，持久数据保存于 `data/plugins/maibot-team.telegram-adapter/`。
3. 在 [BotFather](https://t.me/BotFather) 创建机器人，取得 Token。
4. 在 WebUI 插件配置中填写 `telegram.bot_token`，设置 `plugin.enabled = true`。SDK 会生成实际 `config.toml`；也可以以 `config.example.toml` 为模板手动配置。
5. 网络需要代理时填写 `telegram.proxy_url`，例如 `http://127.0.0.1:7890` 或 `socks5://127.0.0.1:7890`。留空直连，不读取环境变量代理。
6. 私聊机器人发送 `/start`，在日志中确认 `Telegram 网关已就绪`，然后测试聊天。需要接收群内普通消息时，在 BotFather 关闭 Privacy Mode，必要时重新添加机器人，或将机器人设为群管理员。
7. 在 MaiBot 的适配器策略中配置 `telegram` 平台、机器人账号、群/用户的放行规则。

Token 和代理配置在 WebUI 中使用密码控件，实际 `config.toml` 位于各机器人的配置目录。日志不要开启 HTTPX/HTTPCore 的 HTTP 请求调试级别，这些第三方日志可能包含 Bot API URL 中的 Token。

FFmpeg 可执行文件由 `imageio-ffmpeg` 提供；TGS 渲染使用 `rlottie-python` 的内置库，无需配置系统 PATH。首次依赖安装会下载 FFmpeg 平台包。语音识别是否启用、模型和识别效果由 MaiBot 的 ASR 配置决定；发送语音需要宿主或其他插件提供真实音频内容，适配器不负责 TTS。

配置保存后会停止旧连接并重新连接，Token、代理和连接参数均支持热更新。

## 路由与消息 ID

- 平台固定为 `telegram`，`account_id` 为机器人 ID，`scope` 为空。
- 普通群、General 和用户 ID 保留 Telegram 原值，群 ID 的负号不能删除。
- 论坛中的独立话题以 `chat_id:message_thread_id` 作为宿主群 ID，各话题拥有独立聊天流；白名单需放行对应的话题群 ID。发送时还原真实群 ID 并附带话题编号，不依赖是否引用原消息。
- 话题显示为“群名 / 话题名”。接收到话题创建/改名事件后保存名称；Bot API 的普通消息不含话题名，未获知名称时显示“群名 / 话题编号”。General 显示为“群名 / General”。
- 平台消息 ID 使用 `chat_id:message_id`，避免不同聊天的局部消息 ID 冲突。发送回执使用同样格式。
- 宿主负责聊天流创建与 `session_id` 解析。插件不计算备用会话 ID。
- @username 无法直接通过 Bot API 查到用户 ID，因此保留 username；`text_mention` 和 @机器人使用明确的用户 ID。
- 机器人收到的转发副本 ID 用于再次转发，原来源存入 `additional_config.telegram_forward_origin`，不依赖访问原频道的权限。
- 出站引用仅由显式 `reply` 消息段决定；宿主 `reply_to` 是上下文锚点，不自动转成 Telegram 引用，确保 `set_quote=false` 生效。
- `forwardMessage` 不支持回复参数：转发同时带引用时，先发送一条带引用的“转发消息：”，再发送原生转发。
- 合并转发的连续静态图片每组 2–10 张，超过 10 张自动拆组；落单图片单独发送，动画、贴纸、语音及文字保持原顺序。相邻的前置说明文字（不超过 1024 个 UTF-16 单位）放在首张图片 caption 中，仅显示一次；较长文字保持完整独立发送。
- 原生转发受 Telegram 权限与内容保护限制，源消息删除或受保护时返回明确失败，不自动改为复制。

## 输入状态

选中 `reply` 并开始生成回复时，适配器通过 `maisaka.replyer.before_request` Hook 显示机器人“正在输入”，每 4 秒刷新一次；媒体准备期间继续显示，实际发送前停止刷新，Telegram 收到机器人回复后自动清除提示。只接收消息而没有执行回复时不会显示。私聊、群聊和论坛话题分别处理，插件断开时取消刷新任务。为避免生成失败后任务永久存活，最多刷新 180 秒；状态接口失败会记录警告，不影响消息收发。

## 消息表态

订阅 `message_reaction` 与 `message_reaction_count`，把表态新增、取消、替换和匿名计数作为 `is_notify=true` 通知交给宿主。通知保留操作人（匿名统计不推断个人身份）、原消息引用和原始事件；使用 update_id 去重，避免同一消息的多次表态被丢弃。支持普通 emoji、自定义 emoji 标识和付费表态标识。

Telegram 要求机器人为群管理员才会投递表态事件。重载插件并将机器人设为管理员后，需要重新添加或取消表态来验证；此前未投递的事件无法补取。匿名统计可能延迟几分钟，机器人自己设置的表态不会收到对应事件。

插件通过 SDK 的 `Tool` 注册 `telegram_react_message(msg_id, emoji)`，Planner 可自主决定对当前 Telegram 聊天的真实消息表态，也可只表态而不发文字；无需修改主程序。每条消息最多一个普通 emoji，再次调用替换自己的表态，空字符串撤销。群内表情限制、消息类型或权限错误会明确返回，不会换成另一种表情或当作文字消息发送。群消息必须有已记录的真实话题归属，不能跨话题操作。主动表态不要求接收表态事件的管理员权限，但受 Telegram 本身的聊天权限限制。

工具只要求模型提供消息编号与表情，当前聊天上下文由宿主注入。插件使用 `chat.get_all_streams` 和 `message.get_by_id` 查询真实聊天流与消息，并校验账号、会话、聊天和话题归属。非 Telegram 聊天、其他账号和不属于当前会话的消息均返回明确错误。

工具声明 `platforms="telegram"`，支持平台作用域的宿主只会在 Telegram 聊天展示并允许执行该工具。通用插件可对每个 Tool 独立声明 `platforms="qq"`（单个平台）、`platforms=["qq", "telegram"]`（多个平台），或省略/使用 `None`、`[]`（不限平台）。名称忽略大小写及首尾空白；无效类型或空名称在组件注册时报错。该字段是工具元数据，不是模型调用参数；旧宿主会忽略此字段，插件仍保留执行时的 Telegram 校验。

## 用户与群头像

提供公开 API `adapter.avatar.get` v1，参数为 `platform="telegram"`、`target_id=群ID`、`target_type="group"`，并校验宿主传入的 `account_id` 与 `scope`。通过 `getChat` 的群头像文件 ID 下载原图，沿用插件代理；无头像返回 `missing`。用户头像使用 `target_type="user"` 和用户 ID，通过 `getUserProfilePhotos` 查询当前头像并下载最大尺寸；没有对机器人可见的头像返回 `missing`。

头像资源通过仅监听 `127.0.0.1` 随机端口的临时 HTTP 服务提供，URL 使用随机资源标识且不包含机器人 Token。宿主按统一协议下载并缓存 10 分钟；临时服务会在配置重载或插件卸载时关闭。WebUI 使用宿主缓存图片，不需要浏览器访问本机临时端口。插件与宿主需在同一台机器运行。

## 持久化与失败行为

游标和贴纸引用存储在宿主授予的 `data_dir/telegram.sqlite3`，按机器人账号隔离；媒体转换临时文件使用宿主的 `runtime_dir`，转换后清理。不会把 Token 写入数据库。

每条更新只有在转换与路由调用完成后才推进游标。宿主策略拒收也视为完成处理。网络错误及服务端 5xx 会重连，429 按 `retry_after` 等待，发送网络超时不自动重发，避免重复消息。

单条更新的媒体转换、协议校验或宿主路由错误会记录完整异常并持久化失败更新，随后继续接收后续消息；具体行为见下文“接收失败处理”。Telegram 只保留未接收更新最多 24 小时，插件无法承诺无限期保留。长期无消息时会清除过期游标，以适配 Telegram 一周无更新后随机生成新 `update_id` 的行为。

发送前会校验全部消息段并完成媒体转换。平台错误导致部分发送成功时，返回失败以及已发送的 ID 列表；不能把整批消息直接重发。首条发送消息 ID 为宿主回执 ID，其余 ID 记录在回执 metadata 中。

云端 Bot API 单文件下载通常限制为 20 MB；插件同时检查服务端文件大小和实际流长度。宿主 IPC 每帧上限为 16 MB，Base64 编码后的整条消息需低于该上限；大媒体会在调用宿主前明确报错。本地 Bot API 的 `--local` 文件路径模式暂不支持，需通过 HTTP 文件路径下载。图片、音频、TGS 转换错误不会替换为占位文本。

## 开发与验证

在插件目录执行：

```powershell
uv sync --group dev
uv run ruff check .
```

真实 Telegram 联调需要有效 Token 和网络，应验证私聊、群聊与论坛话题的收发、表态通知、头像获取及异常处理。仓库不包含测试文件、本地配置、配置备份和运行数据。

参考：[MaiBot SDK 开发指南](https://github.com/Mai-with-u/maibot-plugin-sdk/blob/main/docs/guide.md)、[Telegram Bot API](https://core.telegram.org/bots/api)、[Telegram Bot FAQ](https://core.telegram.org/bots/faq)。

话题消息及发送回执的归属按机器人账号持久化，重启后表态仍进入对应聊天流。论坛表态若原消息归属未知则跳过并记录日志，避免错误进入 General。

## 接收失败处理

单条消息的媒体转换、帧大小校验或宿主路由异常会记录错误日志及完整异常；原始 Telegram 更新、错误和堆栈保存在插件数据目录 `telegram.sqlite3` 的 `failed_updates` 表中，按机器人账号与 update_id 区分。失败记录与接收游标在同一事务内提交，然后继续处理后续更新；失败消息不冒充成功投递，也不自动重复投递，记录保留用于排查与人工恢复。

网络、服务端和限流错误保留当前更新并重连重试；鉴权错误、轮询冲突或数据库故障明确停止连接。取消任务不会确认当前消息。持久化失败时不会推进游标，避免没有失败记录就丢失原始更新。


## 配置页运行时控件

需要支持运行时控件的新版主程序及 SDK >= 2.19.0。

配置页新增连接状态及“重新连接”按钮。Telegram Bot API 使用 Bot Token，无需二维码登录；填写并保存 Token 后启用适配器即可。

本地开发可设置 `MAIBOT_PLUGIN_SDK_PATH` 指向新版 SDK 仓库，然后重启主程序。
