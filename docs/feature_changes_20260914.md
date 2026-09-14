# 功能变更与对应代码

## 聊天摘要按增量写入

聊天摘要只提取当前窗口新增或明确变更的长期事实。历史摘要用于理解指代和识别变化，不再把未变化的历史事实重复写入段落、实体和关系。没有新增事实时允许返回空摘要及空实体、关系数组，跳过段落写入；摘要服务通过 `skipped_ids`返回被跳过的外部记录标识。

对应代码：

- [summary_importer.py](../src/A_memorix/core/utils/summary_importer.py)：`SUMMARY_PROMPT_TEMPLATE`、`SummaryImportResult.skipped`、`_build_previous_summary_context`、`_import_from_stream_unlocked`。
- [summary_service.py](../src/A_memorix/core/runtime/services/summary_service.py)：`MemorySummaryService.summarize_chat_stream`，区分实际写入和正常跳过。

## 导入任务自动刷新

导入面板激活且开启自动轮询时，持续按配置间隔刷新任务列表。WebSocket连接成功不再关闭轮询，避免连接正常但进度事件缺失时任务状态停留在旧值；收到进度推送时仍立即刷新。

对应代码：

- [useImportQueue.ts](../dashboard/src/routes/resource/knowledge-base/hooks/useImportQueue.ts)：`useImportQueue`、任务查询的 `enabled`和 `refetchInterval`、进度事件订阅。

## 记忆列表与详情展示知识类型

段落记忆在列表和详情中展示知识类型标签，区分结构化资料、叙事资料、事实资料、引用资料和混合资料。

对应代码：

- [MemoryRecordsTab.tsx](../dashboard/src/routes/resource/knowledge-base/tabs/MemoryRecordsTab.tsx)：`KNOWLEDGE_TYPE_LABELS`、`getKnowledgeTypeLabel`、`RecordButton`和详情标签区域。

## 长期记忆查询工具明确检索模式与时间格式

查询工具说明明确区分语义检索、时间检索、经历检索、综合检索及语义与时间混合检索。`time`和 `hybrid`模式要求至少提供一个时间边界；无时间条件时使用 `search`。时间参数说明明确采用 `YYYY/MM/DD`或 `YYYY/MM/DD HH:mm`格式。

对应代码：

- [query_memory.py](../src/maisaka/builtin_tool/query_memory.py)：`get_tool_spec`中的 `mode`、`time_start`和 `time_end`参数定义。

## 向量通道启动后立即恢复探测

后台任务启动后立即检查是否需要恢复嵌入能力，包括启动自检延后、嵌入服务降级和向量指纹待确认的情况。首次恢复探测不再等待完整轮询周期，后续探测仍按配置间隔执行。

对应代码：

- [background_task_service.py](../src/A_memorix/core/runtime/services/background_task_service.py)：`MemoryBackgroundTaskService._embedding_probe_loop`。

## 摘要与文件导入持久化向量指纹

摘要写入和文件导入保存向量时，统一通过内核保存入口同步持久化实际嵌入模型指纹。独立使用摘要导入器时，根据当前嵌入管理器和向量维度生成指纹后保存。

对应代码：

- [runtime_facade.py](../src/A_memorix/core/runtime/runtime_facade.py)：`KernelRuntimeFacade.persist_vector_store`。
- [summary_importer.py](../src/A_memorix/core/utils/summary_importer.py)：`SummaryImporter._persist_vector_store`和摘要持久化调用。
- [web_import_manager.py](../src/A_memorix/core/utils/web_import_manager.py)：`ImportTaskManager._save_runtime_stores_locked`。

## 纯元数据模式允许导入

允许纯元数据写入时，导入管理器不再因向量存储尚未就绪而拒绝导入。未允许纯元数据写入时，分别检查段落向量池和图向量池，避免只检查旧的单一向量存储属性。

对应代码：

- [web_import_manager.py](../src/A_memorix/core/utils/web_import_manager.py)：`ImportTaskManager._ensure_ready`。

## 人物画像与经历详情保持正确定位

人物画像和Episode列表加载完成后，只在尚未选择目标时选中第一项，保留初始指定目标和用户当前选择。Episode详情请求增加请求序号校验，较早请求的迟到结果和错误提示不再覆盖当前详情；切换目标时清空上一条详情。

对应代码：

- [MemoryProfileManager.tsx](../dashboard/src/components/memory/MemoryProfileManager.tsx)：`loadProfiles`中的函数式选择状态更新。
- [MemoryEpisodeManager.tsx](../dashboard/src/components/memory/MemoryEpisodeManager.tsx)：`loadEpisodes`、`loadDetail`和 `detailRequestIdRef`。

## 可分享记忆包导出

新增 `.amembundle`格式，将已经加工的记忆导出为可安装的结构化文件。知识包包含段落、实体、三元组、知识类型、时间和作用域信息；完整包额外包含所选段落关联的Episode、人物画像快照、画像与别名覆盖、事实账本、外部引用和生命周期状态。包内记录包标识、版本、内容摘要及各类记录数量。

导出内容级别和导出范围分别选择。范围支持全部记忆、指定来源、指定聊天流、指定导入任务和已安装知识包。历史导入任务可以从持久化报告读取来源集合，并标记选择精度为 `source_set`；已安装包通过段落归属映射选择内容。

对应代码：

- [bundle_admin_service.py](../src/A_memorix/core/runtime/services/bundle_admin_service.py)：`_select_paragraphs`、`_paragraph_matches_chat`、`_collect_related_rows`、`_build_knowledge_docs`、`_export_bundle`。
- [metadata_schema.py](../src/A_memorix/core/storage/metadata_schema.py)：`_ensure_knowledge_package_tables`中的 `knowledge_packages`和 `knowledge_package_paragraphs`。

## 记忆包校验与内容检查

读取记忆包时检查ZIP有效性、成员数量、解压总大小、重复成员、必需文件、格式版本和JSON结构。SHA-256清单必须完整覆盖包成员，成员校验值和整体内容摘要均通过后才接受内容。管理接口支持读取包信息、内容级别、各类记录数量和文件体积。

对应代码：

- [bundle_admin_service.py](../src/A_memorix/core/runtime/services/bundle_admin_service.py)：`_resolve_bundle_path`、`_load_bundle`、`_public_bundle_summary`、`memory_bundle_admin`的 `inspect`操作。

## 记忆包安装与作用域映射

安装直接恢复已加工的结构化记忆，不再次调用LLM进行分块、实体抽取或关系抽取。合并安装按内容哈希去重，同一包版本安装到同一范围时返回已有安装。恢复安装只接受完整包，并要求目标记忆库为空。

支持安装到全局或指定真实聊天流。安装到聊天流时，同步映射段落作用域、聊天摘要来源、Episode标识和聊天事实标识，并更新人物画像、事实证据及状态转换中的引用。

对应代码：

- [bundle_admin_service.py](../src/A_memorix/core/runtime/services/bundle_admin_service.py)：`_validate_install_scope`、`_existing_installation`、`_assert_restore_target_empty`、`_insert_knowledge_docs`、`_remap_full_state`、`_insert_full_state`、`_import_bundle`。
- [memory.py](../src/webui/routers/memory.py)：`import_memory_bundle`中的上传处理和真实聊天流校验。

## 已加工知识不重复生成Episode

知识包来源统一使用 `knowledge_pack`类型，该来源不再触发自动Episode生成。完整包携带的Episode直接恢复。

对应代码：

- [profile_policy.py](../src/A_memorix/core/utils/profile_policy.py)：`should_auto_enqueue_episode`。
- [episode_service.py](../src/A_memorix/core/utils/episode_service.py)：Episode配置中的 `disabled_source_types`处理。
- [bundle_admin_service.py](../src/A_memorix/core/runtime/services/bundle_admin_service.py)：`_insert_knowledge_docs`、`_insert_full_state`。

## 记忆包向量复用及单池、双池映射

记忆包可选携带段落向量和图向量。仅在包内嵌入指纹与目标实例实际指纹完全一致时复用，否则在目标实例生成缺失向量。导出和安装分别映射包内的实体、关系向量标识与本地存储标识，兼容单向量池和双向量池；卸载时按实际存储模式删除对应向量。

知识级包的数量统计只包含实际导出的段落、实体和关系，完整包按完整状态统计。

对应代码：

- [bundle_admin_service.py](../src/A_memorix/core/runtime/services/bundle_admin_service.py)：`_export_vector_member`、`_export_bundle`、`_bundle_vectors_compatible`、`_import_vector_member`、`_ensure_imported_vectors`、`_uninstall`。

## 记忆包安装中断清理与安全卸载

语义状态、安装记录和资源归属在同一个数据库事务中提交，图与向量投影完成后才将安装状态改为已安装。投影失败时清理本次安装；同包重试时先清理进程中断留下的未完成安装。

安装记录区分新建和复用的段落、实体、关系、Episode、事实账本及人物画像资源。卸载只删除该安装独占创建的资源，其他包仍在使用的资源保留并转移归属，同时清理对应向量和重建派生索引。

对应代码：

- [metadata_schema.py](../src/A_memorix/core/storage/metadata_schema.py)：`_ensure_knowledge_package_tables`中的 `knowledge_package_resources`。
- [bundle_admin_service.py](../src/A_memorix/core/runtime/services/bundle_admin_service.py)：`_register_installation`、`_set_installation_status`、`_install_bundle_metadata`、`_import_bundle`、`_uninstall`。

## 记忆包管理服务与Web接口

运行时、插件和宿主服务接入记忆包管理能力，支持导出、检查、安装、列出安装记录、解析下载文件和卸载。Web接口提供包文件上传、导出、下载、安装记录查询及卸载。

对应代码：

- [admin_contracts.py](../src/A_memorix/core/runtime/admin_contracts.py)：`_dispatch_memory_bundle_admin`、`_BUNDLE_ACTIONS`、`ADMIN_COMPONENT_SPECS`。
- [sdk_memory_kernel.py](../src/A_memorix/core/runtime/sdk_memory_kernel.py)：`MemoryBundleAdminService`注册和 `memory_bundle_admin`。
- [services/__init__.py](../src/A_memorix/core/runtime/services/__init__.py)：导出 `MemoryBundleAdminService`。
- [plugin.py](../src/A_memorix/plugin.py)：`handle_memory_bundle_admin`。
- [memory_service.py](../src/services/memory_service.py)：`MemoryService.bundle_admin`。
- [memory.py](../src/webui/routers/memory.py)：`export_memory_bundle`、`import_memory_bundle`、`list_memory_bundles`、`download_memory_bundle`、`uninstall_memory_bundle`。
- [memory-api.ts](../dashboard/src/lib/memory-api.ts)：`MemoryBundleContentLevel`、`MemoryBundleSelectorType`、记忆包请求与响应类型，以及 `exportMemoryBundle`、`importMemoryBundle`、`getMemoryBundles`、`uninstallMemoryBundle`、`downloadMemoryBundle`。

## WebUI记忆包导入导出管理

长期记忆原导入页面扩展为导入导出页面，增加导入任务和记忆包导入导出两个内部页签。记忆包页面提供内容级别、导出范围、包名称、包标识、版本和携带向量选项，支持导出下载、上传安装、全局或指定聊天流安装、合并或恢复模式选择，以及已安装包刷新和卸载。

指定来源、导入任务和已安装知识包通过候选下拉列表选择。内部页签使用统一Tabs组件，切换到记忆包页面时隐藏导入任务表单和任务详情。

对应代码：

- [knowledge-base.tsx](../dashboard/src/routes/resource/knowledge-base.tsx)：导入导出入口名称与说明。
- [ImportTab.tsx](../dashboard/src/routes/resource/knowledge-base/tabs/ImportTab.tsx)：`transferMode`、内部 `Tabs`、任务区域显隐和 `MemoryBundleCard`挂载。
- [MemoryBundleCard.tsx](../dashboard/src/routes/resource/knowledge-base/tabs/MemoryBundleCard.tsx)：`selectorPayload`、`refreshExportOptions`、`refreshInstallations`、`handleExport`、`handleInstall`、`handleUninstall`。

## 整体数据导出排除运行锁

整体数据导出不再读取 `data/.a_memorix_runtime_writer.lock`运行锁文件，避免Windows下因文件正被持锁而出现读取失败。

对应代码：

- [data_transfer.py](../src/webui/routers/data_transfer.py)：`_EXCLUDED_EXPORT_PATHS`、`_iter_export_files`。
