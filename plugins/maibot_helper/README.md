# maibot_helper

放在 `plugins/maibot_helper` 的普通查询助手插件，默认关闭，不属于核心插件组。在 WebUI 插件管理中启用后提供 8 个查询工具。启停使用插件卡片上的统一开关，配置页仅保留三个查询偏好。

为麦麦提供部署文档、插件 SDK 文档、版本更新日志、插件市场列表和 README 查询。

## 资源链接

`maibot_resource_link` 无需参数，返回[麦麦一键包 CNB 下载页](https://cnb.cool/Mai-with-u/MaiBotOneKey/-/releases)和[麦麦文档](https://docs.maibot.chat/)的固定链接，同时提供麦麦官网、插件市场和人设分享站入口。

## 模型任务分配

`maibot_model_tasks` 只读查看已保存的 `config/model_config.toml` 中的任务分配。留空 `task_name` 查看全部，或指定 `replyer`、`planner` 等任务名查看单项。返回模型名称、模型标识和 provider 名称，不返回 API key、服务地址、请求头、查询参数或额外鉴权配置，也不能修改配置。联网搜索任务只展示所选服务名称。

结果反映磁盘上已保存的显式配置，可能与尚未热重载的运行配置不同；空列表表示未显式分配，不推断运行时继用的模型。更新后重载插件以注册工具。

## 插件查询

- `maibot_plugin_list` 同时用于搜索和浏览，搜索词直接传 `query`，如 `query="语音"`；无需额外搜索工具。默认返回名称、描述、作者、版本、分类和仓库等摘要，保留分页、信息源和缓存状态。
- `maibot_plugin_detail` 查询一个插件的能力、依赖、Host/SDK 要求、发布版本兼容性及推荐版本。可直接传入已知插件 ID；也可使用列表返回的 `id`。
- `maibot_plugin_readme` 读取具体插件的安装、配置和使用说明。

列表及排行榜支持 `compatible_only=true`，筛选当前 Host 和 SDK 环境兼容的插件；兼容性完全由主程序判断。列表设置 `include_manifest=true` 可取完整元信息及清单；详情设置相同参数可取插件和各发布版本的完整清单。默认精简重复数据，减少模型上下文占用。

## 插件统计与排行榜

`maibot_plugin_list` 可查询插件下载量、点赞数、点踩数、评分、评分人数、评论数，以及 7 日／30 日下载和点赞飙升排名。

- 查询单个插件：调用 `maibot_plugin_detail` 并传入 `plugin_id`，支持插件 ID、市场 ID、名称或仓库地址。匹配多个插件时返回候选项，需指定准确 ID。
- 下载最多：`sort_by="downloads"`（列表默认按 `name` 排序）。
- 点赞最多：`sort_by="likes"`。
- 评分最高：`sort_by="rating"`，结果同时提供评分人数。
- 飙升榜：`sort_by="downloads_growth_7d"`、`"likes_growth_7d"`、`"downloads_growth_30d"` 或 `"likes_growth_30d"`。
- 排行榜支持 `query`、`category`、`page`、`limit`；每页默认 20 个，最多 50 个。

飙升位次来自服务端全市场排行榜，按本期比上期的增加量排序。筛选和分页不会重算位次；`null` 表示未上榜，字段缺失表示接口未提供。接口未提供所选飙升榜时返回明确错误。

`maibot_plugin_list` 可传 `include_stats=true` 附带统计，未匹配统计时 `stats=null`。排行榜默认返回插件摘要及统计；单插件详情同时返回兼容性和发布版本。统计请求失败会返回错误；非官方清单源的列表和排行榜会合并官方统计；单插件详情中的统计可能为空。

需要 SDK 2.15.0 及配套 Host。市场列表、统计及 README 通过 `ctx.market` 能力查询，与 WebUI 共用主程序配置的信息源、缓存和并发请求合并，不再自行轮询镜像。官方快照缓存 5 分钟，CNB/GitHub 目录和远程 README 缓存 30 分钟，`force_refresh=true` 强制刷新；统计结果提供 `stats_as_of`、`growth_ranking`、来源和缓存状态。

README 优先读取本地已安装插件；没有本地 README 时按所选信息源读取登记仓库指定分支的 `README.md`。默认分支为 `main`，可显式传 `branch`，失败直接报错，不自动切换分支。

更新后在 WebUI 重载 helper 插件以注册新工具。

本地联调尚未发布的 SDK 时，启动主程序前设置 `MAIBOT_PLUGIN_SDK_PATH` 为上层 `maibot-plugin-sdk` 仓库的绝对路径；运行中的 Runner 需要重启才能切换 SDK。

## 文档正文与版本查询

文档正文默认返回 6400 字符，最多 12800 字符。版本查询仅查询稳定版，返回 MaiBot 最新版本及近期更新日志、WebUI 的 PyPI 版本和独立 Release 日志，以及各来源错误；不提供预发布开关。

统计排序自动附带统计数据，无需设置 include_stats。共提供 8 个工具。

## 查询偏好

WebUI 配置页仅提供三个常用选项：每次显示的插件数（默认 20）、默认只看兼容插件（默认关闭）、优先读取本地说明（默认开启）。字段及分组支持中文、英文和日文。保存后应用于未显式传参的查询，单次工具参数优先。首次重载插件后由 Runner 生成配置，无需手写 config.toml。
