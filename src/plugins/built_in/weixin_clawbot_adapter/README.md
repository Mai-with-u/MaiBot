# 微信 ClawBot 适配器

通过微信 iLink / ClawBot 会话与麦麦私聊。内置 Manifest v2 适配器，不需要 OpenClaw、AstrBot、WeFlow 或桌面微信。

当前版本 0.2.0：支持多账号独立扫码绑定和备注名。多账号已通过本地模拟验证，仍需真实微信扫码验收。

## 功能与边界

- 在「微信账号管理」页面添加账号并扫码，最多同时绑定 16 个账号；各自保存凭证、消息游标和去重记录，重启自动恢复。
- 每个账号可设置备注名、重新扫码或单独移除；操作一个账号不影响其它账号连接。
- 备注名用于绑定用户新消息的昵称显示。它是本地备注，不是微信接口返回的真实昵称；历史消息不会重写。
- 私聊文字、图片双向收发；微信语音转写作为文本输入。
- 表情图片按普通图片发送；引用回复按普通消息发送，不显示原生引用框。
- 网络异常自动重连，登录失效提示重新扫码；持久化同步游标和最近 2000 条消息 ID。
- 支持 iLink 用户 ID 允许列表；同时遵循麦麦的入站访问策略。
- 视频、文件和无转写语音只生成“不支持读取”的提示，不下载内容；不支持发语音。
- 不支持普通微信群、好友列表、朋友圈或读取其他会话。
- 主动发送需要之前收到的用户 `context_token`。服务端有效期与额度未经实测，不保证任意时间可主动发送。
- 图片默认上限 5 MB，最高 8 MB；整条消息受麦麦 16 MB IPC 帧限制。超大消息需拆开发送。

## 安装与登录

1. 本适配器随麦麦提供，位于 `src/plugins/built_in/weixin_clawbot_adapter`，无需复制到外部 plugins 目录。
2. 需要 MaiBot >= 1.3.20、maibot-plugin-sdk >= 2.21.0，以及 `requirements.txt` 中的依赖。插件运行环境应由麦麦安装依赖；独立 `.venv` 仅用于开发测试。
3. 在麦麦插件管理中加载插件，打开“微信 ClawBot 适配器”的设置，开启 `plugin.enabled` 并保存。
4. 打开侧边栏「插件扩展 → 微信账号管理」，填写备注名，点击「添加账号并扫码」。使用对应微信扫码确认；其他账号重复此步骤。主程序需支持 WebUI 页面协议 v2 的 custom_pages 能力。
5. 等状态变成“已连接：微信私聊”，从微信机器人会话发送一条文字，再发送图片、语音分别验证。

从 0.1.0 升级后重载插件即可。旧 account.json 自动作为第一个账号读取，原网关名、账号 ID 和聊天流归属保留，无需先清除登录态。重复绑定同一账号会拒绝，重新登录已有账号请使用其卡片上的「重新扫码」。

默认关闭连接，安装不会主动请求登录。二维码在本地生成，不通过第三方二维码网站。设置页操作在后台执行，轮询等待不会占用页面请求。

`allowed_users` 是所有账号共享的额外过滤列表，一般保持留空；留空允许所有收到的用户；填写的是 iLink 的用户 ID，不是常规微信号。可从麦麦新建会话中的平台用户 ID 获取。

```toml
[plugin]
enabled = false
config_version = "0.2.0"

[weixin]
allowed_users = []
retry_seconds = 5
max_image_mb = 5
```

## 状态与故障处理

登录凭证、备注名、用户会话凭证、同步游标分账号保存在 SDK 分配的数据目录中：首个账号使用 `account.json`，其余使用 `account-1.json` 至 `account-15.json`，采用临时文件原子替换。文件包含敏感凭证，不要分享或提交。Windows 使用所在目录的访问权限；POSIX 创建文件时设置为 0600。

「移除账号」只清除对应账号的上述内容，保留主程序中的聊天记录；它不是服务端注销接口。需要撤销绑定时，在微信 ClawBot 中操作。

- 二维码过期：自动刷新最多三次，之后手动刷新。
- 错误码 -14 / HTTP 401：仅清空该账号失效凭证，保留备注和账号归属，提示重新扫码。
- 缺少会话凭证：从微信先发一条消息。
- 网关 RPC 异常：不推进游标，下次重试；Host 明确返回过滤/重复时则推进，避免堵塞队列。
- 发送超时：结果可能已送达，不自动重发；多段消息错误会报告已确认发送段数。
- 入站是至少一次投递配合插件和 Host 去重，不承诺跨进程崩溃的严格 exactly-once。

## 开发验证

```powershell
uv venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt pytest pytest-asyncio ruff
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check --config pyproject.toml .
```

若 SDK 2.21 尚未发布，可在安装依赖时追加 `-e <本地 maibot-plugin-sdk 路径>`。

测试使用临时目录及本地 HTTP 服务，不连接真实微信，不使用个人账号凭证。

本适配器由 MaiBot Team 维护，插件 ID 为 `maibot-team.weixin-clawbot-adapter`。独立发布目标为 [Mai-with-u/MaiBot-Weixin-ClawBot-Adapter](https://github.com/Mai-with-u/MaiBot-Weixin-ClawBot-Adapter)，不发布到个人账号；此地址配置不代表已创建或发布远端仓库。请勿在外部 `plugins` 目录放置相同 ID 的副本。

内置运行所需的 aiohttp、Pillow、pycryptodome 和 qrcode 由本体依赖统一安装，manifest 不再重复声明安装任务。独立开发依赖仍由本目录的 pyproject.toml 与 requirements.txt 声明。

从旧 ID `local.weixin-clawbot-adapter` 切换时，应先停用旧实例，将旧 ID 数据目录的账号文件复制到新 ID 数据目录，并将 adapter_policy.toml 中对应的 plugin_id 和 adapter_id 归属更新为新 ID；保留备份后再启动新版。

## 协议参考

依据 [AstrBot 适配器（6c942855）](https://github.com/AstrBotDevs/AstrBot/tree/6c942855cb06b6d43854e8b841859d60a5c6043e/astrbot/core/platform/sources/weixin_oc) 观察到的 HTTP 字段独立实现，未引入其运行时或复制其适配器代码。登录条件参考 [AstrBot 文档](https://docs.astrbot.app/platform/weixin_oc.html)。微信服务端行为仍需真实账号验证。

官方协议参考：[Tencent/openclaw-weixin 中文协议](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)。
