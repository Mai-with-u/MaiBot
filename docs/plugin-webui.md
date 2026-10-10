# 插件声明式 WebUI 扩展（协议版本 1）

插件在自身目录新增 `webui.json`，重载插件后生效。不需要安装 Node、打包前端或修改主程序。Runner 在工作线程读取声明，Host 校验并注册；卸载插件时页面和入口同步下线，浏览器导航最多 30 秒更新，也可以手动刷新。

在本仓库试用时，先构建 `dashboard`，启动主程序前设置环境变量 `MAIBOT_WEBUI_USE_LOCAL_DASHBOARD=1`，让 WebUI 使用 `dashboard/dist`；默认安装的 `maibot-dashboard` 包不会因为源码修改自动更新。后端路由首次接入需要重启主程序，之后修改插件声明只需重载插件。前端开发服务固定使用 7999 端口。

## 导航和权限

- `placement: "sidebar"`：进入麦麦工作区的“插件扩展”分组。
- `placement: "workspace"`：进入插件自己的顶部工作区；该工作区名称由 `workspace_title` 指定，侧边栏展示同一插件所有 workspace 页面，第一个是默认页面。
- 页面路径由宿主生成：`/extensions/{plugin_id}/{page_id}`。插件不能覆盖内置入口或提供任意路由。
- 内置图标只接受 `puzzle`、`chart`、`settings`、`database`、`list`。
- 顶部直接显示一个插件工作区，其他工作区放在“更多”；当前插件工作区保持可见。
- 用户可以在“插件管理”页面最下方的“管理插件页面”区域调整插件入口顺序或隐藏整个插件的入口。偏好保存在当前浏览器，只控制展示，不是后端授权。

页面只调用自身 `queries` / `actions` 中声明的 API。API 必须属于当前插件、以 SDK `@API` 静态注册、版本精确匹配、处于启用状态。`public=True` 不会自动开放给 WebUI，也不要求将页面 API 公开给其他插件。动态 API、跨插件调用、任意请求地址、HTML、JS、CSS、事件表达式均不支持。

这套协议限制 WebUI 扩展通道，不是 Python 插件进程的文件或网络沙箱。插件仍需对自己的业务行为负责；`queries` 应保持只读，宿主无法通过声明判断 Python 方法是否有副作用。

## 示例：顶部统计页和侧边管理页

下面是完整 `webui.json`。`summary` API 返回 `{"count": 123}`，`save_limit` API 接收 `limit: int`。

```json
{
  "schema_version": 1,
  "workspace_title": "消息统计",
  "pages": [
    {
      "id": "overview",
      "title": "统计概览",
      "placement": "workspace",
      "icon": "chart",
      "queries": { "summary": { "api": "summary", "version": "1" } },
      "content": [
        { "type": "stat", "label": "今日消息", "value": { "source": "summary", "field": "count" } }
      ]
    },
    {
      "id": "settings",
      "title": "统计设置",
      "placement": "sidebar",
      "actions": {
        "save": {
          "api": "save_limit",
          "version": "1",
          "parameters": {
            "limit": { "type": "integer", "required": true, "minimum": 1, "maximum": 1000 }
          },
          "confirmation": "确认修改统计上限？"
        }
      },
      "content": [
        {
          "type": "card",
          "label": "统计范围",
          "children": [
            { "type": "input", "name": "limit", "label": "上限", "value": 100 },
            { "type": "button", "label": "保存", "action": "save" }
          ]
        }
      ]
    }
  ]
}
```

在现有 `MaiBotPlugin` 子类上添加 API 方法即可（仍需实现 SDK 必需生命周期方法）：

```python
from maibot_sdk.components import API

# 以下方法放在你的插件类内部。
@API("summary", version="1")
async def summary(self):
    return {"count": self.today_count}

@API("save_limit", version="1")
async def save_limit(self, limit: int):
    self.limit = limit
    return {"saved": True}
```

## 组件

所有颜色、间距、字体、暗色模式和 dashboard 风格由宿主控制。组件没有 `className`、`style` 或 HTML 插槽。

| 类型 | 属性 | 说明 |
| --- | --- | --- |
| `stack` | `label`、`children` | 纵向排列 |
| `grid` | `label`、`columns`、`children` | 1–4 列，移动端自动单列 |
| `card` | `label`、`children` | 宿主卡片 |
| `tabs` | `children` | 每个子节点必须有 `label` |
| `text` | `label`、`value` | 纯文本，HTML 不执行 |
| `stat` | `label`、`value` | 统计卡片 |
| `table` | `label`、`value`、`columns`、`selection`、`detail` | 绑定对象数组，每页 50 行；点击或 Enter / 空格选择一行，`detail` 打开具名弹窗 |
| `dialog` | `name`、`label`、`children` | 在 `content` 顶层声明详情弹窗；`name` 是表格 `detail` 的目标，支持 Escape / 关闭按钮 |
| `collapsible` | `label`、`children`、`default_open` | 可展开区块，默认收起 |
| `repeat` | `name`、`value`、`children`、`max_items` | 为数组每项重复子组件；`name` 定义循环项上下文，默认最多 50 项，可设 1–100 |
| `gallery` | `label`、`value`、`columns` | 最多 24 个图片对象；自动布局或 1–4 列；只接受受限的 JPEG / PNG / WebP data URL 缩略图 |
| `pagination` | `label`、`name`、`value` | 绑定 `{page,pages,total}`；上一页 / 下一页修改 `name` 对应参数，搭配页面 `auto_refresh: true` 使用 |
| `chart` | `label`、`value`、`chart_type`、`x`、`y` | `line` 或 `bar`；绑定对象数组，x 为字符串或数字，y 为数字，最多 2000 行 |
| `input` | `name`、`label`、`value` | 数字默认值对应数字输入框，其余为文本 |
| `date` | `name`、`label`、`value` | 字符串日期输入 |
| `select` | `name`、`label`、`value`、`options` | options 为 `[{"label":"一周","value":"week"}]`，value 必须为非空字符串 |
| `switch` | `name`、`label`、`value` | value 必须为布尔值 |
| `button` | `label`、`action`、`variant` | variant 为 `primary`、`danger` 或 `muted`；danger 操作必须声明 confirmation |

`value` 可为标量，展示组件也支持 `{"source":"查询别名","field":"totals.count"}`；空 field 表示查询的完整返回值，表格和图表必须使用数据引用。不支持计算表达式、动态脚本和任意链接。

输入字段的 `name` 在页面中必须唯一。API 参数按绑定中的 `parameters` 从当前表单字段取值；可选且未填写（null）的字段不发送。参数类型是 `string`、`integer`、`number`、`boolean`，可指定 `required`、`max_length`（最多 4000）、`minimum`、`maximum`、`choices`。参数不进行隐式类型转换，未知参数被拒绝；参数名不能使用内部双下划线名称。

页面打开和刷新时依次执行查询；操作成功后再次刷新查询，更新数据。查询失败、API 下线、结果不符合组件要求时显示错误，不使用伪造数据。写操作不会自动重试。需要确认的操作使用宿主确认弹窗，网关也要求确认标记；确认只是防误触机制，不是独立的授权或业务校验。

## 行详情、动态卡片与条件显示

数据引用增加可选 `scope`：默认 `query` 读取查询结果；`selection` 读取表格选中的行；`item` 读取所在 `repeat` 的当前项。`source` 分别填写查询别名、表格 `selection` 名、循环 `name`。循环项只能在该循环的子树使用；不同表格的选择名和弹窗名必须分别唯一。

例如 `summary` 返回 `{"rows":[{"id":1,"title":"记录 A","description":"完整详情"}]}`，可声明以下 `content`：

```json
[
  {
    "type": "table", "value": {"source":"summary","field":"rows"},
    "columns": [{"field":"title","label":"标题"}],
    "selection": "record", "detail": "recordDetails"
  },
  {
    "type": "dialog", "name": "recordDetails", "label": "记录详情",
    "children": [
      {"type":"text","value":{"scope":"selection","source":"record","field":"description"}},
      {"type":"button","label":"删除记录","action":"remove","variant":"danger"}
    ]
  },
  {
    "type": "grid", "columns": 3,
    "when": {"reference":{"source":"summary","field":"rows"},"operator":"not_empty"},
    "children": [{
      "type":"repeat", "name":"entry", "value":{"source":"summary","field":"rows"}, "max_items":20,
      "children":[{"type":"card","children":[
        {"type":"text","value":{"scope":"item","source":"entry","field":"title"}}
      ]}]
    }]
  }
]
```

对应操作绑定：

```json
"actions": {
  "remove": {
    "api": "remove_record",
    "parameters": {"id":{"type":"integer","required":true,"minimum":1}},
    "arguments": {"id":{"scope":"selection","source":"record","field":"id"}},
    "confirmation": "确认删除选中的记录？"
  }
}
```

`arguments` 仅适用于操作，逐字段覆盖同名表单参数，只能映射已声明的标量参数，不会把整行对象传给 API。确认弹窗出现时锁定参数快照。插件 API 仍应核验记录存在性及业务权限。查询刷新成功后清除选中行并关闭详情，避免使用过期记录；没有选择时，选择引用为 null，必填操作参数会报错。

所有组件支持可选 `when`。操作符为 `truthy`、`empty`、`not_empty`、`equals`；`equals` 搭配标量 `expected`。null、空字符串、空数组和空对象视为空，数字 0 不属于空数据。首次查询完成前，有条件的区块不显示。条件只控制显示，不替代权限校验。

`repeat` 保留数组顺序，仅展示前 `max_items` 项。支持嵌套循环，但展开后的整个页面最多 1000 个节点；超过时显示数据错误，不继续展开。循环内可使用展示容器和操作按钮，不能声明输入、详情弹窗或行选择，避免复制表单和交互状态。弹窗放在顶层，可防止详情被折叠区块或未激活的 tab 隐藏。

完整示例见 [Hello World 声明](../plugins/hello_world_plugin/webui.json) 的 `details` 页及 [插件 API](../plugins/hello_world_plugin/plugin.py)；只操作内存中的问候预览。

## 限制与生命周期

单张预览使用 `image` 节点，`value` 指向一条受大小限制的JPEG/PNG/WebP data URL，`label` 用作图片替代文本。它只显示图片，不带图库排列控件；`gallery` 继续用于图片列表。

`upload` 可声明 `image_max_edge`（1–8192）。启用后网页在上传前检查整幅图片；超过边长限制时等比例缩小并转JPEG，超过20MiB但边长合格时保持尺寸转JPEG压缩。透明背景补白，不裁剪、不修改本地原文件，上传副本必须不超过20MiB。未声明时保留原上传行为。角色识别插件设置为4000。

- 文件最多 128 KiB，Host 注册载荷最多 512 KiB；不接受越界路径或符号链接文件。
- 每插件最多 20 个页面，每页最多 200 个组件、8 层嵌套、10 个查询和 20 个操作。
- 每个绑定最多 30 个标量参数；每插件最多两个进行中的 WebUI 请求。
- API 调用超时为 10 秒，响应最多 512 KiB、12 层嵌套和 20000 个元素。
- 超时不代表插件写操作没有生效；核实结果后再尝试，插件可在自己的 API 中实现业务幂等。
- 无效声明或引用未注册 API 会使插件本次注册失败，并在日志中说明原因。现有无 `webui.json` 的插件不受影响。

修改声明后重载插件。`queries` 和 `actions` 内的 API 名是本插件 API 的短名，版本默认 `"1"`，字段、引用和类型错误会直接拒绝。协议 JSON Schema 可由 `WebUIExtension.model_json_schema()` 生成。


## 通用文件上传与后台任务轮询（宿主1.3.6）

声明兼容现有 schema_version=1。使用上传的扩展增加 `required_capabilities: ["file_upload"]`，宿主注册表同时返回 capabilities；旧宿主会拒绝未知声明，前端遇到缺失能力明确提示升级。插件 manifest 要求 SDK2.11.0 与宿主1.3.6；SDK发布前，本地开发通过 `MAIBOT_PLUGIN_SDK_PATH` 使用新SDK，主程序依然兼容PyPI的2.10.0。

```json
{
  "schema_version": 1,
  "required_capabilities": ["file_upload"],
  "pages": [{
    "id": "images", "title": "图片",
    "actions": {"add": {"api": "receive_image", "parameters": {"upload_id": {"type": "string", "required": true}}}},
    "content": [{"type": "upload", "label": "上传图片", "action": "add"}]
  }]
}
```

上传使用已登录的 multipart POST `/api/webui/plugins/runtime/webui/{plugin}/{page}/uploads/{action}`；每请求一个 file，args为有界标量参数JSON字符串，文件内容不能放进args或RPC。组件允许多文件选择，逐文件显示进度和失败。宿主在解析multipart之前校验登录/声明归属与请求总量，按实际图片解码验证格式和像素，随机命名暂存，一小时一次性插件归属凭证；随后仅将upload_id传给插件API。插件声明能力 `webui.claim_upload`，SDK通过 `ctx.webui.claim_upload(upload_id)` 领取到本插件的data_dir/uploads。

首版仅JPEG/PNG/静态WebP，每文件20MiB、4000万像素；不支持压缩包/动画/客户端自定义路径。输入格式错误返回422，超限请求413，未登录401，未开放上传操作403。插件卸载或API下线后拒绝调用。

`poll_interval_seconds` 可在页面声明为3至60秒，0（默认）不轮询；只调用queries，页面离开时停止，操作忙碌时跳过。插件必须保证queries只读，训练action冻结快照、启动独立子进程后立即返回任务编号，不阻塞网关10秒预算。缩略图、分页与每响应512KiB限制保持不变。

### 交互组件能力

本次宿主注册表新增 `interactive_controls`。使用以下新增交互的插件将其加入 `required_capabilities`，与上传能力并列；旧宿主明确提示不兼容，旧声明继续有效。需同步部署前端构建。

- `select.presentation`：默认 `dropdown`，可用 `buttons` 展示选项按钮，仍复用同一套 `name`、`value` 和 `options`。
- `progress`：`value` 为0至100的有限数值或数据引用，显示进度条和百分比。
- `button.value`：可引用动态按钮文案，保留必填 `label` 作为声明标签。
- `upload.submit_label`：手动上传按钮文案，例如“开始识别”；手动上传支持拖入文件、预览及逐张移除，参数在开始时冻结。
- action可返回 `{"navigate_page":"images"}`。前端只导航到同一个插件已声明的页面，不接受URL或跨插件页面。此字段不影响普通action返回值。

依赖检查、训练、安装等长操作应由插件后台进程执行，查询只读取缓存和状态。任务/版本界面应返回可读字段，技术JSON可放在折叠区域。

上传和选择弹窗统一复用普通 `button.detail` 与顶层 `dialog.name`，按钮只能声明 `action` 或 `detail` 之一。`upload` 支持取消和重试失败文件，重试保持原批参数，关闭弹窗取消未完成请求；已接收文件保留。逐文件反馈来自插件的字符串 `message`，不由渲染器猜测业务含义。

输入和按钮支持 `disabled_when`，格式同 `when`；按钮支持 `disabled_reason`，禁用时展示原因。后台轮询不禁用按钮，操作会取消旧后台查询。导航返回 `navigate_params` 时，仅目标页面查询声明的标量参数写入当前页面URL，报告选择不再依赖共享插件设置。
