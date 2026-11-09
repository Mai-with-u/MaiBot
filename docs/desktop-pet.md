# 内置桌宠与插件 SDK

Android 用户可安装独立[安卓悬浮桌宠](../pet-clients/android/README.md)，连接局域网或远端宿主，复用形象、皮肤、聊天、待办、探索页面和 WebUI 认证。需要 Android 8.0 及以上、更新的系统 WebView 和悬浮窗权限；首版不支持手机自动读屏或应用监测，服务器端同样使用 `remote` 模式。APK 构建与分发位于 `pet-clients/android/`，不改变桌面客户端的构建方式。

桌宠无需安装 `sengokucola.desktop-pet` 插件。独立 Tauri 2 客户端提供 Windows、macOS 和 Linux 构建，可连接本机或远端 MaiBot 宿主，使用系统 WebView 和 Rust 原生能力。原有 Python 内嵌桌宠继续支持 Windows，需要 WebView2 Runtime；其依赖随 Windows 主程序安装，源码环境更新依赖可运行 `uv sync --inexact`（保留插件额外依赖）。

在 WebUI 主程序配置的「桌宠」启用 `desktop_pet.enabled`，默认关闭。`desktop_pet.mode` 默认为 `local`，由主程序启动并回收 Python 内嵌窗口；设置为 `remote` 后等待独立 Tauri 2 客户端连接。独立客户端连接本机 MaiBot 时也使用 `remote` 模式。两种模式共用桌宠网页界面和本体业务，配置支持热重载；窗口与网络通信在主程序事件循环之外运行。

旧桌宠插件会被明确阻止加载并显示内置替代提示，避免同时启动两个窗口；插件源码保留。继续读写 `data/plugins/sengokucola.desktop-pet/todos.json` 和 `window.json`，已有待办和窗口位置无需复制。临时 WebView 数据改用 `temp/desktop_pet`。

桌宠聊天继续使用 `internal / owner`，昵称由网页聊天室统一设置。麦麦给主人的回复在桌宠和网页同步展示；旧 `webui` 私聊不迁移。桌宠提交的消息保留界面来源标记，不具备受保护命令权限。主人聊天流有实际发言时会主动显示桌宠；聊天面板未展开时弹出消息气泡，不受菜单、待办或插件面板状态限制，点击气泡可查看聊天记录。

## Tauri 2 客户端与部署

### 服务器

1. 更新 MaiBot 本体，并正常启动宿主和目标 Bot。
2. 在目标 Bot 的 WebUI「桌宠」设置启用桌宠，将运行模式设为 `remote`。服务器无需安装图形桌面或启动桌宠窗口。
3. 在客户端填写宿主 WebUI 地址，点击「通过 WebUI 连接」，在打开的 WebUI 页面完成首次登录；多个机器人时再按名称选择目标。客户端通过宿主认证与 Bot 代理连接，不直接暴露 Bot 内部端口。
4. 公网部署使用 HTTPS/WSS。已有反向代理需要支持 WebSocket Upgrade，并允许长连接；客户端提供心跳和自动重连。宿主也可使用已有的 TLS 配置直接提供 HTTPS/WSS。

每个 Bot 同时接入一个独立桌宠。连接本机时填写本机宿主 WebUI 地址，例如 `http://127.0.0.1:8001`，端口以实际部署为准。`remote` 表示由独立客户端连接，并不要求宿主在另一台电脑；`local` 则由本体启动 Windows Python 内嵌窗口。模式显式选择，断线不会自动切换。

### 启动 Tauri 2 客户端

使用对应系统的分发包时，无需安装 Python、Node.js、Rust 或完整 MaiBot。Windows 使用 WebView2 Runtime，安装器在缺少时在线安装；macOS 使用系统 WKWebView，Linux 使用 WebKitGTK 4.1 及相应桌面运行库。启动后填写宿主地址，在 WebUI 页面中登录；只有多个机器人时才需要选择目标。

桌宠没有独立密码框，也不把密码写入本地配置。Windows、Linux 和 macOS 14 及以上为各服务器使用独立 WebView 数据存储；macOS 10.15～13 使用默认 WebKit 数据存储，遵循浏览器的 Cookie 域和路径隔离规则。宿主会话当前有效期为 8 小时；有效期内再次连接可复用，过期、撤销或宿主认证状态变化导致失效时重新显示 WebUI 登录页。桌宠 WebView 和另外打开的浏览器各自管理会话。

认证完成后切换到同源的独立轻量连接页，维持桌宠通信，不持续加载完整 React WebUI。服务端需要同步更新，提供 `/api/host/desktop-pet/session`；该页面复用现有宿主会话校验，未经认证返回 401，不绕过宿主登录或 Nginx 等反向代理认证。

若 Nginx 等反向代理启用 HTTP Basic Auth，原生 WebView 的认证对话框可能另行要求代理账号密码。凭据缓存受 WebView 和代理控制，缓存失效或重新启动后可能需要再次认证，不保证永久免输。托盘菜单提供「打开 WebUI 登录」及「更换连接 / 屏幕授权」。

从源码运行时，安装 Node.js 22 或更高版本、npm、Rust stable（当前依赖至少需要 1.90），以及系统编译工具。Windows 需要 Visual Studio Build Tools 的 C++ 桌面开发和 Windows SDK；macOS 需要 Xcode Command Line Tools；Linux 需要 WebKitGTK/GTK、托盘、XCB/XRandR、D-Bus、PipeWire、Clang 等开发依赖。详见 [Tauri 前置依赖](https://v2.tauri.app/start/prerequisites/)、[xcap Linux 依赖](https://github.com/nashaofu/xcap#linux-system-requirements) 和[客户端说明](../pet-clients/desktop/README.md)。在仓库根目录执行：

```sh
cd pet-clients/desktop
npm ci
npm start
```

Windows 准备好工具链并完成 `npm ci` 后也可以双击 `pet-clients/desktop/start.cmd`；首次启动会编译 Rust 原生程序。

独立客户端支持聊天图文、回复气泡、思考状态、皮肤、待办及图片附件、探索记录和插件按钮。聊天、待办与探索数据由服务器管理，窗口位置和本地资源缓存留在客户端。断网时显示连接状态并自动重连；已经发出但未获确认的操作不会自动重放，避免重复聊天或重复新增待办。客户端离线不停止服务器上的 Bot 定时待办和主动思考；用户提醒等到客户端连接后显示，桌面采集在离线时停止。

### 截图授权与平台限制

截图和持续阅读需同时满足服务器「允许读取桌面」及客户端本次连接的本地授权，实际读取的是客户端电脑主屏；无法确定主屏时会报错。macOS 还需要系统「屏幕录制」权限，拒绝时不会返回截图，权限变更后可能需要重启客户端。Linux 使用 X11 窗口模式，Wayland 会话需要 XWayland；xcap 的截图门户可能每次要求确认，不能保证无人值守连续采样。窗口透明、置顶、位置控制和鼠标穿透也需在目标桌面验证。参见 [xcap 平台支持范围](https://github.com/nashaofu/xcap#implementation-status)。

Windows 的应用条件支持进程名和窗口标题；macOS/Linux 当前按进程名匹配。当前在 Windows 上进行运行验证，macOS/Linux 已提供构建配置但尚未进行实机验证；macOS 最低配置版本为 10.15。

### 构建与分发

在 `pet-clients/desktop` 目录运行对应平台的构建命令：

```sh
npm run dist:win
npm run dist:mac
npm run dist:linux
```

原始安装包位于 `pet-clients/desktop/src-tauri/target/release/bundle/`，构建脚本同时收集到仓库的 `dist/desktop-pet-client/`，分别为 Windows NSIS 安装器、macOS DMG，以及 Linux DEB、RPM、AppImage。默认构建当前工具链的单一架构，请在目标操作系统和架构上构建、验证；macOS 签名与公证需要相应环境。构建使用共用桌宠网页资源，不包含完整浏览器、Bot 配置、密码、聊天数据库或模型依赖。JavaScript 依赖由 `pet-clients/desktop/package-lock.json` 管理，Rust 依赖由 `pet-clients/desktop/src-tauri/Cargo.lock` 管理。

## 插件控制

使用 SDK 2.17.0+ 和带有本功能的 MaiBot 1.3.16+ Host。SDK 尚未发布时使用[本地 SDK 开发模式](internal-chat.md#sdk-开发联调)。内置桌宠本身不依赖新版 SDK。

清单按需声明能力：

```json
"capabilities": [
  "desktop_pet.status", "desktop_pet.show", "desktop_pet.hide",
  "desktop_pet.say", "desktop_pet.set_thinking"
]
```

```python
state = await self.ctx.desktop_pet.status()
if state["ready"]:
    await self.ctx.desktop_pet.show()
    await self.ctx.desktop_pet.set_thinking(True)
    try:
        await self.ctx.desktop_pet.say("待办已经整理好了")
    finally:
        await self.ctx.desktop_pet.set_thinking(False)
    # await self.ctx.desktop_pet.hide()
```

| 接口 | 行为 |
| --- | --- |
| `status()` | 返回 `enabled`、`supported`、`running`、`ready`、`visible`、`error` |
| `show()` / `hide()` | 显示或隐藏已启动的窗口；隐藏不退出进程 |
| `say(text)` | 显示 1～2000 字符纯文本提示气泡，不加入聊天记录、学习或记忆 |
| `set_thinking(active)` | 布尔值，开启或结束思考动画；聊天回复也会控制动画 |

所有控制接口返回当前状态，窗口回执成功后才返回；未就绪、窗口执行失败、通信超时会抛错。`status()` 在关闭或不支持的平台上也可调用。启动中的窗口可能 `running=True` 而 `ready=False`，插件应跳过本次桌面提示或稍后自行查询。`show()` 不会绕过用户的启用开关，隐藏状态下的 `say()` 也不会擅自显示窗口。

插件不能注入 JavaScript、HTML 或原生窗口代码。气泡提示与 `ctx.internal_chat.send(text)` 不同：后者以主人身份真正发起聊天，可能触发麦麦回复并进入聊天历史；前者只在桌面显示提示，不触发模型调用。

## 插件按钮

插件声明的按钮统一出现在桌宠的拼图图标「插件扩展」菜单中。每个插件最多 10 个按钮，按 `order`、插件 ID、按钮 ID 排序；允许不同插件使用相同按钮 ID。

SDK 2.21 提供 `DesktopPetButton`、`DesktopPetButtonState`，在 `MaiBotPlugin.desktop_pet_buttons` 声明按钮，使用 `get_desktop_pet_button_states()` 返回完整状态，使用 `on_desktop_pet_button_click(button_id)` 接收操作。已有 SDK 2.20 的插件也可直接使用字典声明并实现这两个异步方法。

按钮仅接受 ID、文字、内置图标和顺序，不允许注入 HTML 或脚本。Host 在点击前校验按钮归属、可见及禁用状态；同一按钮执行中禁止重复点击。回调限时 8 秒，长时间打开的窗口或后台任务应在回调中启动并自行管理生命周期。执行失败会在桌宠显示错误；插件停用、卸载或 Runner 退出后，按钮随注册表清理，窗口重启会重新同步。

状态会在打开扩展菜单、点击完成和运行时状态变化后刷新。插件还可以声明 `desktop_pet.refresh_buttons` 能力并调用 `ctx.desktop_pet.refresh_buttons()` 请求异步刷新；SDK 2.20 可使用 `ctx.call_capability("desktop_pet.refresh_buttons")`。

### 插件内嵌面板

带此功能的 Host 支持按钮回调返回声明式面板，使用 SDK 2.20+ 即可通过字典接入，不需要插件创建窗口或注入 HTML。面板在桌宠内部显示，复用待办的气泡外框、字体和控件。旧插件回调返回 `None` 时行为不变。

```python
async def on_desktop_pet_button_click(self, button_id):
    return await self.get_desktop_pet_panel(button_id)

async def get_desktop_pet_panel(self, button_id):
    return {
        "title": "我的扩展",
        "text": "当前状态",
        "copy_text": "可以复制的完整结果",
        "actions": [{"id": "refresh", "label": "刷新"}],
    }

async def on_desktop_pet_panel_action(self, button_id, action_id):
    if action_id != "refresh":
        raise ValueError("未知操作")
    return await self.get_desktop_pet_panel(button_id)
```

`image` 可选，填写不含 data URL 前缀的 base64 PNG（最多 100 万字符、400 万像素）。桌宠自动提供图片缩放、滚动查看及复制图片；`copy_text` 非空时提供复制文本。`title` 最长 64 字符，`text` 最长 4000 字符，`copy_text` 最长 16000 字符。最多 10 个 `actions`，每项包含唯一 `id`、`label`，以及可选的 `disabled`、`selected` 布尔值。

操作前重新读取 `get_desktop_pet_panel()`，校验操作是否存在且可用；面板归属于已注册且可见、可用的入口按钮，沿用按钮的互斥与 8 秒超时。两个面板方法必须为异步方法；同步读写和绘图请交给 `asyncio.to_thread`。操作回调返回完整面板更新；面板不自动轮询，可声明刷新操作。插件停用、卸载或入口不可用后自动退出面板，迟到结果不会重新弹出已关闭的面板。

## 桌宠 Hook

桌宠提供观察型 Hook，处理器不能拦截或修改桌宠操作；处理器异步运行，默认超时 5 秒。窗口未曾就绪便退出时不触发 `closed`。

| Hook | 参数 | 触发时机 |
| --- | --- | --- |
| `desktop_pet.ready` | 无 | 窗口就绪 |
| `desktop_pet.closed` | `reason`：`stopped` 或 `unexpected` | 已就绪的窗口退出 |
| `desktop_pet.clicked` | 无 | 单击桌宠本体 |
| `desktop_pet.drag_started` | `gesture_id`、`offset_x`、`offset_y`、`scale` | 超过拖动阈值后首次实际移动 |
| `desktop_pet.drag_ended` | `gesture_id` | 结束或取消拖动，包括失去指针捕获、窗口失焦或隐藏 |
| `desktop_pet.visibility_changed` | `visible`：布尔值 | 显示或隐藏状态改变 |
| `desktop_pet.skin_changed` | `skin_id`：字符串 | 皮肤切换成功 |

插件可在 Hook 中调用已有的桌宠气泡和动画能力；动画被其他操作占用时仍会返回忙碌错误。

拖动开始、结束携带相同的 `gesture_id`，单击不会触发拖动 Hook。`offset_x`、`offset_y` 是桌宠落脚锚点相对抓取光标的物理像素偏移，`scale` 是窗口的实际缩放倍率；扩展可以据此定位自己的桌面特效。观察型处理器并发运行，使用拖动 ID 配对，避免迟到的结束事件取消下一次拖动。

## 图文聊天与聊天流工具

在聊天框粘贴剪贴板图片，可预览、移除，并和文字一次发送；也支持纯图片。每次最多 4 张，每张不超过 8 MiB、2500 万像素。Enter 发送，Shift+Enter 换行，失败保留草稿并显示原因。

在「桌宠」开启 `desktop_pet.chat_tools_enabled`（默认关闭），主人内部聊天的 Agent 即可使用 `list_chat_sessions` 查询真实聊天流，使用 `handoff_to_chat` 向目标 Agent 传达任务并唤醒。支持名称搜索、平台和聊天类型筛选、分页，以及当前聊天图片引用。无需启用独立 Agent。

此开关作用于桌宠和网页共用的 `internal / owner` 会话，调用时重新检查配置；普通群聊不会获得这两个工具。图片发送不受此开关影响。目标遵守适配器策略；回执仅表示任务入队，不代表已对外发送消息。目标 Agent 根据其上下文处理任务，不自动回传完成结果。

## Agent 待办工具

主人共享聊天流可调用 `desktop_pet_todo`，通过 `list`、`add`、`update`、`complete`、`delete` 查看、新增、修改、完成归档及永久删除待办。操作使用查询返回的真实 ID；完成后保留原内容、触发信息和完成时间，点击面板右上角的小勾按钮切换已完成列表。默认查询和常驻上下文只包含未完成事项，`list(include_completed=true)` 可查询历史。旧版本已删除的事项无法恢复。内容最多 200 字。

工具和界面操作同一份待办，保存后刷新桌宠列表，不受 `chat_tools_enabled` 开关影响；普通群聊无权调用。本机模式由窗口进程保存，桌宠须运行且就绪；远端模式由服务器保存，客户端离线时 Bot 仍可查询、修改待办，界面重连后读取最新内容。旧待办自动视为未定时的用户事项。

事项分为 `bot`（Bot 自身待办，到期向主人真实聊天流投递内部任务并唤醒）和 `user`（用户提醒，到期显示桌宠窗口及提醒消息）。`add` 默认 `user`，`update` 可以只修改部分字段。`due_at` 接受 ISO 8601 绝对日期时间，建议带时区；不带时区时按服务器本地时间。`delay_seconds` 表示从保存时起多少秒后触发，两者不能同时填写。省略时间保持原计划，`due_at: null` 取消定时。

桌宠待办面板可以新增、编辑内容、类型和时间，支持绝对时间或多少分钟以后。列表默认定位到底部并展示最后 5 条事项的高度（受屏幕可用高度限制），长内容最多占两行，悬停或键盘聚焦时自动往返滚动阅读。向上滚动列表时面板平滑向上扩展，退出待办模式后恢复默认高度；编辑草稿保留。修改过的事项下方显示橙色细线，悬停可查看修改时间；无需滚动时不显示滚动条，滚动条与编辑按钮之间保留间距。时间以使用界面的本地时区显示。到期事项保留在列表，显示待触发、触发中、已触发或失败；勾选后归档到已完成列表，不再定时或条件触发。“已触发”仅表示提醒已显示或 Bot 任务已入队，不代表任务执行完成。失败详情保留，重新设置未来时间即可重试。Bot 待办需要先建立主人聊天流（在桌宠或网页聊一句）。

计时由主程序每秒检查，隐藏桌宠不停止计时；到期的用户提醒会显示窗口。本机模式下，关闭桌宠、退出主程序或休眠期间不能准时提醒，恢复运行后补触发。远端模式下，客户端离线时 Bot 定时待办继续执行，用户提醒留待客户端连接后显示；服务器停止时两类计时都停止。重启后未确认投递的事项会重新尝试，极端情况下投递成功但回执未保存便退出，可能重复触发；不用于要求严格一次执行的操作。

## 条件待办与常驻上下文

「应用打开」是独立的程序检测模式：填写 `code`、`Code.exe`、`记事本` 等进程名或窗口标题关键字（工具参数 `condition_type="app"`，`condition` 填关键字）。忽略大小写、全半角与末尾 `.exe`，做包含匹配，不做语义猜测。桌宠运行时每 5 秒检查，已运行的应用也算成立，不必等待重新启动；应用的后台进程仍运行也会匹配进程名。不会执行用户填写的字符串，不需要开启截图或持续阅读。查询失败会记录错误，未满足条件不会当作成功。

新增或编辑待办时可选择「条件触发」，填写自然语言条件，例如「当桌面出现 VS Code 时」，并填写需要提醒或执行的事项。Bot 也可通过 `desktop_pet_todo` 的 `condition` 字段创建、修改；空字符串取消条件。工具同时设置时间与条件时，时间表示最早允许触发的时刻，两者必须同时满足。

主人内部私聊每轮构建模型请求时重新读取完整待办快照，保留真实 ID、版本、条件、时间和状态，不依赖历史窗口，桌宠关闭时也能看到已保存内容。编辑或删除在下一轮生效；普通群聊不会加入这些数据。

条件由 Bot 根据当前截图或对话事实判断，满足后调用 `trigger`，携带真实 ID、当前 `revision` 和具体 `evidence`。存储层原子领取，已触发或版本过期的事项会拒绝重复触发。用户事项显示提醒，Bot 事项投递内部任务；触发失败保留失败状态和原因。条件修改后重新等待触发，不是循环提醒。

桌面条件需要允许桌面读取，并开启持续阅读才能随画面变化自动检查；普通聊天或定期思考中，Bot 也可按需调用截图工具判断。没有新画面、没有思考轮次时不会自动知道条件成立。自然语言判断由模型完成，不保证识别每次短暂变化；多帧拼图只以最新帧判断当前桌面状态。

## 人工发起探索

独立记录窗口使用桌宠同款配色、描边和网页标题栏，隐藏系统边框。拖动标题栏移动窗口，双击标题栏或点击最大化按钮切换最大化／还原；四边和四角可拖动缩放，最小尺寸为 680×480。右上角提供最小化、最大化和关闭按钮，最大化保留系统任务栏。

探索面板的「独立窗口 ↗」按钮打开可缩放、可最大化的探索记录窗口。左侧按目标搜索和按状态筛选，右侧查看完整目标、创建／更新时间、资源与经验卡片和带时间戳的执行记录。窗口每 5 秒自动刷新，也可手动刷新；重复点击复用已打开窗口，关闭记录窗口不会终止任务，退出桌宠时一同关闭。

桌宠菜单中的独立「探索」按钮可填写目标（最多 2000 字）并提交一次内部任务。先在桌宠或网页聊一句建立主人会话；投递失败在探索面板显示，草稿保留。「已提交」只表示进入主人 Agent 队列，执行结果在共享聊天中汇报，关闭面板不会取消已提交任务。

探索复用当前聊天 Agent 和已有工具权限，根据能力声明提出假设、实验并总结。中英日提示词 `desktop_pet_exploration` 建议最多验证三个假设，不创建循环唤醒；目前是模型行为指引，没有独立的硬性预算或取消机制，任务进度由模型明确上报。目标未授权的外部发送和修改需先询问主人。

主人聊天独享 `desktop_pet_schema`：`list` 查询已有候选图式，`save` 保存标题、包含适用条件与步骤的工作流和实验证据。记录按真实会话隔离，原子保存到 `data/desktop_pet/exploration_schemas.json`，桌宠关闭后仍可查询。候选图式是模型总结的经验，系统不自动认证其有效性；后续可让麦麦查询并按条件复用。失败与未验证部分由模型在证据和聊天报告中说明。

### 资源与经验

#### 复用反馈闭环

经验检索返回内容版本引用 `experience_ref` 及复用汇总：成功／部分成功／失败次数、最近五次记录、最近一次成功与失败。模型实际复用后调用 `record_experience_reuse`，关联真实任务与经验版本，保存本次条件、实际路径、结果证据、反馈来源，以及成功时验证的完善点、失败时的原因（不明则明确说明）。这些补充参与后续关键词检索与适用性判断，不将成功次数当作可靠性保证。

反馈追加到原任务的 `reuse_records`，保留原经验、任务状态与所有复用历史。每次尝试用唯一 `reuse_key`，相同上报重试不重复计数；同一个标识提交不同内容会报错。经验内容变化后旧引用会被拒绝，旧版本反馈不混入新版本。桌宠与独立窗口在经验卡片下展示复用统计和可展开的逐次记录。保存由模型根据实际结果显式调用，不自动把工具调用成功当作经验复用成功。

普通主人聊天（桌宠与网页共享会话）也会获得按需复用提示，并可直接调用只读工具 `search_exploration_products`，不需要发起探索任务，桌宠关闭后仍可用。模型遇到资料、媒体、工具用法或类似问题时，先提取 1～6 个短关键词检索，按资源／经验筛选，默认每页 5 条、最多 10 条。按关键词命中数、标题匹配及更新时间排序，支持分页；这是关键词检索，不是语义向量检索，无命中可改用短词或同义词。

结果包含完整的精简产物、来源任务 ID、任务状态和更新时间，模型据此检查资源时效、经验适用条件及反馈。失败经验保留用于避错；历史成功不代表当前任务已经执行。检索按真实主人会话隔离，不向群聊或其他用户开放，不改写旧任务的状态或原实验结论，复用反馈独立追加，也不会自动创建探索。旧版未分类长文本和旧候选图式仍通过原有任务／图式工具查询。

新任务以结构化字段保存在同一任务记录中，中英日提示词一致。`desktop_pet_exploration_task` 的 `products` 包含 `resources` 和 `experiences` 两个列表，每次提交是完整快照；更新前用 `get` 查询保留已有成果，`progress` 和 `fail` 也能保存阶段产物。`complete` 必须带两类列表（可为空）及最多 400 字的结果概述。字段不合法会直接报错，不写入、不把任务标记完成。

- **资源**：标题、类型（文本／图片／音频／视频／文件）、摘要、文本正文、来源和资源地址。每任务最多 12 条，摘要最多 240 字，正文最多 1200 字；文本必须有正文，媒体和文件必须有真实地址。HTTP(S) 地址可打开，图片、音频、视频点击“预览媒体”后才加载；本地路径仅展示供复制，不自动下载、执行或上传文件。
- **经验**：标题、适用条件、实际路径、成功／部分成功／失败、实际结果、反馈与反馈来源。每任务最多 8 条，路径最多 6 步且每步最多 160 字，结果与反馈各最多 300 字。反馈来源区分实际观察、用户反馈和尚未验证，失败经验同样保留。

新探索通过任务 `list/get` 检索资源与经验，旧候选图式工具继续保留用于既有记录。结构校验不代表系统已经核实模型提供的来源或结论；经验复用仍需检查实际条件。

## 定期主动思考

在 WebUI「桌宠」开启「定期主动思考」（`desktop_pet.proactive_wake_enabled`，默认关闭），设置「主动思考间隔（分钟）」（`proactive_wake_interval_minutes`，默认 30，范围 1～1440）。每次间隔后向已存在的主人聊天流投递一次内部思考事件，复用正常 Planner、人格、上下文和可用工具，不伪造主人消息。请先在桌宠或网页聊一句以建立主人会话。

提示词鼓励 Bot 根据近期交流、记忆和待办选择一个小方向自主探索，必要时使用工具，有值得分享的发现再自然交流；也可以安静结束，不保证每次发消息。可在提示词管理中编辑「桌宠定期主动思考」（`desktop_pet_proactive_wake`），中、英、日模板同步提供。唤醒会调用模型，聊天流传话等工具仍受原有开关和权限控制。

本机模式在桌宠就绪后开始计时，退出桌宠时取消；远端模式由服务器计时，客户端离线不取消。桌宠配置热更新会重新等待完整间隔，隐藏窗口不影响计时，关闭功能或停止服务器会取消。停止运行期间的周期不累积、不补跑，恢复后只继续当前周期。一次投递失败会记录完整日志和桌宠错误状态，下一周期再尝试。

## 查看桌面与持续阅读

在 WebUI「桌宠」开启「允许读取桌面」（`screen_read_enabled`，默认关闭）。Python 本机模式读取 Windows 主屏；Tauri 2 独立客户端读取客户端电脑的主屏，并遵守客户端授权与系统权限。桌面读取不控制鼠标键盘。截图会发给当前聊天模型，可能包含屏幕上可见的私人信息；模型需要支持图片理解。

- 主人聊天可调用 `desktop_pet_screen`，获得当前截图、捕获时间和尺寸。普通群聊无法使用。
- 单次查看由 Bot 根据聊天需要调用截图工具，界面不提供「看一眼」按钮；用户也可以直接在聊天中提出查看桌面的需求。
- 点击「持续阅读」立即开始采样，按 `screen_sample_interval_seconds`（默认 2 秒，0.5～30 秒）比较画面，按 `screen_read_interval_seconds`（默认 10 秒，2～3600 秒）投递变化时间线。首次在完整投递间隔后交给模型；有效采样间隔不超过投递间隔。绿色指示点和「停止阅读」按钮表示正在运行；点击停止可取消后续投递。

差异检测在 320×180 缩略图上比较亮度，忽略低于 20/255 的单像素变化；明显变化的像素比例达到 `screen_change_threshold`（默认 0.015，即 1.5%）才保留新帧。比较基准为上次保留的帧，小变化可以累积；小面积文字变化也可能被过滤，可降低阈值提高敏感度。无明显变化不重复投递。

每次最多拼接 6 帧，按从左到右、从上到下排列，标注序号及 UTC 捕获时间。保留时间段起点和最新五个变化帧，过早的中间帧会被舍弃；上一批末帧作为下一批起点。拼图每格 960×572 像素（含时间标签），保持原画面比例。持续阅读提示词只鼓励在有意义的变化或帮助机会出现时交流，不要求每次发消息；它是按差异筛选的定期截图，不是视频流。已有配置里的投递间隔（例如 60 秒）会保留，可在 WebUI 调至 10 秒。可编辑中英日提示词 `desktop_pet_screen_read`。截图最长边缩至 1920 像素。Python 本机模式及 Tauri 的 Windows、macOS、纯 X11 截图路径在内存中处理；Linux Wayland 的 GNOME/门户路径可能生成系统临时 PNG，xcap 读取后执行清理。图片会进入主人会话的模型上下文，持续阅读在上下文中仅保留最近两张时间拼图供对照，避免长期堆积。

截图或投递失败会停止持续阅读并显示原因，修复后手动重新开始。关闭读取权限、关闭桌宠或主程序退出会停止；重启后不会自动恢复。隐藏桌宠窗口不停止阅读。已有模型任务不因点击停止而撤回，但不会继续采集和投递新的画面。

## 皮肤包

「桌宠 → 当前皮肤」可选择皮肤、试播预览、导入 ZIP；保存配置后热切换。`desktop_pet.skin_id` 默认 `builtin`。预览不会打断桌宠；切换先校验并解码所有资源，失败保留原形象并报告错误。原有聊天、待办和默认 SVG 形象仍可使用。

皮肤从程序自带的 `src/desktop_pet/presets/<目录>/skin.json`、实例的 `data/desktop_pet/skins/<目录>/skin.json` 和 `plugins/<插件目录>/skins/<目录>/skin.json` 发现。程序预设随源码和安装包分发，各 Bot 实例均可选择，无需复制到实例目录。插件可以携带静态皮肤，是否执行插件代码与皮肤选择独立；插件不能自动切换用户的皮肤。以上来源之间 ID 重复会明确报错，导入不覆盖已有皮肤。

「橙芽与小鱼」（`orange-sprout`）是程序预设，使用橙芽角色和小鱼互动动画。待机为两秒一轮的轻微呼吸和晃动，点击时从待机姿势抱起小鱼，结束时放下双手、收回小鱼并恢复待机姿势；播放器用短暂淡化衔接不同动作，连续点击会从头重播完整动作。小鱼绕飞保留为可单独试播的动作，也可试播递鱼、欢呼和舞蹈。更新后重新打开桌宠窗口以加载新版播放器和皮肤；首次选择预设可在「桌宠 → 当前皮肤」中试播并保存配置。更新不会自动选择新皮肤。预设通过图片帧播放，使用时不需要启动 EmoteLab，也无需安装模型编辑器。

包内只使用 JSON、静态 PNG/WebP，不执行脚本。ZIP 可以直接包含 skin.json 或套一层目录。每包最多 32 MiB、512 个文件，单张图最多 1600 万像素，单动画最多 240 帧、30 秒。

最小 skin.json：

```json
{
  "schema_version": 1,
  "id": "author.pet",
  "name": "我的桌宠",
  "version": "1.0.0",
  "canvas": {
    "width": 160, "height": 160,
    "anchor": [80, 160], "bubble_anchor": [80, 0],
    "hitbox": [10, 10, 140, 150]
  },
  "animations": {
    "idle": {"file": "idle.png", "frames": 1, "fps": 1, "motion": "bob"}
  },
  "states": {"idle": "idle", "thinking": "idle", "talking": "idle", "dragging": "idle"},
  "events": {"greeting": "idle"}
}
```

- 画布宽度 64～220、高度 64～240（CSS 像素）。落脚锚点在底部 4 像素内，横向需使画布位于窗口内；切换保持屏幕落脚点不动。
- `hitbox` 为画布内 `[x, y, width, height]`；`bubble_anchor` 控制气泡的垂直位置，气泡水平居中，以免超出窗口。
- 精灵图：填写 `file`、`frame_width`、`frame_height`、`frames`、`fps`，从左到右、从上到下取帧。
- 图片帧：改用 `sequence` 数组，每帧为 `{"file":"atlas.png","rect":[0,0,256,256],"duration_ms":125}`。省略 rect 时使用整张图片。这样也能在同一图集中引用不连续帧。
- `motion` 支持 `none`、`bob`、`sway`、`hop`、`shake`、`pulse`，由主程序提供有限的位移/缩放效果。
- 四种持续状态必须全部显式映射，允许复用动画。可选事件为 `click`、`happy`、`error`、`greeting`。不存在的资源或动作会报错。

## 动画 SDK（2.18.0）

需要包含本功能的 Host 和 SDK 2.18.0；本地源码联调仍使用 `MAIBOT_PLUGIN_SDK_PATH`。新接口均需在插件清单声明同名 `desktop_pet.<接口>` 能力：

| 方法 | 返回/行为 |
| --- | --- |
| `get_skin()` | 当前皮肤 id、name、version |
| `list_animations()` | skin、animations（name、duration_ms）、events |
| `play_animation(name, repeat=1)` | 开始有限播放，返回 playback_id、status、animation、skin_id |
| `play_event(event)` | 根据皮肤的标准事件映射播放一次 |
| `stop_animation(playback_id)` | 只能停止本插件的播放 |
| `animation_status(playback_id)` | 查询 playing/finished/interrupted/stopped 和 reason |

```python
result = await self.ctx.desktop_pet.play_event("greeting")
status = await self.ctx.desktop_pet.animation_status(result["playback_id"])
```

回执表示窗口确认开始播放，不代表播完。一次调用最多 30 秒；同一插件的新动作替换旧动作，其他插件不能抢占或访问播放记录。只保留最近 128 条已结束记录，窗口重启后记录清空。

持续状态优先级：拖动 > 说话 > 思考 > 待机。临时动作可以覆盖思考和待机；说话、拖动会打断它，结束后恢复实时状态。忙碌不排队；隐藏窗口返回 `hidden`，不擅自显示。错误文本带 `busy`、`animation_not_found`、`event_not_found`、`invalid_duration`、`not_owner`、`playback_not_found` 等代码。

第一版不包含 Live2D、皮肤脚本、音效或无限循环插件动作。


## 界面控件复用

桌宠页面的共用控件样式集中在 `src/desktop_pet/web/index.html` 的“共用控件”段。新增功能优先复用：

- `pet-input`：文本、数字、日期输入及 textarea；`pet-select`：统一描边和箭头的选择框。
- `pet-button`：普通按钮；叠加 `pet-button--primary` 表示主要操作，叠加 `pet-icon-button` 表示圆形图标按钮。
- `controlIcon(name)` 与 `setControlIcon(button, name, label)`：统一线条图标和操作提示；图标按钮必须有 `title` 和 `aria-label`。

沿用 `--ink`、`--paper-2`、`--cream`、`--leaf`、`--orange` 主题变量，保留焦点、按压和禁用反馈。信息优先用图标与简短标签表达，错误原因仍显示完整文字，不只依赖颜色区分状态。

菜单按钮统一放入 `#pet-menu`，使用 `.orb` 和 `data-hit="23"`，由容器居中并自动换行；不要为新增按钮写固定 left 坐标。气泡避让使用菜单实际高度，裁剪区域覆盖按钮静止和动画位置。


## 探索任务进度与结果

探索面板提供任务列表，可筛选进行中或已结束的任务；点击任务查看目标、状态、开始时间、带时间戳的执行记录和结果。新任务结果按资源和经验分别展示，任务列表显示两类数量。旧 Markdown 结果标记为“历史结果 · 尚未分类”，不猜测或自动拆分。文件路径可以直接选中复制。界面沿用桌宠控件样式，打开时每 3 秒刷新，刷新保留选中任务与阅读位置。

每次提交生成独立任务 ID 并保存到 `data/desktop_pet/exploration_tasks.json`，按主人真实会话隔离。`desktop_pet_exploration_task` 提供 list/get/progress/complete/fail：Bot 开始和完成关键步骤时明确上报，完成必须提交实际结果，失败记录原因和部分成果；聊天回复或普通工具调用成功不会自动标记完成，不显示推测的百分比。中英日探索提示词已同步引导上报。

已结束任务拒绝迟到或重复更新。主程序重启后，旧进程中未确认结束的任务显示「已中断」；历史结果仍可查看。旧版本仅在聊天里汇报的探索没有独立任务记录，不会凭聊天内容补造进度。候选图式继续使用原有存储，结果可引用其真实 ID。


## 思考阶段字幕

在桌宠设置开启「显示桌宠手中事」（默认关闭）后，主人内部聊天的阶段变化会独立调用 `fast_model`，根据阶段名称、动作说明和轮次生成简短摘要，显示在桌宠底部。使用随包附带的 Ark Pixel 中文像素字体，文字依次轻跳，系统减少动态效果时关闭动画。

摘要限时 2 秒，不等待主推理、不重试；防抖结束进入消息整理时立即显示「麦麦思考中......」，当前阶段摘要生成后替换；阶段切换恢复占位并取消旧请求，超时或空摘要保留占位，迟到结果不会显示。空闲、等待、错误或运行停止时收起。只读取运行阶段元数据，不读取模型思维正文；提示词为 `desktop_pet_thinking_caption`，中英日版本同步提供。


## 备忘图片附件

在待办输入区按 Ctrl+V 可粘贴剪贴板图片，每项最多 4 张、每张最多 8 MiB。仅粘贴图片也可以保存为「图片备忘」。草稿缩略图点击移除，保存后的缩略图点击展开／收起，编辑可增删附件，完成归档后仍可查看。支持 PNG、JPEG、WebP、GIF，图片尺寸上限为 2000 万像素。

附件保存在待办数据目录的 `todo_images/`，待办 JSON 仅记录图片标识；重启后保留，常驻文字上下文不嵌入图片二进制。

「显示桌宠手中事」仅在 `fast_model.model_list` 已单独分配有效模型时允许开启，不借用其他模型。关闭选项会取消当前摘要并清空字幕。
