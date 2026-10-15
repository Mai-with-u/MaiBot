# Hello World 示例插件

除了命令、工具、Action、事件处理器和首页卡片，本插件演示声明式 WebUI 自定义页面。无需编译插件前端，页面布局与样式由宿主统一渲染。

## 自定义页面

- 顶部 **Hello World** 工作区：统计卡片、最近问候、柱状图、内部 tabs 和历史表格，支持确认后清空演示记录。
- 麦麦侧边栏 **插件扩展 → Hello World 问候体验**：文本输入、选择框、数字输入和开关，点击按钮后调用插件 API 并自动刷新预览。
- **Hello World 详情与循环示例**：点击表格行或按 Enter / 空格查看完整问候，在详情弹窗确认删除该记录；展开动态卡片区可查看 `repeat` 生成的内容，空记录时由 `when` 显示提示。

启用并加载插件后即可看到入口。源码试用请按 [插件 WebUI 接入说明](../../docs/plugin-webui.md) 启用本地构建的 dashboard；后端首次接入扩展功能需要重启主程序。修改 `webui.json` 后重载插件即可。

页面地址：

- `/extensions/maibot-team.hello-world-plugin/overview`
- `/extensions/maibot-team.hello-world-plugin/greeting`
- `/extensions/maibot-team.hello-world-plugin/details`

首次使用可先在侧边页面填写称呼并生成问候，再回到顶部概览点击刷新。生成问候只更新插件内存，不发送聊天消息，不修改配置文件。最多保留最近 20 条记录，重载或重启插件后清空。

## 声明与 API

`webui.json` 通过查询和操作别名绑定本插件的四个静态 API：

| API | 用途 |
| --- | --- |
| `webui_summary` | 只读摘要、最近记录和图表数据 |
| `webui_generate_greeting` | 按表单生成预览并记录结果 |
| `webui_reset_greetings` | 清空演示记录，页面声明要求宿主确认 |
| `webui_remove_greeting` | 接收选中行的 `sequence`，确认后删除单条页面记录 |

详情页是完整的声明式示例：`table.selection` 保存行上下文，`table.detail` 打开 `dialog`；`scope: "selection"` 读取行字段；`actions.arguments` 把序号映射到 API 参数；`repeat` 为每条记录提供 `scope: "item"`；`when` 配合 `empty` / `not_empty` 隐藏空区块，`collapsible` 控制展开。删除不会减少累计生成次数，只减少当前保留记录数。后端首次升级这些组件后需重启宿主，随后修改 JSON 只需重载插件。

这些 API 使用 `@API(..., version="1")`，没有对其他插件设置 `public=True`，也没有增加 manifest 能力权限。页面不包含自定义 JS、HTML 或 CSS。
