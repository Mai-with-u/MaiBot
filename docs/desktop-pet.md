# 内置桌宠与插件 SDK

桌宠由主程序管理，不再需要安装 `sengokucola.desktop-pet` 插件。目前支持 Windows 桌面环境，需要 WebView2 Runtime。Windows 的 Python 依赖已包含 pywebview，源码环境更新依赖可运行 `uv sync --inexact`（保留插件额外依赖）。

在 WebUI 主程序配置的「桌宠」启用 `desktop_pet.enabled`，默认关闭。配置支持热重载，关闭即退出桌宠，主程序退出也会回收窗口。图形界面仍使用独立子进程，以免阻塞聊天和网页事件循环；界面、待办和启动管理代码都已随主程序分发。

旧桌宠插件会被明确阻止加载并显示内置替代提示，避免同时启动两个窗口；插件源码保留。继续读写 `data/plugins/sengokucola.desktop-pet/todos.json` 和 `window.json`，已有待办和窗口位置无需复制。临时 WebView 数据改用 `temp/desktop_pet`。

桌宠聊天继续使用 `internal / owner`，昵称由网页聊天室统一设置。麦麦给主人的回复在桌宠和网页同步展示；旧 `webui` 私聊不迁移。桌宠提交的消息保留界面来源标记，不具备受保护命令权限。

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

工具直接操作窗口进程里的同一份待办，保存后刷新桌宠列表，不受 `chat_tools_enabled` 开关影响；普通群聊无权调用。桌宠须处于运行且就绪状态，否则工具明确返回错误。现有待办文件和数据格式不变。

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
