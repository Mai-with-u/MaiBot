# A_Memorix 修改规定

## 目的

`src/A_memorix` 是上游 `A_memorix` 仓库在 MaiBot 内的同步目录。

这个目录允许包含面向 MaiBot 的耦合实现，但这些耦合的归属应当属于上游
`MaiBot_branch`，而不是在 MaiBot 仓库内长期各自演化的私有改动。

本文件用于明确 `src/A_memorix` 目录下的修改边界。

## 事实来源

- 上游仓库：`https://github.com/A-Dawn/A_memorix.git`
- 上游对接分支：`MaiBot_branch`
- MaiBot 内同步前缀：`src/A_memorix`

基本原则：

- 如果改动属于 A_Memorix 的业务逻辑、内部实现或对 MaiBot 的耦合实现，应优先提交到上游 `MaiBot_branch`。
- 如果改动只属于 MaiBot 的加载、运行时接入、WebUI 接入、配置接入或测试接入，应在 MaiBot 仓库内完成。

## 可直接在 MaiBot 仓库修改的范围

以下内容默认由 MaiBot 仓库直接维护：

- `src/services/memory_service.py`
- `src/webui/routers/memory.py`
- `dashboard/src/routes/resource/knowledge-base.tsx`
- `dashboard/src/routes/resource/__tests__/knowledge-base.test.tsx`
- `dashboard/src/routes/resource/knowledge-graph/`
- `dashboard/src/lib/memory-api.ts`
- `config/a_memorix.toml`
- `data/a-memorix/`
- `data/plugins/a-dawn.a-memorix/`（旧脚本默认路径）
- `data/a-memorix/imports/`（文件来源、上传暂存、任务状态与转换结果）
- `pytests/A_memorix_test/`
- 同步脚本与同步文档，例如 `scripts/sync_a_memorix_subtree.sh`

这些内容属于 MaiBot 侧接入层。

常见例子：

- 调整 `src/services/memory_service.py` 中 A_Memorix 的宿主调用封装
- 修改 `src/webui/routers/memory.py` 中对 A_Memorix 的 API 暴露方式
- 修改 dashboard 中对 A_Memorix 图谱页、控制台页的展示与交互
- 调整 `config/a_memorix.toml` 的默认配置项
- 增补 `pytests/A_memorix_test/` 中用于验证 MaiBot 集成行为的测试
- 修改同步文档、同步脚本、接入说明和迁移说明

## 原则上应先在上游修改的范围

以下内容原则上应先在上游 `MaiBot_branch` 修改，再同步回 MaiBot：

- `src/A_memorix/core/`
- `src/A_memorix/scripts/`
- `src/A_memorix/plugin.py`
- `src/A_memorix/paths.py`
- `src/A_memorix/runtime_registry.py`
- `src/A_memorix/README.md` 及其他描述包行为的文档

这类改动包括但不限于：

- 新功能开发
- 行为变更
- 数据模型变更
- 存储与检索逻辑变更
- A_Memorix 内部的 MaiBot 耦合变更

## 允许的本地例外

在以下情况下，允许直接在 `src/A_memorix` 下做本地修改：

- 需要解决同步冲突，以保证 MaiBot 可以构建、启动或测试
- 需要紧急修复，以解除 MaiBot 当前开发或发布阻塞
- 需要临时兼容补丁，而对应改动尚未同步进入上游

出现上述情况时，应遵循以下约束：

- 补丁尽量小
- 在提交说明或 PR 描述中写明为什么需要本地补丁
- 条件允许时，尽快把同等改动提交到上游 `MaiBot_branch`

## 实操判断规则

在修改 `src/A_memorix` 前，先问两个问题：

1. 这个改动是否属于 A_Memorix 的行为或内部实现？
2. 如果 MaiBot 不存在，这个改动是否仍然应属于 A_Memorix 的 MaiBot 对接分支？

如果答案是“是”，原则上应先改上游。

如果这个改动只影响 MaiBot 如何加载、配置、展示、测试或包装 A_Memorix，
则应留在 MaiBot 仓库内。

## 目标

本规定不是为了完全禁止本地修改，而是为了明确归属：

- MaiBot 拥有接入层。
- 上游 `A_memorix` 拥有实现层，包括面向 MaiBot 的对接分支实现。
