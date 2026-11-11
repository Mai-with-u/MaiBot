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
config_version = "0.4.15"

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

WebUI 侧边栏「插件扩展 → QQ 群与用户映射」展示群号、用户 QQ 号及头像；用户记录同时显示昵称、群名片、所属群、手动或自动来源和冲突状态。自动记录按群分别展示，与旧手动记录并列；旧记录未保存的名称显示「—」，手动用户映射的作用域标为「全局」。仅显示当前账号、仍绑定群内的未过期自动记录；冲突记录保留并明确标出暂停或手动优先生效的原因。绑定或静默同步后点击刷新即可更新。页面为只读，无绑定时显示命令提示。头像按需加载，失败时显示原因；未绑定用户 OpenID 可获取头像，群 OpenID 仍需绑定真实群号。

QQ 官方平台的 OpenID 与真实 QQ 号不互通。为了让消息以真实 QQ 号归属（与 NapCat 系适配器的数据统一），可在聊天内发送绑定命令：

两条绑定命令注册在本体命令系统中，权限为 `operator`，通过本体「命令管理」统一授权、停用及设置别名。授权对象使用该消息在本体中的用户 ID；尚未绑定的官方用户以 OpenID 标识，可在聊天记录中查看。适配器不再维护独立的管理员名单。

- `/assign_group_id <群号>`：把当前群的 group_openid 绑定到指定群号，群聊可用。
- `/assign_id <QQ号>`：把发送者的 OpenID 绑定到指定 QQ 号；`/assign_id @某人 <QQ号>` 可为被 @ 的用户绑定。

绑定立即生效并持久化到 `data/plugins/<插件ID>/id_map.json`：入站消息的群/用户 ID 按映射替换（原始 OpenID 保留在消息路由信息中），出站发送时自动反查回 OpenID 调用官方 API。命令由本体匹配、鉴权和拦截，不进入普通对话处理；绑定结果回复到原消息所在会话。

绑定群号、绑定私聊用户或修改 `unified_account_id` 都可能改变会话身份，使后续消息进入另一聊天流。旧消息、上下文及按聊天流保存的学习结果仍属于旧会话，不会自动搬迁或合并；建议开始聊天前完成身份配置。已有历史时不要把重新绑定当作迁移工具。`unified_account_id` 只能在部署配置中修改，聊天内的绑定命令不会修改它。

注意：session 归属按 `platform + 账号 ID + 群/用户 ID` 计算。只做群/用户绑定时，ID 数据可与其它适配器对齐，但聊天流仍按本适配器的账号 ID 区分；填写 `unified_account_id` 并对齐群/用户 ID 后，后续消息可归入同一会话，已有历史不会自动合并。

## 群成员静默同步

同时更新 QQ 官方适配器和统一 QQ 连接器（SnowLuma / NapCat）。先使用 `/assign_group_id <群号>` 绑定群，在官方适配器的「关联QQ号」`unified_account_id` 填写 SnowLuma / NapCat 当前登录的机器人 QQ 号，并保持 `silent_user_sync = true`。无需成员另外发送验证码，协作过程不会向群内发送消息。

两边收到同一群的消息后，通过规范化正文的 SHA-256 指纹和平台时间（相差不超过 2 秒）匹配。等待 5 秒让乱序消息到齐，必须双向唯一、至少三条不同正文一致，才确认用户 OpenID 对应的 QQ 号。短于 8 字符的正文、重复字符刷屏、命令以及带图片等媒体的消息不作为身份确认依据；@ 和引用段不参与指纹。双方必须实际收到足够多的可匹配消息，否则保持未绑定。

确认后，后续官方群消息使用真实 QQ 号、SnowLuma 的昵称和群名片；昵称和名片也会通过后续短消息刷新，清空群名片同样会同步。推断限定 AppID、关联机器人账号、群 OpenID 和真实群号，不用于私聊，不覆盖人工 `/assign_id` 映射，冲突会记录警告并暂停自动身份使用，已有 QQ 号不自动改绑；人工 `/assign_id` 可以明确指定正确身份。历史消息不会回写。

两边短期观察缓存各最多 2000 条，每群最多 128 条、保留 120 秒；官方侧候选最多 1000 个、保留 10 分钟，重复证据不会累计。确认结果与人工映射分开，原子保存到插件数据目录的 `auto_user_profiles.json`，最多 5000 条，90 天未更新时失效。待匹配记录在停用时清空；只交换指纹和必要身份信息，不在缓存中保存聊天正文。单独关闭其中一个适配器不影响正常收发；关闭 `silent_user_sync` 则停止推断并停用自动身份转换。

### SL 仅作身份查询（默认关闭）

官方短暂断线时保留租约剩余时间，不立即释放；重连后先确认 SL 已暂停，再开放官方收发路由，避免两边在后台续租前同时发送。

在官方适配器的 QQ 开放平台配置中打开 `snowluma_identity_only`，并填写正确的「关联QQ号」。官方连接就绪且 WebSocket 打开时，每 5 秒调用 SL 的 `identity_only_lease` 接口续期；SL 只有收到续期才会暂停该账号的群消息、私聊和通知（包括合成通知）向 MaiBot 投递，继续采集群身份指纹、维护连接及提供查询，避免 SL 单独触发思考和回复。此开关独立于是否正在查询消息指纹，空闲时也需持续续期。

暂停状态只在 SL 内存中保存，固定 15 秒有效，普通身份查询不会续期。官方掉线、卡住或退出后，不再续期，在最后一次续期后 15 秒自动恢复普通收发路由；正常关闭开关或卸载时会主动释放。SL 自身断连、停用或重启会清空状态，必须重新续期才能激活。暂停期间的消息直接跳过投递，不在恢复后补发；同时向 Host 撤销出站网关，避免同一条回复被官方与 SL 各发一次。底层连接、身份采集和查询接口保留，租约到期任务会独立恢复路由，不依赖新消息或新查询到达。

## 禁言工具

提供 `set_mute` LLM 工具（参数：`msg_id`、`duration`、可选 `reason`）。按目标成员发过的消息 ID 解析群和用户：`duration > 0` 表示禁言对应秒数，`duration = 0` 表示解除禁言，负数或非整数返回参数错误。调用官方 `POST /v2/groups/{group_openid}/restrict_chat_setting` 接口。由配置 `[mute].enabled` 开关（默认开启，WebUI 插件配置「禁言工具」中可切换，保存即生效）；启用后受以下配置约束：

- `allowed_groups`：允许使用禁言的群（统一群号或 group_openid）；留空不限制
- `admin_users`：保护名单（统一 QQ 号或 OpenID），名单内用户不会被禁言
- `min_duration`/`max_duration`：时长限制（秒），官方上限 30 天，接口限频 60 QPM

平台限制：只能禁言普通成员（群主、管理员、机器人不可被禁言）；目标用户需能解析出 OpenID——其消息经本适配器接收时自动携带，否则需先 `/assign_id` 绑定。

解除禁言使用 `op=del`，零时长不应用最短禁言时间。保护名单只阻止施加禁言，不阻止解除禁言。机器人仍需具备群管理员权限，平台拒绝时直接报告失败。

## 撤回工具

提供 `recall_message` LLM 工具（参数：`msg_id`；所在群/单聊从该消息解析，调用官方 `DELETE /v2/groups/{group_openid}/messages/{message_id}` 或 `DELETE /v2/users/{user_openid}/messages/{message_id}` 接口）。由配置 `[recall].enabled` 开关（默认开启）；`allowed_groups` 限制可撤回的群（统一群号或 group_openid），留空不限制。

未填写工具配置或 `allowed_groups` 时，工具默认启用且不限制群；已有配置中显式设置的 `enabled = false` 仍会关闭对应工具。

平台限制：发送超过 2 分钟的消息不可撤回；群聊中机器人是群管理员时可撤回自己与普通群成员的消息，否则只能撤回自己发的消息；单聊只能撤回机器人自己发的消息。

## 头像接口

提供公开 API `adapter.avatar.get`（版本 `1`），与 SnowLuma 适配器使用相同的头像协议。参数为 `platform`、`target_id`、`target_type`（`user` 或 `group`，默认 `user`），并接受宿主传入的 `account_id` 和 `scope`。

- 数字 QQ 号、群号返回头像 URL 和 `expires_in = 86400`，图片由宿主统一下载、缓存。
- OpenID 已通过 `/assign_id` 或 `/assign_group_id` 绑定真实数字 ID 时，使用绑定值查询头像。
- 未绑定用户 OpenID 通过 AppID 对应的 QQ 头像路由查询；机器人自身头像来自 `/users/@me`，按应用和账号缓存最多一小时。
- 未绑定群 OpenID 及非 `qq` 平台返回 `status = "unsupported"`。

## 戳一戳

QQ 开放平台官方 API 目前没有提供戳一戳接口（截至 2026-09-16 的官方变更记录），本适配器暂不支持。

## 参考致谢

- [qq-official-adapter](https://github.com/WhiteCloudOL/qq-official-adapter)（作者：[清蒸云鸭](https://github.com/WhiteCloudOL)）：为本适配器开发提供了参考和借鉴。


## 配置页运行时控件

需要支持运行时控件的新版主程序及 SDK >= 2.19.0。

启用适配器后，在配置页点击“生成 / 刷新二维码”，使用手机 QQ 授权。配置页同时显示连接状态，并可取消扫码或重新连接。扫码成功后 AppID / AppSecret 沿用原有配置保存流程；二维码、临时密钥和实时状态不写入 TOML。群与用户映射扩展页继续保留。

本地开发可设置 `MAIBOT_PLUGIN_SDK_PATH` 指向新版 SDK 仓库，然后重启主程序。
