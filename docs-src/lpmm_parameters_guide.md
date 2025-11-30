# LPMM 关键参数调节指南（进阶版）

> 本文是对 `config/bot_config.toml` 中 `[lpmm_knowledge]` 段的补充说明。  
> 如果你只想使用默认配置，可以不改这些参数，脚本仍然可以正常工作。
>
> 重要提醒：无论是修改 `[lpmm_knowledge]` 段的参数，还是通过脚本导入 / 删除 LPMM 知识库数据，主程序都需要重启（或在内部调用一次 `lpmm_start_up()`）后，新的参数和知识才会真正生效到聊天侧。

所有与 LPMM 相关的参数，都集中在：

```toml
[lpmm_knowledge] # lpmm知识库配置
enable = true
lpmm_mode = "agent"
...
```

下面按功能将常用参数分为三组介绍。

---

## 一、检索相关参数（影响答案质量与风格）

```toml
qa_relation_search_top_k = 10      # 关系检索TopK
qa_relation_threshold    = 0.5     # 关系阈值，相似度高于该值才认为“命中关系”
qa_paragraph_search_top_k = 1000   # 段落检索TopK，越小可能影响召回
qa_paragraph_node_weight = 0.05    # 段落节点权重，在图检索&PPR中的权重
qa_ent_filter_top_k      = 10      # 实体过滤TopK
qa_ppr_damping           = 0.8     # PPR阻尼系数
qa_res_top_k             = 3       # 最终提供给问答模型的段落数
```

- `qa_relation_search_top_k`  
  控制“最多考虑多少条关系向量候选”。  
  - 数值大：召回更全面，但略慢；  
  - 数值小：更快，可能遗漏部分隐含关系。

- `qa_relation_threshold`  
  关系相似度的阈值：  
  - 数值高：只信任非常相关的关系，系统更可能退化为纯段落向量检索；  
  - 数值低：图结构影响更大，适合实体关系较丰富的场景。

- `qa_paragraph_search_top_k`  
  控制“最多考虑多少段落候选”。  
  - 太小：可能召回不全，导致答案缺失；  
  - 太大：略微增加计算量，一般 1000 为安全默认。

- `qa_paragraph_node_weight`  
  文段节点在图检索中的权重：  
  - 数值大：更依赖段落向量相似度（传统向量检索）；  
  - 数值小：更依赖图结构和实体网络。

- `qa_ppr_damping`  
  Personalized PageRank 的阻尼系数：  
  - 通常保持在 0.8 左右即可；  
  - 越接近 1：偏向长路径探索，结果更发散；  
  - 略低：更集中在与问题直接相关的节点附近。

- `qa_res_top_k`  
  LPMM 最终会把相关度最高的前 `qa_res_top_k` 条段落组合成“知识上下文”给问答模型。  
  - 太多：增加模型负担、阅读更多文字；  
  - 太少：信息不够充分，一般 3–5 比较平衡。

> 调参建议：  
> - 优先在 `qa_relation_threshold`、`qa_paragraph_node_weight` 上做小幅调整；  
> - 每次调整后，用 `scripts/test_lpmm_retrieval.py` 跑一遍固定问题，感受回答变化。

---

## 二、性能与硬件相关参数

```toml
embedding_dimension   = 1024  # 嵌入向量维度,应与模型输出维度一致
max_embedding_workers = 12    # 嵌入/抽取并发线程数
embedding_chunk_size  = 16    # 每批嵌入的条数
info_extraction_workers = 3   # 实体抽取同时执行线程数
enable_ppr            = true  # 是否启用PPR，低配机器可关闭
ppr_node_cap          = 8000  # 图节点数超过该值时自动跳过PPR
ppr_relation_cap      = 50    # 命中关系数超过该值时自动跳过PPR
```

- `embedding_dimension`  
  必须与所选嵌入模型的输出维度一致（比如 768、1024 等）。**不要随意修改，除非你知道你在做什么！！！**

- `max_embedding_workers`  
  决定导入/抽取阶段的并行线程数：  
  - 机器配置好：可以适当调大，加快导入速度；  
  - 机器配置弱：建议调低（如 2 或 4），避免 CPU 长时间 100%。

- `embedding_chunk_size`  
  每批发送给嵌入 API 的段落数量：  
  - 数值大：请求次数少，但单次请求更“重”；  
  - 数值小：请求次数多，但对网络和 API 的单次压力小。

- `info_extraction_workers`  
  `scripts/info_extraction.py` 中实体抽取的并行线程数：  
  - 使用 Pro/贵价模型时建议不要太大，避免并行费用过高；
  - 一般 2–4 就能取得较好平衡。

- `enable_ppr`  
  是否启用个性化 PageRank（PPR）图检索：  
  - `true`：检索会结合向量+知识图，效果更好，但略慢；  
  - `false`：只用向量检索，牺牲一定效果，性能更稳定。

- `ppr_node_cap` / `ppr_relation_cap`  
  安全阈值：当图节点数或命中关系数超过阈值时自动跳过 PPR，以避免“大图”导致卡顿。

> 调参建议：  
> - 若导入/检索阶段机器明显“顶不住”（>=1MB的大文本，且分配配置<4C），优先调低：  
>   - `max_embedding_workers`  
>   - `embedding_chunk_size`  
>   - `info_extraction_workers`  
>   - 或暂时将 `enable_ppr = false`  （除非真的出现问题，否则不建议禁用此项，大幅影响检索效果）
> - 调整后重新执行导入或检索，观察日志与系统资源占用。

> 小提示：每次大改参数或批量删除知识后，建议用  
> - `scripts/test_lpmm_retrieval.py` 看回答风格是否如预期；  
> - 如需确认当前磁盘数据能否正常初始化，可执行 `scripts/refresh_lpmm_knowledge.py` 做一次快速自检。

---

## 三、开启/关闭 LPMM 与模式说明

```toml
enable    = true       # 是否开启lpmm知识库
lpmm_mode = "agent"    # 可选 classic / agent
```

- `enable`  
  - `true`：LPMM 知识库启用，检索和问答会使用知识库；  
  - `false`：LPMM 完全关闭，脚本仍可导入/删除数据，但对聊天问答不生效。

- `lpmm_mode`  
  - `classic`：传统模式，仅使用 LPMM 知识库本身；  
  - `agent`：与新的记忆系统联动，用于更复杂的记忆+知识混合场景。

> 修改 `enable` 或 `lpmm_mode` 后，需要重启主程序，让配置生效。

---

## 四、推荐的调参流程

1. **保持默认配置，先跑一轮完整流程**
   - 导入 → `inspect_lpmm_global.py` → `test_lpmm_retrieval.py`；
   - 记录当前“答案风格”和“响应速度”。

2. **每次只调整一到两个参数**
   - 例如先调 `qa_relation_threshold`、`qa_paragraph_node_weight`；  
   - 或在性能不佳时调整 `max_embedding_workers`、`enable_ppr`。

3. **调整后重复同一组测试问题**
   - 使用 `scripts/test_lpmm_retrieval.py`；  
   - 对比不同配置下的答案，选择更符合需求的组合。

4. **出现“怎么调都不对”时**
   - 将 `[lpmm_knowledge]` 段恢复为仓库中的默认配置；  
   - 重启主程序，即可回到“出厂设置”。

通过本指南中的参数调节，你可以在“检索质量”“响应速度”“系统资源占用”之间找到适合自己麦麦和机器的平衡点！

