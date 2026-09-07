# 表达选择评测脚本

这个目录只保留表达选择评测相关的核心入口。

## 1. LLM 表达选择评测器

```powershell
uv run python scripts/expression_selection/llm_judge.py `
  --input-json data/analysis/expression_selection_batch_compare_full_pipeline_20260622_173330.json `
  --llm-task-name utils `
  --model-name deepseek-v4f `
  --max-tokens 512
```

作用：读取三方案 batch，随机盲化为 A/B/C，让 LLM 评估排序和打分。

## 2. 三种表达选择器离线运行器

```powershell
uv run python scripts/expression_selection/offline_runner.py `
  --input-json data/analysis/expression_selection_batch_compare_live_intent_20260622_164359.json `
  --limit 30 `
  --selector-task-name planner `
  --selector-max-tokens 4096 `
  --vector-pool-size 50
```

作用：构建完整链路 batch：

- `legacy_precise`：legacy 候选池 + 精细选择器
- `vector_no_intent_precise`：不带 intent 的向量候选池 + 精细选择器
- `vector_intent_online`：live-log 中真实线上 vector_intent 结果

## 3. 辅助工具

构建表达向量索引：

```powershell
uv run python scripts/expression_selection/vector_index_tools.py build-index --clusters 80
```

刷新与当前 embedding profile 不一致的表达：

```powershell
uv run python scripts/expression_selection/vector_index_tools.py refresh-profile --limit 200
```

不写子命令时默认执行 `build-index`。

## 4. 聚类数量人工盲评

```powershell
uv run python scripts/expression_selection/human_cluster_trial.py --data-root C:/GitHub/MaiBot-dev/MaiBot-dev/data --output data/analysis/human_cluster_trial
```

打开输出目录中的 `review.html`，按气泡形式的真实聊天判断三个并列方案，点选更适合的方案即可，也可选择并列或都不合适。详细评分改为可选按钮，备注可不填，最后导出评分 JSON。A固定为80簇基准，B/C随机排列；绿色表示相对A前10条新增的表达，灰色表示相同表达，红色直接展示本方案前10条缺少的基准表达，按表达ID比较，不代表质量优劣。`answer_key.json` 保存参数映射和抽样表达 ID。新版使用独立的本地评分存储，不覆盖旧版评分。

实验使用六条历史真实回复上下文，随机模拟 500、2,000、8,000 条表达库和当前全量库；比较 20、80、320 簇。小库使用两个固定随机种子，规模之间采用嵌套无放回抽样。同一题内表达库、查询向量、聚类初始化种子、取 16 簇和 MMR 取 50 条规则保持一致。

脚本通过 AST 提取生产代码的纯 K-means 与 MMR 方法运行，不导入数据库和服务。实验模拟全局共享表达库，不重放历史会话权限；复用历史 intent + planner 查询缓存，模型名及维度一致，但旧缓存没有向量空间探针可供验证。结果用于比较当前召回流程下的簇数，不能直接推断大于现有表达库规模的最优簇数。页面代表表达仅是中心最近的五条，不能代替对全部簇成员的一致性检查。
