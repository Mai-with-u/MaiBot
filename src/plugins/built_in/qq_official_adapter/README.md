# MaiBot QQ 官方机器人适配器

直连 [QQ 开放平台](https://q.qq.com/)的 麦麦 qq 适配器

## 部署

本适配器随 MaiBot 内置提供，随本体更新，不支持单独更新或卸载；可在插件管理页启用或停用，仍可查看插件市场详情。

升级后会自动迁移已安装版本的配置，原插件目录保存在 `plugins/.builtin-backups/`；插件 ID 不变，继续使用原持久数据。

需要 MaiBot >= 1.4.0；禁言和撤回工具仅在 QQ 平台提供。

1. 在 [QQ 开放平台](https://q.qq.com/)完成开发者入驻并创建机器人，在控制台取得 **AppID** 与 **AppSecret（ClientSecret）**。在控制台为机器人开通群聊/单聊相关能力。QQ 的 OpenID 不是普通 QQ 号。
2. 在 WebUI 的「QQ 官方机器人适配器 → QQ 开放平台」配置中填写 **AppID** 和 **AppSecret**。
3. 在已配置的测试群 **@机器人 发一条纯文本**，或由测试用户发起单聊并发送纯文本，观察 MaiBot 是否创建聊天流并回复。到 WebUI 聊天页的「适配器策略」检查 `qq` 平台是否被放行`。

配置示例（`config/builtin_plugins/qq_official_adapter/config.toml`）：

```toml
[plugin]
enabled = true
config_version = "0.4.16"

[qq_official]
app_id = "你的 AppID"
app_secret = "你的 AppSecret"
api_base_url = "https://api.bot.qq.com"
# 以下三项在 WebUI 的“其他来源信息同步”节显示，配置文件仍保存在 [qq_official] 下。
# 可选：统一账号 ID（如 NapCat 机器人 QQ 号）。填写后聊天流按该 ID 归属，
# 与同一 platform 下的其它适配器（如 SnowLuma/NapCat）共享上下文。
unified_account_id = ""
# 已绑定群内，静默与 SnowLuma / NapCat 配对用户身份；需填写上方 unified_account_id。
silent_user_sync = true
# 默认关闭；仅官方连接在线并持续续期时暂停 SL/NapCat 的 MaiBot 收发路由。
snowluma_identity_only = false

[mute]
enabled = true
# 留空时允许所有群使用禁言与解除禁言工具。
allowed_groups = []

[recall]
enabled = true
# 留空时允许所有群使用撤回工具，单聊不受群白名单限制。
allowed_groups = []

[kick]
# 默认关闭；需要 QQ 官方移除群成员接口权限。
enabled = false
# 留空时允许所有群使用；建议按需指定群。
allowed_groups = []
# 关闭映射时保护名单只匹配用户 OpenID；启用映射时也可匹配关联 QQ 号。
admin_users = []

[advanced]
# 在日志中逐条显示主动／被动发送方式，被动发送同时标出目标消息 ID 和回复序号。
show_debug_info = false
# 断线重连间隔（秒）。
reconnect_delay_sec = 5.0

[filter]
# 默认忽略官方事件中 author.bot=true 的机器人消息，避免互相回复；绑定命令也不执行。
ignore_other_bots = true
```

## 出站消息

- 文本、@（含 @ 时以 markdown 发送以渲染真实 @）、图片与表情（jpg/png/gif/webp/bmp）、语音（SILK/MP3/WAV/OGG，最大 20 MB）、MP4 视频与普通文件（最大 100 MiB）。群聊与单聊均可使用。
- 官方一条消息只能承载一种内容，混合消息按原顺序拆成多条发送；每条各占一个被动回复序号（群聊每条入站消息最多被动回复 5 次）。
- 图片/表情、语音、视频与文件走官方分片上传（`upload_prepare` → 分片 PUT → `upload_part_finish` → `files` 合并）取得 `file_info`，再以 `msg_type=7` 发送；视频使用 `file_type=2`，语音使用 `file_type=3`，普通文件使用 `file_type=4`。
- 语音接收标准 `voice` 段的 `binary_data_base64`，按文件头识别格式，可直接发送 TTS 插件生成的 MP3；适配器不负责语音合成或转码。空数据、非法 Base64、未知格式及超限语音会明确返回发送失败，不转成文字或普通文件。
- 普通文件接收标准 `file` 段，保留 `data.name` 文件名。支持宿主签发的 `file_ref` 下载票据或小文件的 `data.base64`；不读取任意本机路径，也不直接下载插件自填 URL。宿主附件在主程序检查聊天归属后下载，大小不符、超限、下载或上传失败直接报告失败。
- 视频可用 SDK `send.custom("video", {"name": "sample.mp4", "base64": "..."}, stream_id)` 发送；宿主的 `dict` 包装由适配器识别。普通 `file` 段即使包含 MP4 也仍按文件发送，不自动改变媒体类型。Base64 传输还受宿主 RPC 消息大小限制，大文件应使用文件服务。
- 被动回复只使用宿主明确传入的 `reply_to` 锚点；无锚点的定时推送按主动消息发送，不占用最近入站消息的回复额度。`reply_to` 不决定客户端引用样式，显示引用须提供标准 `reply` 段。

## 扫码登录

启用适配器后，在 WebUI「适配器管理 → QQ 官方机器人适配器」配置页点击「生成 / 刷新二维码」，使用手机 QQ 扫码并在官方页面完成机器人授权。成功后会将 AppID / AppSecret 原子保存到本插件的 `config.toml`，自动启用并连接；页面显示 AppID、机器人名称和实际连接状态，不显示 AppSecret。已有机器人凭据会被替换，请确认扫码授权的是目标机器人。

支持取消扫码、过期后重新生成二维码；二维码最多等待 5 分钟，官方提前返回过期时也会停止。关闭页面不会取消已经发起的授权，可重新打开查看进度。保存阶段会完成写入及连接切换后再结束，避免出现已取消但凭据仍被替换的情况。原有手动配置方式继续可用。

协议参考腾讯官方 [OpenClaw QQBot 登录实现](https://github.com/tencent-connect/openclaw-qqbot/blob/89988812442583bf47c34b89f9abf5fd4b85df0b/src/setup/login.ts)及其 `@tencent-connect/qqbot-connector@1.2.0`：创建绑定任务、轮询结果、使用 AES-256-GCM 解密凭据；二维码在本地生成。插件依赖由 `_manifest.json` 声明，无需 Node.js。

## 查看及绑定 ID 映射

WebUI 侧边栏「插件扩展 → QQ 群与用户映射」展示群号、历史 QQ 号、Union ID、原始 OpenID 及头像；用户记录同时显示昵称、群名片、所属群、关联来源和冲突状态。自动记录按群分别展示，尚未关联 QQ 号的官方 Union 身份也会展示。绑定或静默同步后点击刷新即可更新。页面提供用户数据灌注；头像按需加载，群 OpenID 仍需绑定真实群号。

「其他来源信息同步」中的 `enable_mapping`（启用映射）默认关闭：关闭时用户 `user_id` 保持原 OpenID，不应用手动关联或静默同步的 QQ 号映射；开启后才按已有映射使用 QQ 号。关联记录始终保留。消息中的 `author.union_openid` 只记录关联，不触发数据迁移或身份切换。适配器继续使用原 OpenID，Union 身份迁移已停用。群 ID 继续使用原群号映射；成员和单聊 OpenID 按 AppID、群分别保存，供官方 API 使用，不混用不同场景。

两条绑定命令注册在本体命令系统中，权限为 `operator`，通过本体「命令管理」统一授权、停用及设置别名。授权对象使用当前用户 ID，可在聊天记录中查看；手动迁移后需按新身份调整权限名单。适配器不再维护独立的管理员名单。

- `/assign_group_id <群号>`：把当前群的 group_openid 绑定到指定群号，群聊可用。
- `/assign_id <QQ号>`：记录发送者的历史 QQ 号，用于迁移依据、旧参数解析和映射模式下的保护名单；`/assign_id @某人 <QQ号>` 可为被 @ 的用户关联。迁移前仅在启用映射时按该 QQ 号接收消息。

手动关联持久化到 `data/plugins/<插件ID>/id_map.json`；平台确认的 Union 身份和历史 QQ 号依据另外保存在 `union_user_ids.json`。出站私聊、群 @、禁言及踢人均反查目标场景的原始 OpenID，缺少对应场景记录时明确报错。命令由本体鉴权和拦截，结果回复到原消息所在会话。

绑定群号或修改 `unified_account_id` 仍可能改变聊天流。用户数据可通过本体用户数据灌注合并；重新绑定命令不搬迁历史数据。`unified_account_id` 只能在部署配置中修改。

注意：session 归属包含平台、机器人账号、作用域及群/用户 ID。通过本体迁移合并用户后，后续上报的来源 ID 会由主程序别名归一到目标 ID；仍需对齐机器人账号及作用域才能共享私聊会话。

## 用户管理与数据灌注

QQ 自定义页面默认展示可搜索、浏览和复制 ID 的 QQ 用户列表，并提供登录状态页。用户列表采用紧凑行高、限高滚动和分页，右上角可直接保存映射模式开关（不迁移历史数据）。仅在映射模式下显示群映射和用户映射标签；人物编辑仍使用本体「个体与账号」页面。用户 ID 使用原 OpenID，只有开启默认关闭的「启用映射」时才应用 QQ 号映射。Union OpenID 仅作为平台附加信息记录，不用于选择用户 ID，已移除 Union 迁移入口。

主程序「数据 → 用户数据灌注」调用本体人物迁移接口，将来源人物资料、当前画像和有效长期记忆合并至目标用户，历史消息、工具记录和旧私聊会话保留原样，冲突逐项确认；预览和执行均在线进行。冲突内容变化时需重新确认，执行失败回滚事务。灌注会将来源身份登记为目标别名，后续消息也归入目标身份。

映射模式下，「用户映射」按 QQ 号展示迁回 OpenID 的进度。点击「迁移预览」进入主程序「数据 → 用户数据灌注」；一个 QQ 号有多个 OpenID 时必须选择目标。确认冲突并执行后，返回进度页继续处理其余用户。全部已迁移或没有人物/消息数据时，点击「完成并关闭映射」切换为原始 OpenID。历史 QQ 关联与静默资料同步配置保留；非映射模式不展示映射表。迁移不涉及群 ID。

## 群成员静默同步

同时更新 QQ 官方适配器和统一 QQ 连接器（SnowLuma / NapCat）。先使用 `/assign_group_id <群号>` 绑定群，在官方适配器的「关联QQ号」`unified_account_id` 填写 SnowLuma / NapCat 当前登录的机器人 QQ 号，并保持 `silent_user_sync = true`。无需成员另外发送验证码，协作过程不会向群内发送消息。

两边收到同一群的消息后，通过规范化正文的 SHA-256 指纹和平台时间（相差不超过 2 秒）匹配。等待 5 秒让乱序消息到齐，必须双向唯一、至少三条不同正文一致，才确认用户 OpenID 对应的 QQ 号。短于 8 字符的正文、重复字符刷屏、命令以及带图片等媒体的消息不作为身份确认依据；@ 和引用段不参与指纹。双方必须实际收到足够多的可匹配消息，否则保持未绑定。

确认后，仅在启用映射时使用 QQ 号映射。昵称优先使用官方 `username`，群名片使用 `member_nickname`（包括清空），未下发时使用一致且无冲突的 SnowLuma 资料。手动关联与自动匹配不一致时不采用自动资料。推断仍限定 AppID、关联机器人账号、群 OpenID 和真实群号，不用于私聊；三条不同正文、双向唯一、冲突暂停等规则保持不变。

## 官方消息身份字段

支持 2026-11-16 更新后的群 @、全量群和单聊事件：`author.username` 写入标准 `user_nickname`，群聊 `author.member_nickname` 写入标准 `user_cardname`；@ 对象的昵称和群内昵称分别写入标准 at 段，展示优先使用群内昵称。

平台字段保存在 `message_info.additional_config`，供插件读取：

- `qq_official_author_id`、`qq_official_author_username`、`qq_official_author_avatar`、`qq_official_author_bot`：发送者原始标识、昵称、头像 URL 和机器人标记。
- `qq_official_author_union_openid`：跨应用用户标识。
- `qq_official_author_member_role`、`qq_official_author_member_nickname`：群内角色和昵称，仅群聊。
- `qq_official_union_group_openid`：跨应用群标识，仅群聊。
- `qq_official_mentions`：原始 @ 对象列表，保留角色、群内昵称、`is_you`、`scope` 等信息，仅群聊。

字段按官方实际推送保存，缺失的可选字段不生成；`author.union_openid` 是可选的跨应用附加字段，不作为用户 ID。`union_group_openid` 仍只保留为元数据，不替换群 ID。原始用户和群 OpenID 使用 `qq_official_user_openid`、`qq_official_group_openid`，供路由和官方 API 使用。

两边短期观察缓存各最多 2000 条，每群最多 128 条、保留 120 秒；官方侧候选最多 1000 个、保留 10 分钟，重复证据不会累计。确认结果原子保存到 `auto_user_profiles.json`，最多 5000 条，90 天未更新时失效；对应 Union 的已确认关联同时记录到 `union_user_ids.json`，供身份关联和路由校验使用。关闭 `silent_user_sync` 停止新的匹配和资料更新，已有关联记录保留；匹配出现冲突时撤销该范围的自动 QQ 号依据。只交换指纹和身份信息，不保存聊天正文。

已经由管理员确认并执行的数据迁移不会因后续指纹冲突自动撤销；这种情况需要人工核对已合并身份，避免用新的推断覆盖既有数据归属。

### SL 仅作身份查询（默认关闭）

官方短暂断线时保留租约剩余时间，不立即释放；重连后先确认 SL 已暂停，再开放官方收发路由，避免两边在后台续租前同时发送。

在官方适配器的 QQ 开放平台配置中打开 `snowluma_identity_only`，并填写正确的「关联QQ号」。官方连接就绪且 WebSocket 打开时，每 5 秒调用 SL 的 `identity_only_lease` 接口续期；SL 只有收到续期才会暂停该账号的群消息、私聊和通知（包括合成通知）向 MaiBot 投递，继续采集群身份指纹、维护连接及提供查询，避免 SL 单独触发思考和回复。此开关独立于是否正在查询消息指纹，空闲时也需持续续期。

暂停状态只在 SL 内存中保存，固定 15 秒有效，普通身份查询不会续期。官方掉线、卡住或退出后，不再续期，在最后一次续期后 15 秒自动恢复普通收发路由；正常关闭开关或卸载时会主动释放。SL 自身断连、停用或重启会清空状态，必须重新续期才能激活。暂停期间的消息直接跳过投递，不在恢复后补发；同时向 Host 撤销出站网关，避免同一条回复被官方与 SL 各发一次。底层连接、身份采集和查询接口保留，租约到期任务会独立恢复路由，不依赖新消息或新查询到达。

## 禁言工具

提供 `set_mute` LLM 工具（参数：`msg_id`、`duration`、可选 `reason`）。按目标成员发过的消息 ID 解析群和用户：`duration > 0` 表示禁言对应秒数，`duration = 0` 表示解除禁言，负数或非整数返回参数错误。调用官方 `POST /v2/groups/{group_openid}/restrict_chat_setting` 接口。由配置 `[mute].enabled` 开关（默认开启，WebUI 插件配置「禁言工具」中可切换，保存即生效）；启用后受以下配置约束：

- `allowed_groups`：允许使用禁言的群（统一群号或 group_openid）；留空不限制
- `admin_users`：保护名单（关闭映射时仅匹配 OpenID；启用映射时也可匹配关联 QQ 号），名单内用户不会被禁言
- `min_duration`/`max_duration`：时长限制（秒），官方上限 30 天，接口限频 60 QPM

平台限制：只能禁言普通成员（群主、管理员、机器人不可被禁言）；目标用户需能解析出 OpenID——其消息经本适配器接收时自动携带，否则需先 `/assign_id` 绑定。

解除禁言使用 `op=del`，零时长不应用最短禁言时间。保护名单只阻止施加禁言，不阻止解除禁言。机器人仍需具备群管理员权限，平台拒绝时直接报告失败。

## 撤回工具

提供 `recall_message` LLM 工具（参数：`msg_id`；所在群/单聊从该消息解析，调用官方 `DELETE /v2/groups/{group_openid}/messages/{message_id}` 或 `DELETE /v2/users/{user_openid}/messages/{message_id}` 接口）。由配置 `[recall].enabled` 开关（默认开启）；`allowed_groups` 限制可撤回的群（统一群号或 group_openid），留空不限制。

未填写工具配置或 `allowed_groups` 时，工具默认启用且不限制群；已有配置中显式设置的 `enabled = false` 仍会关闭对应工具。

平台限制：发送超过 2 分钟的消息不可撤回；群聊中机器人是群管理员时可撤回自己与普通群成员的消息，否则只能撤回自己发的消息；单聊只能撤回机器人自己发的消息。

## 踢人工具

提供 `kick_member` LLM 工具（参数：`msg_id`、可选 `reason`），从目标成员发过的消息解析群与用户，调用官方 `POST /v2/groups/{group_openid}/batch_remove_members` 移除单个成员，不加入群黑名单。`reason` 仅用于操作日志。

默认关闭。在 WebUI 的「QQ 官方机器人适配器 → 踢人工具」打开开关并保存即可启用，也可以设置 `[kick].enabled = true`。`allowed_groups` 限制可操作的群（统一群号或 group_openid），留空不限制；`admin_users` 为独立的踢人保护名单（关闭映射时仅匹配 OpenID；启用映射时也可匹配关联 QQ 号）。工具拒绝移除机器人自身，关闭后不向模型提供该工具。

目标消息必须能从宿主查询，并能解析出群和成员的官方 OpenID。机器人需具备相应群管理权限；[官方移除成员接口](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_batch_remove_members.post.html)目前仅对内邀机器人开放，未开通时返回错误码 `11253`。开启工具不会自动取得平台权限，平台拒绝时直接报告失败。

## 对外插件管理 API

提供以下公开 API（`version="1"`），命名和参数参考 OneBot 适配器：

| API | 参数 | 用途 |
| --- | --- | --- |
| `adapter.qq_official.group.set_group_ban` | `group_id: str`、`user_id: str`、`duration: int`、`reason: str = ""` | 禁言群成员，`duration=0` 解除禁言 |
| `adapter.qq_official.message.delete_msg` | `message_id: str`、`stream_id: str = ""` | 撤回聊天记录中的消息，可限定聊天流 |

调用方插件需声明 `api.call` 能力，通过完整 API 名称调用：

```python
result = await self.ctx.api.call(
    "Mai-with-u.qq-adapter.adapter.qq_official.group.set_group_ban",
    version="1",
    group_id="群 OpenID 或已绑定群号",
    user_id="用户 OpenID 或已绑定 QQ 号",
    duration=120,
)

result = await self.ctx.api.call(
    "Mai-with-u.qq-adapter.adapter.qq_official.message.delete_msg",
    version="1",
    message_id="聊天记录中的平台消息 ID",
    stream_id="目标聊天流 ID",
)
```

禁言 API 无需提供目标成员的消息 ID，支持人工绑定与目标群内的静默身份映射；未绑定的数字群号或 QQ 号无法直接用于官方接口。撤回 API 从宿主消息记录解析群聊或单聊目标，因此消息必须可查询，且能解析出官方 OpenID。

返回字典：成功时 `success=true`，并附带操作信息；失败时 `success=false`、`error` 包含失败原因。禁言返回实际生效的 `duration`；正时长沿用配置的最短/最长限制，零时长不受限制。两个 API 分别受 `[mute].enabled`、`[recall].enabled` 控制，并共用工具的群白名单、禁言保护名单和平台权限限制。QQ 拒绝操作时返回失败。

## 头像接口

提供公开 API `adapter.avatar.get`（版本 `1`），与 SnowLuma 适配器使用相同的头像协议。参数为 `platform`、`target_id`、`target_type`（`user` 或 `group`，默认 `user`），并接受宿主传入的 `account_id` 和 `scope`。

- 数字 QQ 号、群号返回头像 URL 和 `expires_in = 86400`，图片由宿主统一下载、缓存。
- OpenID 已通过 `/assign_id` 或 `/assign_group_id` 绑定真实数字 ID 时，使用绑定值查询头像。
- 用户头像优先使用消息下发的 `author.avatar`，按应用、原始 OpenID 和当前映射用户 ID 缓存最多一天、最多 2000 条；缓存未命中时沿用数字 QQ 号或 AppID 对应的 OpenID 头像路由。机器人自身头像来自 `/users/@me`，按应用和账号缓存最多一小时。
- 未绑定群 OpenID 及非 `qq` 平台返回 `status = "unsupported"`。

## 戳一戳

QQ 开放平台官方 API 目前没有提供戳一戳接口（截至 2026-09-16 的官方变更记录），本适配器暂不支持。

## 参考致谢

- [qq-official-adapter](https://github.com/WhiteCloudOL/qq-official-adapter)（作者：[清蒸云鸭](https://github.com/WhiteCloudOL)）：为本适配器开发提供了参考和借鉴。


## 配置页运行时控件

需要支持运行时控件的新版主程序及 SDK >= 2.19.0。

启用适配器后，在配置页点击“生成 / 刷新二维码”，使用手机 QQ 授权。配置页同时显示连接状态，并可取消扫码或重新连接。扫码成功后 AppID / AppSecret 沿用原有配置保存流程；二维码、临时密钥和实时状态不写入 TOML。群与用户映射扩展页继续保留。

本地开发可设置 `MAIBOT_PLUGIN_SDK_PATH` 指向新版 SDK 仓库，然后重启主程序。

群映射和用户映射使用公共分页表格，支持检索、选择当前页、全选筛选结果及批量删除。删除以 OpenID 为单位清理手动关联、相关静默资料与官方身份关联；删除群绑定时同时清理该群的静默关联。操作会短暂重连，不删除人物资料或历史消息；新的消息仍可重新触发静默匹配。绑定文件采用持久化提交记录，中断后重载先完成删除，再加载身份。页面查询与操作统一排队，避免轮询、映射和迁移进度请求竞争插件并发名额。
