# 内置桌宠与插件 SDK

桌宠由主程序管理，不再需要安装 `sengokucola.desktop-pet` 插件。目前支持 Windows 桌面环境，需要 WebView2 Runtime。Windows 的 Python 依赖已包含 pywebview，源码环境更新依赖可运行 `uv sync --inexact`（保留插件额外依赖）。

在 WebUI 主程序配置的「桌宠」启用 `desktop_pet.enabled`，默认关闭。配置支持热重载，关闭即退出桌宠，主程序退出也会回收窗口。图形界面仍使用独立子进程，以免阻塞聊天和网页事件循环；界面、待办和启动管理代码都已随主程序分发。

旧桌宠插件会被明确阻止加载并显示内置替代提示，避免同时启动两个窗口；插件源码保留。继续读写 `data/plugins/sengokucola.desktop-pet/todos.json` 和 `window.json`，已有待办和窗口位置无需复制。临时 WebView 数据改用 `temp/desktop_pet`。

桌宠聊天继续使用 `internal / owner`，昵称由网页聊天室统一设置。麦麦给主人的回复在桌宠和网页同步展示；旧 `webui` 私聊不迁移。桌宠提交的消息保留界面来源标记，不具备受保护命令权限。主人聊天流有实际发言时会主动显示桌宠；聊天面板未展开时弹出消息气泡，不受菜单、待办或插件面板状态限制，点击气泡可查看聊天记录。

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

主人共享聊天流可调用 `desktop_pet_todo`，通过 `list`、`add`、`update`、`delete` 查看、新增、修改、删除待办。修改或删除使用查询返回的真实 ID；完成待办仍按原设计删除，不保留完成列表。内容最多 200 字。

工具直接操作窗口进程里的同一份待办，保存后刷新桌宠列表，不受 `chat_tools_enabled` 开关影响；普通群聊无权调用。桌宠须处于运行且就绪状态，否则工具明确返回错误。旧待办自动视为未定时的用户事项。

事项分为 `bot`（Bot 自身待办，到期向主人真实聊天流投递内部任务并唤醒）和 `user`（用户提醒，到期显示桌宠窗口及提醒消息）。`add` 默认 `user`，`update` 可以只修改部分字段。`due_at` 接受 ISO 8601 绝对日期时间，建议带时区；不带时区时按服务器本地时间。`delay_seconds` 表示从保存时起多少秒后触发，两者不能同时填写。省略时间保持原计划，`due_at: null` 取消定时。

桌宠待办面板可以新增、编辑内容、类型和时间，支持绝对时间或多少分钟以后。列表默认定位到底部并展示最后 5 条事项的高度（受屏幕可用高度限制），长内容最多占两行，悬停或键盘聚焦时自动往返滚动阅读。向上滚动列表时面板平滑向上扩展，退出待办模式后恢复默认高度；编辑草稿保留。修改过的事项下方显示橙色细线，悬停可查看修改时间；无需滚动时不显示滚动条，滚动条与编辑按钮之间保留间距。时间以使用界面的本地时区显示。到期事项保留在列表，显示待触发、触发中、已触发或失败；勾选后删除。“已触发”仅表示提醒已显示或 Bot 任务已入队，不代表任务执行完成。失败详情保留，重新设置未来时间即可重试。Bot 待办需要先建立主人聊天流（在桌宠或网页聊一句）。

计时由主程序在桌宠运行时每秒检查，隐藏桌宠不停止计时；到期的用户提醒会显示窗口。关闭桌宠、退出主程序或休眠期间不能准时提醒，恢复运行后补触发。重启后未确认投递的事项会重新尝试，极端情况下投递成功但回执未保存便退出，可能重复触发；不用于要求严格一次执行的操作。

## 条件待办与常驻上下文

「应用打开」是独立的程序检测模式：填写 `code`、`Code.exe`、`记事本` 等进程名或窗口标题关键字（工具参数 `condition_type="app"`，`condition` 填关键字）。忽略大小写、全半角与末尾 `.exe`，做包含匹配，不做语义猜测。桌宠运行时每 5 秒检查，已运行的应用也算成立，不必等待重新启动；应用的后台进程仍运行也会匹配进程名。不会执行用户填写的字符串，不需要开启截图或持续阅读。查询失败会记录错误，未满足条件不会当作成功。

新增或编辑待办时可选择「条件触发」，填写自然语言条件，例如「当桌面出现 VS Code 时」，并填写需要提醒或执行的事项。Bot 也可通过 `desktop_pet_todo` 的 `condition` 字段创建、修改；空字符串取消条件。工具同时设置时间与条件时，时间表示最早允许触发的时刻，两者必须同时满足。

主人内部私聊每轮构建模型请求时重新读取完整待办快照，保留真实 ID、版本、条件、时间和状态，不依赖历史窗口，桌宠关闭时也能看到已保存内容。编辑或删除在下一轮生效；普通群聊不会加入这些数据。

条件由 Bot 根据当前截图或对话事实判断，满足后调用 `trigger`，携带真实 ID、当前 `revision` 和具体 `evidence`。存储层原子领取，已触发或版本过期的事项会拒绝重复触发。用户事项显示提醒，Bot 事项投递内部任务；触发失败保留失败状态和原因。条件修改后重新等待触发，不是循环提醒。

桌面条件需要允许桌面读取，并开启持续阅读才能随画面变化自动检查；普通聊天或定期思考中，Bot 也可按需调用截图工具判断。没有新画面、没有思考轮次时不会自动知道条件成立。自然语言判断由模型完成，不保证识别每次短暂变化；多帧拼图只以最新帧判断当前桌面状态。

## 人工发起探索

桌宠菜单中的独立「探索」按钮可填写目标（最多 2000 字）并提交一次内部任务。先在桌宠或网页聊一句建立主人会话；投递失败在探索面板显示，草稿保留。「已提交」只表示进入主人 Agent 队列，执行结果在共享聊天中汇报，关闭面板不会取消已提交任务。

探索复用当前聊天 Agent 和已有工具权限，根据能力声明提出假设、实验并总结。中英日提示词 `desktop_pet_exploration` 建议最多验证三个假设，不创建循环唤醒；目前是模型行为指引，没有独立的硬性预算、任务进度或取消机制。目标未授权的外部发送和修改需先询问主人。

主人聊天独享 `desktop_pet_schema`：`list` 查询已有候选图式，`save` 保存标题、包含适用条件与步骤的工作流和实验证据。记录按真实会话隔离，原子保存到 `data/desktop_pet/exploration_schemas.json`，桌宠关闭后仍可查询。候选图式是模型总结的经验，系统不自动认证其有效性；后续可让麦麦查询并按条件复用。失败与未验证部分由模型在证据和聊天报告中说明。

## 定期主动思考

在 WebUI「桌宠」开启「定期主动思考」（`desktop_pet.proactive_wake_enabled`，默认关闭），设置「主动思考间隔（分钟）」（`proactive_wake_interval_minutes`，默认 30，范围 1～1440）。每次间隔后向已存在的主人聊天流投递一次内部思考事件，复用正常 Planner、人格、上下文和可用工具，不伪造主人消息。请先在桌宠或网页聊一句以建立主人会话。

提示词鼓励 Bot 根据近期交流、记忆和待办选择一个小方向自主探索，必要时使用工具，有值得分享的发现再自然交流；也可以安静结束，不保证每次发消息。可在提示词管理中编辑「桌宠定期主动思考」（`desktop_pet_proactive_wake`），中、英、日模板同步提供。唤醒会调用模型，聊天流传话等工具仍受原有开关和权限控制。

桌宠就绪或桌宠配置热更新后重新等待完整间隔；隐藏窗口仍计时，关闭功能或退出桌宠会取消计时。离线周期不累积、不补跑，恢复后只继续当前周期。一次投递失败会记录完整日志和桌宠错误状态，下一周期再尝试。

## 查看桌面与持续阅读

在 WebUI「桌宠」开启「允许读取桌面」（`screen_read_enabled`，默认关闭）。这项能力读取运行桌宠的 Windows 本机主屏，和 Windows Sandbox 电脑工具分开，不控制鼠标键盘。截图会发给当前聊天模型，可能包含屏幕上可见的私人信息；模型需要支持图片理解。

- 主人聊天可调用 `desktop_pet_screen`，获得当前截图、捕获时间和尺寸。普通群聊无法使用。
- 单次查看由 Bot 根据聊天需要调用截图工具，界面不提供「看一眼」按钮；用户也可以直接在聊天中提出查看桌面的需求。
- 点击「持续阅读」立即开始采样，按 `screen_sample_interval_seconds`（默认 2 秒，0.5～30 秒）比较画面，按 `screen_read_interval_seconds`（默认 10 秒，2～3600 秒）投递变化时间线。首次在完整投递间隔后交给模型；有效采样间隔不超过投递间隔。绿色指示点和「停止阅读」按钮表示正在运行；点击停止可取消后续投递。

差异检测在 320×180 缩略图上比较亮度，忽略低于 20/255 的单像素变化；明显变化的像素比例达到 `screen_change_threshold`（默认 0.015，即 1.5%）才保留新帧。比较基准为上次保留的帧，小变化可以累积；小面积文字变化也可能被过滤，可降低阈值提高敏感度。无明显变化不重复投递。

每次最多拼接 6 帧，按从左到右、从上到下排列，标注序号及 UTC 捕获时间。保留时间段起点和最新五个变化帧，过早的中间帧会被舍弃；上一批末帧作为下一批起点。拼图每格 960×572 像素（含时间标签），保持原画面比例。持续阅读提示词只鼓励在有意义的变化或帮助机会出现时交流，不要求每次发消息；它是按差异筛选的定期截图，不是视频流。已有配置里的投递间隔（例如 60 秒）会保留，可在 WebUI 调至 10 秒。可编辑中英日提示词 `desktop_pet_screen_read`。截图最长边缩至 1920 像素，捕获模块只在内存中处理，不另存截图文件；图片会进入主人会话的模型上下文，持续阅读在上下文中仅保留最近两张时间拼图供对照，避免长期堆积。

截图或投递失败会停止持续阅读并显示原因，修复后手动重新开始。关闭读取权限、关闭桌宠或主程序退出会停止；重启后不会自动恢复。隐藏桌宠窗口不停止阅读。已有模型任务不因点击停止而撤回，但不会继续采集和投递新的画面。

## 皮肤包

「桌宠 → 当前皮肤」可选择皮肤、试播预览、导入 ZIP；保存配置后热切换。`desktop_pet.skin_id` 默认 `builtin`。预览不会打断桌宠；切换先校验并解码所有资源，失败保留原形象并报告错误。原有聊天、待办和默认 SVG 形象仍可使用。

皮肤从 `data/desktop_pet/skins/<目录>/skin.json` 和 `plugins/<插件目录>/skins/<目录>/skin.json` 发现。插件可以携带静态皮肤，是否执行插件代码与皮肤选择独立；插件不能自动切换用户的皮肤。ID 重复会明确报错，导入不覆盖已有皮肤。

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
