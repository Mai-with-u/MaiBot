# 人物身份合并与数据迁移

将同一平台下一个用户 ID 的数据并入另一个 ID，用于 QQ 号、OpenID 等已确认属于同一人的身份切换。先完成迁移，再让适配器使用新 ID；本功能本身不识别两个账号是否属于同一人。

## 范围

迁移只处理影响后续对话的人物资料与当前长期记忆：

- 人物资料、称呼、人工设置和记忆要点；同名字段冲突逐项确认。
- 每个人最新一版画像，以及画像刷新任务；历史画像版本保留原样。
- 有效的人物事实及证据、未过期的人物事实段落。事实继承给目标人物，原事实与证据保留，供历史画像及变更记录引用。矛盾事实保留双方并标记冲突。
- 不重写记忆正文，不重新计算向量。
- 来源身份登记为目标别名，仅对后续消息生效。

历史消息、工具调用、旧私聊会话、统计、表达、黑话、情景历史、删除记录与维护任务均不迁移，也不参与预览扫描。旧会话及消息仍归属原 ID。来源必须有可迁移的人物资料，不能仅凭旧消息启动迁移。

第三方插件只有明确登记的迁移扩展才参与处理。

预览和执行均在线进行，不停止或重启 Bot。执行时在参与数据库的写事务内重新生成计划，纳入预览后的新增数据；已确认的冲突内容变化时要求重新确认。写入异常回滚当前事务，身份别名与主库数据一同提交。

实例配置、插件配置和权限名单不由数据迁移修改；使用用户 ID 的名单需按新身份调整。

## 使用

在源码部署环境中使用 `uv run --no-sync python -m src.cli.tool`；安装版可直接使用 `maibot`。`--directory` 指定单个 **Bot 实例目录**，不是包含多个 Bot 的工作区目录。

生成预览：

```powershell
uv run --no-sync python -m src.cli.tool person-migrate --directory C:/Bots/mai preview --platform qq --source OLD_ID --target NEW_ID --output C:/Temp/person-plan.json
```

命令生成完整的 `person-plan.json`，以及便于检查的 `person-plan.review.json`。后者包含各库、各表的变更数量和逐字段冲突。来源、目标 ID 可以是 QQ 号或 OpenID，目标身份尚未建档也可以迁入。只允许同一平台内合并。

存在冲突时，单独创建 `decisions.json`，为 review 中每个冲突的 `id` 明确选择 `source` 或 `target`，例如：

```json
{
  "从 review 复制的冲突 ID": "source",
  "另一个冲突 ID": "target"
}
```

带选择重新生成计划：

```powershell
uv run --no-sync python -m src.cli.tool person-migrate --directory C:/Bots/mai preview --platform qq --source OLD_ID --target NEW_ID --decisions C:/Temp/decisions.json --output C:/Temp/person-plan.json
```

当输出“全部确认: True”时执行；没有冲突的预览可直接执行：

```powershell
uv run --no-sync python -m src.cli.tool person-migrate --directory C:/Bots/mai execute --plan C:/Temp/person-plan.json
```

不要直接编辑完整计划；修改决定后重新预览。配置、身份别名或冲突内容变化时需要重新确认。成功时输出执行记录目录 `data/person-migrations/<任务ID>/`，重复执行同一计划会被拒绝。

进程意外退出后可核对任务提交状态：

```powershell
uv run --no-sync python -m src.cli.tool person-migrate --directory C:/Bots/mai recover --job C:/Bots/mai/data/person-migrations/JOB_ID
```

`recover` 根据每个参与库的提交标记更新任务记录，不覆盖在线数据。SQLite 的多个 WAL 数据库在断电时不保证跨库原子性；若提交状态不一致会明确报错，需要依据任务记录修复。

长期记忆会检查当前实例配置的 `a_memorix.storage.data_dir`，以及默认和旧版数据目录。即使记忆未启用，只要数据库存在也会迁移。实例外的记忆库、涉及来源身份且尚未完成的记忆写入任务会明确报错，需要先处理后再迁移。

## 插件迁移扩展

Python 服务接口位于 `src.services.person_migration_service`，提供 `preview`、`execute`、`recover` 和 `MigrationExtension`。扩展工厂接收实例目录，返回自己的数据库路径和计划生成函数；扩展库必须位于实例内，不能重复登记。

```python
from pathlib import Path
from typing import Any, Dict

from src.services.person_migration_plan import DatabasePlan
from src.services.person_migration_service import MigrationExtension


def build(plan: DatabasePlan, context: Dict[str, Any]) -> None:
    def transform(row: Dict[str, Any]) -> Dict[str, Any]:
        if row["platform"] == context["platform"]:
            row["user_id"] = context["user_map"].get(row["user_id"], row["user_id"])
        return row
    plan.transform("my_user_records", transform)


def create_extension(root: Path) -> MigrationExtension:
    return MigrationExtension("my-plugin", root / "data/plugins/my-plugin/records.db", build)
```

这是运行于迁移调用进程的扩展模块，不在 SDK 插件 Runner 内执行。通过 `preview --extension my_module:create_extension` 加载，可重复指定多个扩展。生成的变更自动进入同一份预览、冲突确认、过期检查、事务和任务记录流程。特殊计数或唯一键合并语义应通过 `plan.transform(..., merge=...)` 显式实现，不应忽略冲突或直接写数据库。

A_Memorix 的迁移实现先在上游 `MaiBot_branch` 工作副本开发，再同步到 `src/A_memorix/core/person_migration.py`；不需要更改向量存储格式。

## 用户数据灌注

主程序「数据」页面的「用户数据灌注」标签页支持将 QQ 平台来源用户的数据合并到指定目标用户。填写用户 ID（非 person_id），在线查看预览并逐项确认冲突，确认后在线合并。该操作调用本体 `preview` / `execute`，保留目标数据并合并来源数据，来源身份登记为目标别名，后续消息也归到目标身份。

宿主 `POST /api/webui/person-migrations/user-injection/preview` 接收 `source`、`target`、`decisions`，执行和取消复用 `/execute`、`/cancel`。接口经宿主按 Bot 路由并鉴权，可以查看、确认或取消持久化预览。

QQ 官方适配器的「用户映射」可选择 QQ 号及对应 OpenID，跳转本页预填来源和目标。迁移完成后返回进度页；全部处理完成后显式关闭 QQ 号映射。关闭前由适配器重新检查目标别名和来源人物资料，存在未迁移项或冲突时拒绝关闭。
