# 更新日志 (Changelog)

## [2.0.0] - 2026-03-18

本次 `2.0.0` 为架构收敛版本，主线是 **SDK Tool 接口统一**、**管理工具能力补齐**、**元数据 schema 升级到 v8** 与 **文档口径同步到 2.0.0**。

### 🔖 版本信息

- 插件版本：`1.0.1` → `2.0.0`
- 元数据 schema：`7` → `8`

### 🚀 重点能力

- Tool 接口统一：
  - `plugin.py` 统一通过 `SDKMemoryKernel` 对外提供 Tool 能力。
  - 保留基础工具：`search_memory / ingest_summary / ingest_text / get_person_profile / maintain_memory / memory_stats`。
  - 新增管理工具：`memory_graph_admin / memory_source_admin / memory_episode_admin / memory_profile_admin / memory_runtime_admin / memory_import_admin / memory_tuning_admin / memory_v5_admin / memory_delete_admin`。
- 检索与写入治理增强：
  - 检索/写入链路支持 `respect_filter + user_id/group_id` 的聊天过滤语义。
  - `maintain_memory` 支持 `freeze` 与 `recycle_bin`，并统一到内核维护流程。
- 导入与调优能力收敛：
  - `memory_import_admin` 提供任务化导入能力（上传、粘贴、扫描、OpenIE、LPMM 转换、时序回填、MaiBot 迁移）。
  - `memory_tuning_admin` 提供检索调优任务（创建、轮次查看、回滚、apply_best、报告导出）。
- V5 与删除运维：
  - 新增 `memory_v5_admin`（`reinforce/weaken/remember_forever/forget/restore/status`）。
  - 新增 `memory_delete_admin`（`preview/execute/restore/list/get/purge`），支持操作审计与恢复。

### 🛠️ 存储与运行时

- `metadata_store` 升级到 `SCHEMA_VERSION = 8`。
- 新增/完善外部引用与运维记录能力（包括 `external_memory_refs`、`memory_v5_operations`、`delete_operations` 相关数据结构）。
- `SDKMemoryKernel` 增加统一后台任务编排（自动保存、Episode pending 处理、画像刷新、记忆维护）。

### 📚 文档同步

- `README.md`、`QUICK_START.md`、`CONFIG_REFERENCE.md`、`IMPORT_GUIDE.md` 已切换到 `2.0.0` 口径。
- 文档主入口统一为 SDK Tool 工作流，不再以旧版 slash 命令作为主说明路径。

## [1.0.1] - 2026-03-07

本次 `1.0.1` 为 `1.0.0` 发布后的热修复版本，主线是 **图谱 WebUI 取数稳定性修复**、**大图过滤性能修复** 与 **真实检索调优链路稳定性修复**。

### 🔖 版本信息

- 插件版本：`1.0.0` → `1.0.1`
- 配置版本：`4.1.0`（不变）

### 🛠️ 代码修复

- 图谱接口稳定性：
  - 修复 `/api/graph` 在“磁盘已有图文件但运行时尚未装载入内存”场景下返回空图的问题，接口现在会自动补加载持久化图数据。
  - 修复问题数据集下 WebUI 打开图谱页时看似“没有任何节点”的现象；根因不是图数据消失，而是后端过滤路径过慢。
- 图谱过滤性能：
  - 优化 `/api/graph?exclude_leaf=true` 的叶子过滤逻辑，改为预计算 hub 邻接关系，不再对每个节点反复做高成本边权查询。
  - 优化 `GraphStore.get_neighbors()` 并补充入邻居访问能力，避免稠密矩阵展开导致的大图性能退化。
- 检索调优稳定性：
  - 修复真实调优任务在构建运行时配置时深拷贝 `plugin.config`，误复制注入的存储实例并触发 `cannot pickle '_thread.RLock' object` 的问题。
  - 调优评估改为跳过顶层运行时实例键，仅保留纯配置字段后再附加运行时依赖，真实 WebUI 调优任务可正常启动。

### 📚 文档同步

- 同步更新 `README.md`、`CHANGELOG.md`、`CONFIG_REFERENCE.md` 与版本元数据（`plugin.py`、`__init__.py`、`_manifest.json`）。
- README 新增 `v1.0.1` 修复说明，并补充“调优前先做 runtime self-check”的建议。

## [1.0.0] - 2026-03-06

本次 `1.0.0` 为主版本升级，主线是 **运行时架构模块化**、**Episode 情景记忆闭环**、**聚合检索与图召回增强**、**离线迁移 / 运行时自检 / 检索调优中心**。

### 🔖 版本信息

- 插件版本：`0.7.0` → `1.0.0`
- 配置版本：`4.1.0`（不变）

### 🚀 重点能力

- 运行时重构：
  - `plugin.py` 大幅瘦身，生命周期、后台任务、请求路由、检索运行时初始化拆分到 `core/runtime/*`。
  - 配置 schema 抽离到 `core/config/plugin_config_schema.py`，`_manifest.json` 同步扩展新配置项。
- 检索与查询增强：
  - `KnowledgeQueryTool` 拆分为 query mode + orchestrator，新增长 `aggregate` / `episode` 查询模式。
  - 新增图辅助关系召回、统一 forward/runtime 构建与请求去重桥接。
- Episode / 运维能力：
  - `metadata_store` schema 升级到 `SCHEMA_VERSION = 7`，新增 `episodes` / `episode_paragraphs` / rebuild queue 等结构。
  - 新增 `release_vnext_migrate.py`、`runtime_self_check.py`、`rebuild_episodes.py` 与 Web 检索调优页 `web/tuning.html`。

### 📚 文档同步

- 版本号同步到 `plugin.py`、`__init__.py`、`_manifest.json`、`README.md` 与 `CONFIG_REFERENCE.md`。
- 新增 `RELEASE_SUMMARY_1.0.0.md`

## [0.7.0] - 2026-03-04

本次 `0.7.0` 为中版本升级，主线是 **关系向量化闭环（写入 + 状态机 + 回填 + 审计）**、**检索/命令链路增强** 与 **导入任务能力补齐**。

### 🔖 版本信息

- 插件版本：`0.6.1` → `0.7.0`
- 配置版本：`4.1.0`（不变）

### 🚀 重点能力

- 关系向量化闭环：
  - 新增统一关系写入服务 `RelationWriteService`（metadata 先写、向量后写，失败进入状态机而非回滚主数据）。
  - `relations` 侧补齐 `vector_state/retry_count/last_error/updated_at` 等状态字段，支持 `none/pending/ready/failed` 统一治理。
  - 插件新增后台回填循环与统计接口，可持续修复关系向量缺失并暴露覆盖率指标。
- 检索与命令链路增强：
  - 检索主链继续收敛到 `search/time` forward 路由，`legacy` 仅保留兼容别名。
  - relation 查询规格解析收口，结构化查询与语义回退边界更清晰。
  - `/query stats` 与 tool stats 补充关系向量化统计输出。
- 导入与运维增强：
  - Web Import 新增 `temporal_backfill` 任务入口与编排处理。
  - 新增一致性审计与离线回填脚本，支持灰度修复历史数据。

### 📚 文档同步

- 同步更新 `README.md`、`CONFIG_REFERENCE.md` 与本日志版本信息。
- `README.md` 新增关系向量审计/回填脚本使用说明，并更新 `convert_lpmm.py` 的关系向量重建行为描述。

## [0.6.1] - 2026-03-03

本次 `0.6.1` 为热修复小版本，重点修复 WebUI 插件配置接口在 A_Memorix 场景下的 `tomlkit` 节点序列化兼容问题。

### 🔖 版本信息

- 插件版本：`0.6.0` → `0.6.1`
- 配置版本：`4.1.0`（不变）

### 🛠️ 代码修复

- 新增运行时补丁 `_patch_webui_a_memorix_routes_for_tomlkit_serialization()`：
  - 仅包裹 `/api/webui/plugins/config/{plugin_id}` 及其 schema 的 `GET` 路由。
  - 仅在 `plugin_id == "A_Memorix"` 时，将返回中的 `config/schema` 通过 `to_builtin_data` 原生化。
  - 保持 `/api/webui/config/*` 全局接口行为不变，避免对其他插件或核心配置路径产生副作用。
- 在插件初始化时执行该补丁，确保 WebUI 读取插件配置时返回结构可稳定序列化。

### 📚 文档同步

- 同步更新 `README.md`、`CONFIG_REFERENCE.md` 与本日志中的版本信息及修复说明。

## [0.6.0] - 2026-03-02

本次 `0.6.0` 为中版本升级，主线是 **Web Import 导入中心上线与脚本能力对齐**、**失败重试机制升级**、**删除后 manifest 同步** 与 **导入链路稳定性增强**。

### 🔖 版本信息

- 插件版本：`0.5.1` → `0.6.0`
- 配置版本：`4.0.1` → `4.1.0`

### 🚀 重点能力

- 新增 Web Import 导入中心（`/import`）：
  - 上传/粘贴/本地扫描/LPMM OpenIE/LPMM 转换/时序回填/MaiBot 迁移。
  - 任务/文件/分块三级状态展示，支持取消与失败重试。
  - 导入文档弹窗读取（远程优先，失败回退本地）。
- 失败重试升级为“分块优先 + 文件回退”：
  - `POST /api/import/tasks/{task_id}/retry_failed` 保持原路径，语义升级。
  - 支持对 `extracting` 失败分块进行子集重试。
  - `writing`/JSON 解析失败自动回退为文件级重试。
- 删除后 manifest 同步失效：
  - 覆盖 `/api/source/batch_delete` 与 `/api/source`。
  - 返回 `manifest_cleanup` 明细，避免误命中去重跳过重导入。

### 📂 变更文件清单（本次发布）

新增文件：

- `core/utils/web_import_manager.py`
- `scripts/migrate_maibot_memory.py`
- `web/import.html`

修改文件：

- `CHANGELOG.md`
- `CONFIG_REFERENCE.md`
- `IMPORT_GUIDE.md`
- `QUICK_START.md`
- `README.md`
- `__init__.py`
- `_manifest.json`
- `components/commands/debug_server_command.py`
- `core/embedding/api_adapter.py`
- `core/storage/graph_store.py`
- `core/utils/summary_importer.py`
- `plugin.py`
- `requirements.txt`
- `server.py`
- `web/index.html`

删除文件：

- 无

### 📚 文档同步

- 同步更新 `README.md`、`QUICK_START.md`、`CONFIG_REFERENCE.md`、`IMPORT_GUIDE.md` 与本日志。
- `IMPORT_GUIDE.md` 新增 “Web Import 导入中心” 专区，统一说明能力范围、状态语义与安全边界。

## [0.5.1] - 2026-02-23

本次 `0.5.1` 为热修订小版本，重点修复“随主程序启动的后台任务拉起”“空名单过滤语义”以及“知识抽取模型选择”。

### 🔖 版本信息

- 插件版本：`0.5.0` → `0.5.1`
- 配置版本：`4.0.0` → `4.0.1`

### 🛠️ 代码修复

- 生命周期接入主程序事件：
  - 新增 `a_memorix_start_handler`（`ON_START`）调用 `plugin.on_enable()`；
  - 新增 `a_memorix_stop_handler`（`ON_STOP`）调用 `plugin.on_disable()`；
  - 解决仅注册插件但未触发生命周期时，定时导入任务不启动的问题。
- 聊天过滤空列表策略调整：
  - `whitelist + []`：全部拒绝；
  - `blacklist + []`：全部放行。
- 知识抽取模型选择逻辑调整（`import_command._select_model`）：
  - `advanced.extraction_model` 现在支持三种语义：任务名 / 模型名 / `auto`；
  - `auto` 优先抽取相关任务（`lpmm_entity_extract`、`lpmm_rdf_build` 等），并避免误落到 `embedding`；
  - 当配置无法识别时输出告警并回退自动选择，提高导入阶段的模型选择可预期性。

### 📚 文档同步

- 同步更新 `README.md`、`CONFIG_REFERENCE.md` 与 `CHANGELOG.md`。
- 同步修正文档中的空名单过滤行为描述，保持与当前代码一致。

## [0.5.0] - 2026-02-15

本次 `0.5.0` 以提交 `66ddc1b98547df3c866b19a3f5dc96e1c8eb7731` 为核心，主线是“人物画像能力上线 + 工具/命令接入 + 版本与文档同步”。

### 🔖 版本信息

- 插件版本：`0.4.0` → `0.5.0`
- 配置版本：`3.1.0` → `4.0.0`

### 🚀 人物画像主特性（核心）

- 新增人物画像服务：`core/utils/person_profile_service.py`
  - 支持 `person_id/姓名/别名` 解析。
  - 聚合图关系证据 + 向量证据，生成画像文本并版本化快照。
  - 支持手工覆盖（override）与 TTL 快照复用。
- 存储层新增人物画像相关表与 API：`core/storage/metadata_store.py`
  - `person_profile_switches`
  - `person_profile_snapshots`
  - `person_profile_active_persons`
  - `person_profile_overrides`
- 新增命令：`/person_profile on|off|status`
  - 文件：`components/commands/person_profile_command.py`
  - 作用：按 `stream_id + user_id` 控制自动注入开关（opt-in 模式）。
- 查询链路接入人物画像：
  - `knowledge_query_tool` 新增 `query_type=person`，支持 `person_id` 或别名查询。
  - `/query person` 与 `/query p` 接入画像查询输出。
- 插件生命周期接入画像刷新任务：
  - 启动/停止统一管理 `person_profile_refresh` 后台任务。
  - 按活跃窗口自动刷新画像快照。

### 🛠️ 版本与 schema 同步

- `plugin.py`：`plugin_version` 更新为 `0.5.0`。
- `plugin.py`：`plugin.config_version` 默认值更新为 `4.0.0`。
- `config.toml`：`config_version` 基线同步为 `4.0.0`（本地配置文件）。
- `__init__.py`：`__version__` 更新为 `0.5.0`。
- `_manifest.json`：`version` 更新为 `0.5.0`，`manifest_version` 保持 `1` 。
- `manifest_utils.py`：仓库内已兼容更高 manifest 版本；但插件发布默认保持 `manifest_version=1` 。

### 📚 文档同步

- 更新 `README.md`、`CONFIG_REFERENCE.md`、`QUICK_START.md`、`USAGE_ARCHITECTURE.md`。
- 0.5.0 文档主线改为“人物画像能力 + 版本升级 + 检索链路补充说明”。

## [0.4.0] - 2026-02-13

本次 `0.4.0` 版本整合了时序检索增强与后续检索链路增强、稳定性修复和文档同步。

### 🔖 版本信息

- 插件版本：`0.3.3` → `0.4.0`
- 配置版本：`3.0.0` → `3.1.0`

### 🚀 新增

- 新增 `core/retrieval/sparse_bm25.py`
  - `SparseBM25Config` / `SparseBM25Index`
  - FTS5 + BM25 稀疏检索
  - 支持 `jieba/mixed/char_2gram` 分词与懒加载
  - 支持 ngram 倒排回退与可选 LIKE 兜底
- `DualPathRetriever` 新增 sparse/fusion 配置注入：
  - embedding 不可用时自动 sparse 回退；
  - `hybrid` 模式支持向量路 + sparse 路并行候选；
  - 新增 `FusionConfig` 与 `weighted_rrf` 融合。
- `MetadataStore` 新增 FTS/倒排能力：
  - `paragraphs_fts`、`relations_fts` schema 与回填；
  - `paragraph_ngrams` 倒排索引与回填；
  - `fts_search_bm25` / `fts_search_relations_bm25` / `ngram_search_paragraphs`。

### 🛠️ 组件链路同步

- `plugin.py`
  - 新增 `[retrieval.sparse]`、`[retrieval.fusion]` 默认配置；
  - 初始化并向组件注入 `sparse_index`；
  - `on_disable` 支持按配置卸载 sparse 连接并释放缓存。
- `knowledge_search_action.py` / `query_command.py` / `knowledge_query_tool.py`
  - 统一接入 sparse/fusion 配置；
  - 统一注入 `sparse_index`；
  - `stats` 输出新增 sparse 状态观测。
- `requirements.txt`
  - 新增 `jieba>=0.42.1`（未安装时自动回退 char n-gram）。

### 🧯 修复与行为调整

- 修复 `retrieval.ppr_concurrency_limit` 不生效问题：
  - `DualPathRetriever` 使用配置值初始化 `_ppr_semaphore`，不再被固定值覆盖。
- 修复 `char_2gram` 召回失效场景：
  - FTS miss 时增加 `_fallback_substring_search`，优先 ngram 倒排回退，按配置可选 LIKE 兜底。
- 提升可观测性与兼容性：
  - `get_statistics()` 对向量规模字段兼容读取 `size -> num_vectors -> 0`，避免属性缺失导致异常。
  - `/query stats` 与 `knowledge_query` 输出包含 sparse 状态（enabled/loaded/tokenizer/doc_count）。

### 📚 文档

- `README.md`
  - 新增检索增强说明、稀疏行为说明、时序回填脚本入口。
- `CONFIG_REFERENCE.md`
  - 补齐 sparse/fusion 参数与触发规则、回退链路、融合实现细节。

### ⏱️ 时序检索与导入增强

#### 时序检索能力（分钟级）

- 新增统一时序查询入口：
  - `/query time`（别名 `/query t`）
  - `knowledge_query(query_type=time)`
  - `knowledge_search(query_type=time|hybrid)`
- 查询时间参数统一支持：
  - `YYYY/MM/DD`
  - `YYYY/MM/DD HH:mm`
- 日期参数自动展开边界：
  - `from/time_from` -> `00:00`
  - `to/time_to` -> `23:59`
- 查询结果统一回传 `metadata.time_meta`，包含命中时间窗口与命中依据（事件时间或 `created_at` 回退）。

#### 存储与检索链路

- 段落存储层支持时序字段：
  - `event_time`
  - `event_time_start`
  - `event_time_end`
  - `time_granularity`
  - `time_confidence`
- 时序命中采用区间相交逻辑，并遵循“双层时间语义”：
  - 优先 `event_time/event_time_range`
  - 缺失时回退 `created_at`（可配置关闭）
- 检索排序规则保持：语义优先，时间次排序（新到旧）。
- `process_knowledge.py` 新增 `--chat-log` 参数：
  - 启用后强制使用 `narrative` 策略；
  - 使用 LLM 对聊天文本进行语义时间抽取（支持相对时间转绝对时间），写入 `event_time/event_time_start/event_time_end`。
  - 新增 `--chat-reference-time`，用于指定相对时间语义解析的参考时间点。

#### Schema 与文档同步

- `_manifest.json` 同步补齐 `retrieval.temporal` 配置 schema。
- 配置 schema 版本升级：`config_version` 从 `3.0.0` 提升到 `3.1.0`（`plugin.py` / `config.toml` / 配置文档同步）。
- 更新 `README.md`、`CONFIG_REFERENCE.md`、`IMPORT_GUIDE.md`，补充时序检索入口、参数格式与导入时间字段说明。

## [0.3.3] - 2026-02-11

本次更新为 **语言一致性补丁版本**，重点收敛知识抽取时的语言漂移问题，要求输出严格贴合原文语言，不做翻译改写。

### 🛠️ 关键修复

#### 抽取语言约束

- `BaseStrategy`:
  - 移除按 `zh/en/mixed` 分支的语言类型判定逻辑；
  - 统一为单一约束：抽取值保持原文语言、保留原始术语、禁止翻译。
- `NarrativeStrategy` / `FactualStrategy`:
  - 抽取提示词统一接入上述语言约束；
  - 明确要求 JSON 键名固定、抽取值遵循原文语言表达。

#### 导入链路一致性

- `ImportCommand` 的 LLM 抽取提示词同步强化“优先原文语言、不要翻译”要求，避免脚本与指令导入行为不一致。

#### 测试与文档

- 更新 `test_strategies.py`，将语言判定测试调整为统一语言约束测试，并验证提示词中包含禁止翻译约束。
- 同步更新注释与文档描述，确保实现与说明一致。

### 🔖 版本信息

- 插件版本：`0.3.2` → `0.3.3`

## [0.3.2] - 2026-02-11

本次更新为 **V5 稳定性与兼容性修复版本**，在保持原有业务设计（强化→衰减→冷冻→修剪→回收）的前提下，修复关键链路断裂与误判问题。

### 🛠️ 关键修复

#### V5 记忆系统契约与链路

- `MetadataStore`:
  - 统一 `mark_relations_inactive(hashes, inactive_since=None)` 调用契约，兼容不同调用方；
  - 补充 `has_table(table_name)`；
  - 增加 `restore_relation(hash)` 兼容别名，修复服务层恢复调用断裂；
  - 修正 `get_entity_gc_candidates` 对孤立节点参数的处理（支持节点名映射到实体 hash）。
- `GraphStore`:
  - 清理 `deactivate_edges` 重复定义并统一返回冻结数量，保证上层日志与断言稳定。
- `server.py`:
  - 修复 `/api/memory/restore` relation 恢复链路；
  - 清理不可达分支并统一异常路径；
  - 回收站查询在表检测场景下不再出现错误退空。

#### 命令与模型选择

- `/memory` 命令修复 hash 长度判定：以 64 位 `sha256` 为标准，同时兼容历史 32 位输入。
- 总结模型选择修复：
  - 解决 `summarization.model_name = auto` 误命中 `embedding` 问题；
  - 支持数组与选择器语法（`task:model` / task / model）；
  - 兼容逗号分隔字符串写法（如 `"utils:model1","utils:model2",replyer`）。

#### 生命周期与脚本稳定性

- `plugin.py` 修复后台任务生命周期管理：
  - 增加 `_scheduled_import_task` / `_auto_save_task` / `_memory_maintenance_task` 句柄；
  - 避免重复启动；
  - 插件停用时统一 cancel + await 收敛。
- `process_knowledge.py` 修复 tenacity 重试日志级别类型错误（`"WARNING"` → `logging.WARNING`），避免 `KeyError: 'WARNING'`。

### 🔖 版本信息

- 插件版本：`0.3.1` → `0.3.2`

## [0.3.1] - 2026-02-07

本次更新为 **稳定性补丁版本**，主要修复脚本导入链路、删除安全性与 LPMM 转换一致性问题。

### 🛠️ 关键修复

#### 新增功能

- 新增 `scripts/convert_lpmm.py`：
  - 支持将 LPMM 的 `parquet + graph` 数据直接转换为 A_Memorix 存储结构；
  - 提供 LPMM ID 到 A_Memorix ID 的映射能力，用于图节点/边重写；
  - 当前实现优先保证检索一致性，关系向量采用安全策略（不直接导入）。

#### 导入链路

- 修复 `import_lpmm_json.py` 依赖的 `AutoImporter.import_json_data` 公共入口缺失/不稳定问题，确保外部脚本可稳定调用 JSON 直导入流程。

#### 删除安全

- 修复按来源删除时“同一 `(subject, object)` 存在多关系”场景下的误删风险：
  - `MetadataStore.delete_paragraph_atomic` 新增 `relation_prune_ops`；
  - 仅在无兄弟关系时才回退删除整条边。
- `delete_knowledge.py` 新增保守孤儿实体清理（仅对本次候选实体执行，且需同时满足无段落引用、无关系引用、图无邻居）。
- `delete_knowledge.py` 改为读取向量元数据中的真实维度，避免 `dimension=1` 写回污染。

#### LPMM 转换修复

- 修复 `convert_lpmm.py` 中向量 ID 与 `MetadataStore` 哈希不一致导致的检索反查失败问题。
- 为避免脏召回，转换阶段暂时跳过 `relation.parquet` 的直接向量导入（待关系元数据一一映射能力完善后再恢复）。

### 🔖 版本信息

- 插件版本：`0.3.0` → `0.3.1`

## [0.3.0] - 2026-01-30

本次更新引入了 **V5 动态记忆系统**，实现了符合生物学特性的记忆衰减、强化与全声明周期管理，并提供了配套的指令与工具。

### 🧠 记忆系统 (V5)

#### 核心机制

- **记忆衰减 (Decay)**: 引入"遗忘曲线"，随时间推移自动降低图谱连接权重。
- **访问强化 (Reinforcement)**: "越用越强"，每次检索命中都会刷新记忆活跃度并增强权重。
- **生命周期 (Lifecycle)**:
  - **活跃 (Active)**: 正常参与计算与检索。
  - **冷冻 (Inactive)**: 权重过低被冻结，不再参与 PPR 计算，但保留语义映射 (Mapping)。
  - **修剪 (Prune)**: 过期且无保护的冷冻记忆将被移入回收站。
- **多重保护**: 支持 **永久锁定 (Pin)** 与 **限时保护 (TTL)**，防止关键记忆被误删。

#### GraphStore

- **多关系映射**: 实现 `(u,v) -> Set[Hash]` 映射，确保同一通道下的多重语义关系互不干扰。
- **原子化操作**: 新增 `decay`, `deactivate_edges` (软删), `prune_relation_hashes` (硬删) 等原子操作。

### 🛠️ 指令与工具

#### Memory Command (`/memory`)

新增全套记忆维护指令：

- `/memory status`: 查看记忆系统健康状态（活跃/冷冻/回收站计数）。
- `/memory protect <query> [hours]`: 保护记忆。不填时间为永久锁定(Pin)，填时间为临时保护(TTL)。
- `/memory reinforce <query>`: 手动强化记忆（绕过冷却时间）。
- `/memory restore <hash>`: 从回收站恢复误删记忆（仅当节点存在时重建连接）。

#### MemoryModifierTool

- **LLM 能力增强**: 更新工具逻辑，支持 LLM 自主触发 `reinforce`, `weaken`, `remember_forever`, `forget` 操作，并自动映射到 V5 底层逻辑。

### ⚙️ 配置 (`config.toml`)

新增 `[memory]` 配置节：

- `half_life_hours`: 记忆半衰期 (默认 24h)。
- `enable_auto_reinforce`: 是否开启检索自动强化。
- `prune_threshold`: 冷冻/修剪阈值 (默认 0.1)。

### 💻 WebUI (v1.4)

实现了与 V5 记忆系统深度集成的全生命周期管理界面：

- **可视化增强**:
  - **冷冻状态**: 非活跃记忆以 **虚线 + 灰色 (Slate-300)** 显示。
  - **保护状态**: 被 Pin 或保护的记忆带有 **金色 (Amber) 光晕**。
- **交互升级**:
  - **记忆回收站**: 新增 Dock 入口与专用面板，支持浏览删除记录并一键恢复。
  - **快捷操作**: 边属性面板新增 **强化 (Reinforce)**、**保护 (Protect/Pin)**、**冷冻 (Freeze)** 按钮。
  - **实时反馈**: 操作后自动刷新图谱布局与样式。

---

## [0.2.3] - 2026-01-30

本次更新主要集中在 **WebUI 交互体验优化** 与 **文档/配置的规范化**。

### 🎨 WebUI (v1.3)

#### 加载与同步体验升级

- **沉浸式加载**: 全新设计的加载遮罩，采用磨砂玻璃背景 (`backdrop-filter`) 与呼吸灯文字动效，提升视觉质感。
- **精准状态反馈**: 优化加载逻辑，明确区分“网络同步”与“拓扑计算”阶段，解决数据加载时的闪烁问题。
- **新手引导**: 在加载界面新增基础操作提示，降低新用户上手门槛。

#### 全功能帮助面板

- **操作指南重构**: 全面翻新“操作指南”面板，新增 Dock 栏功能详解、编辑管理操作及视图配置说明。

### 🛠️ 工程与规范

#### plugin.py

- **配置描述补全**: 修复了 `config_section_descriptions` 中缺失 `summarization`, `schedule`, `filter` 节导致的问题。
- **版本号**: `0.2.2` → `0.2.3`

### ⚙️ 核心与服务

#### Core

- **量化逻辑修正**: 修正了 `_scalar_quantize_int8` 函数，确保向量值正确映射到 `[-128, 127]` 区间，提高量化精度。

#### Server

- **缓存一致性**: 在执行删除节点/边等修改操作后，显式清除 `_relation_cache`，确保前端获取的关系数据实时更新。

### 🤖 脚本与数据处理

#### process_knowledge.py

- **策略模式重构**: 引入了 `Strategy-Aware` 架构，支持通过 `Narrative` (叙事), `Factual` (事实), `Quote` (引用) 三种策略差异化处理文本(准确说是确认实装)（默认采用 Narrative模式）。
- **智能分块纠错**: 新增“分块拯救” (`Chunk Rescue`) 机制，可在长叙事文本中自动识别并提取内嵌的歌词或诗句。

#### import_lpmm_json.py

- **LPMM 迁移工具**: 增加了对 LPMM OpenIE JSON 格式的完整支持，能够自动计算 Hash 并迁移实体/关系数据，确保与 A_Memorix 存储格式兼容。

#### Project

- **构建清理**: 优化 `.gitignore` 规则

---

## [0.2.2] - 2026-01-27

本次更新专注于提高 **网络请求的鲁棒性**，特别是针对嵌入服务的调用。

### 🛠️ 稳定性与工程改进

#### EmbeddingAPI

- **可配置重试机制**: 新增 `[embedding.retry]` 配置项，允许自定义最大重试次数和等待时间。默认重试次数从 3 次增加到 10 次，以更好应对网络波动。
- **配置项**:
  - `max_attempts`: 最大重试次数 (默认: 10)
  - `max_wait_seconds`: 最大等待时间 (默认: 30s)
  - `min_wait_seconds`: 最小等待时间 (默认: 2s)

#### plugin.py

- **版本号**: `0.2.1` → `0.2.2`

---

## [0.2.1] - 2026-01-26

本次更新重点在于 **可视化交互的全方位重构** 以及 **底层鲁棒性的进一步增强**。

### 🎨 可视化与交互重构

#### WebUI (Glassmorphism)

- **全新视觉设计**: 采用深色磨砂玻璃 (Glassmorphism) 风格，配合动态渐变背景。
- **Dock 菜单栏**: 底部新增 macOS 风格 Dock 栏，聚合所有常用功能。
- **显著性视图 (Saliency View)**: 基于 **PageRank** 算法的“信息密度”滑块，支持以此过滤叶子节点，仅展示核心骨干或全量细节。
- **功能面板**:
  - **❓ 操作指南**: 内置交互说明与特性介绍。
  - **🔍 悬浮搜索**: 支持按拼音/ID 实时过滤节点。
  - **📂 记忆溯源**: 支持按源文件批量查看和删除记忆数据。
  - **📖 内容字典**: 列表化展示所有实体与关系，支持排序与筛选。

### 🛠️ 稳定性与工程改进

#### EmbeddingAPI

- **鲁棒性增强**: 引入 `tenacity` 实现指数退避重试机制。
- **错误处理**: 失败时返回 `NaN` 向量而非零向量，允许上层逻辑安全跳过。

#### MetadataStore

- **自动修复**: 自动检测并修复 `vector_index` 列错位（文件名误存）的历史数据问题。
- **数据统计**: 新增 `get_all_sources` 接口支持来源统计。

#### 脚本与工具

- **用户体验**: 引入 `rich` 库优化终端输出进度条与状态显示。
- **接口开放**: `process_knowledge.py` 新增 `import_json_data` 供外部调用。
- **LPMM 迁移**: 新增 `import_lpmm_json.py`，支持导入符合 LPMM 规范的 OpenIE JSON 数据。

#### plugin.py

- **版本号**: `0.2.0` → `0.2.1`

---

## [0.2.0] - 2026-01-22

> [!CAUTION]
> **不完全兼容变更**：v0.2.0 版本重构了底层存储架构。由于数据结构的重大调整，**旧版本的导入数据无法在新版本中完全无损兼容**。
> 虽然部分组件支持自动迁移，但为确保数据一致性和检索质量，**强烈建议在升级后重新使用 `process_knowledge.py` 导入原始数据**。

本次更新为**重大版本升级**，包含向量存储架构重写、检索逻辑强化及多项稳定性改进。

### 🚀 核心架构重写

#### VectorStore: SQ8 量化 + Append-Only 存储

- **全新存储格式**: 从 `.npy` 迁移至 `vectors.bin`（float16 增量追加）和 `vectors_ids.bin`，大幅减少内存占用。
- **原生 SQ8 量化**: 使用 Faiss `IndexScalarQuantizer(QT_8bit)`，替代手动 int8 量化逻辑。
- **L2 Normalization 强制化**: 所有向量在存储和检索时统一执行 L2 归一化，确保 Inner Product 等价于 Cosine 相似度。
- **Fallback 索引机制**: 新增 `IndexFlatIP` 回退索引，在 SQ8 训练完成前提供检索能力，避免冷启动无结果问题。
- **Reservoir Sampling 训练采样**: 使用蓄水池采样收集训练数据（上限 10k），保证小数据集和流式导入场景下的训练样本多样性。
- **线程安全**: 新增 `threading.RLock` 保护并发读写操作。
- **自动迁移**: 支持从旧版 `.npy` 格式自动迁移至新 `.bin` 格式。

### ✨ 检索功能增强

#### KnowledgeQueryTool: 智能回退与多跳路径搜索

- **Smart Fallback (智能回退)**: 当向量检索置信度低于阈值 (默认 0.6) 时，自动尝试提取查询中的实体进行多跳路径搜索（`_path_search`），增强对间接关系的召回能力。
- **结果去重 (`_deduplicate_results`)**: 新增基于内容相似度的安全去重逻辑，防止冗余结果污染 LLM 上下文，同时确保至少保留一条结果。
- **语义关系检索 (`_semantic_search_relation`)**: 支持自然语言查询关系（无需 `S|P|O` 格式），内部使用 `REL_ONLY` 策略进行向量检索。
- **路径搜索 (`_path_search`)**: 新增 `GraphStore.find_paths` 调用，支持查找两个实体间的间接连接路径（最大深度 3，最多 5 条路径）。
- **Clean Output**: LLM 上下文中不再包含原始相似度分数，避免模型偏见。

#### DualPathRetriever: 并发控制与调试模式

- **PPR 并发限制 (`ppr_concurrency_limit`)**: 新增 Semaphore 控制 PageRank 计算并发数，防止 CPU 峰值过载。
- **Debug 模式**: 新增 `debug` 配置项，启用时打印检索结果原文到日志。
- **Entity-Pivot 关系检索**: 优化 `_retrieve_relations_only` 策略，通过检索实体后扩展其关联关系，替代直接检索关系向量。

### ⚙️ 配置与 Schema 扩展

#### plugin.py

- **版本号**: `0.1.3` → `0.2.0`
- **默认配置版本**: `config_version` 默认值更新为 `2.0.0`
- **新增配置项**:
  - `retrieval.relation_semantic_fallback` (bool): 是否启用关系查询的语义回退。
  - `retrieval.relation_fallback_min_score` (float): 语义回退的最小相似度阈值。
- **相对路径支持**: `storage.data_dir` 现在支持相对路径（相对于插件目录），默认值改为 `./data`。
- **全局实例获取**: 新增 `A_MemorixPlugin.get_global_instance()` 静态方法，供组件可靠获取插件实例。

#### config.toml / \_manifest.json

- **新增 `ppr_concurrency_limit`**: 控制 PPR 算法并发数。
- **新增训练阈值配置**: `embedding.min_train_threshold` 控制触发 SQ8 训练的最小样本数。

### 🛠️ 稳定性与工程改进

#### GraphStore

- **`find_paths` 方法**: 新增多跳路径查找功能，支持 BFS 搜索指定深度内的实体间路径。
- **`find_node` 方法**: 新增大小写不敏感的节点查找。

#### MetadataStore

- **Schema 迁移**: 自动添加缺失的 `is_permanent`, `last_accessed`, `access_count` 字段。

#### 脚本与工具

- **新增脚本**:
  - `scripts/diagnose_relations_source.py`: 诊断关系溯源问题。
  - `scripts/verify_search_robustness.py`: 验证检索鲁棒性。
  - `scripts/run_stress_test.py`, `stress_test_data.py`: 压力测试套件。
  - `scripts/migrate_canonicalization.py`, `migrate_paragraph_relations.py`: 数据迁移工具。
- **目录整理**: 将大量旧版测试脚本移动至 `deprecated/` 目录。

### 🗑️ 移除与废弃

- 废弃 `vectors.npy` 存储格式（自动迁移至 `.bin`）。

---

## [0.1.3] - 上一个稳定版本

- 初始发布，包含基础双路检索功能。
- 手动 Int8 向量量化。
- 基于 `.npy` 的向量存储。
