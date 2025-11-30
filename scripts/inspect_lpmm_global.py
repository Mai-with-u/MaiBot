import os
import sys
from typing import Set

# 保证可以导入 src.*
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.kg_manager import KGManager
from src.common.logger import get_logger

logger = get_logger("inspect_lpmm_global")


def main() -> None:
    """检查当前整库（所有批次）的向量与 KG 状态，用于观察删除对剩余数据的影响。"""
    em = EmbeddingManager()
    kg = KGManager()

    try:
        em.load_from_file()
        kg.load_from_file()
    except Exception as e:
        logger.error(f"加载当前知识库失败: {e}")
        sys.exit(1)

    # 向量库统计
    para_cnt = len(em.paragraphs_embedding_store.store)
    ent_cnt_vec = len(em.entities_embedding_store.store)
    rel_cnt_vec = len(em.relation_embedding_store.store)

    # KG 统计
    nodes = kg.graph.get_node_list()
    edges = kg.graph.get_edge_list()
    node_set: Set[str] = set(nodes)

    para_nodes = [n for n in nodes if n.startswith("paragraph-")]
    ent_nodes = [n for n in nodes if n.startswith("entity-")]

    print("==== 向量库统计 ====")
    print(f"段落向量条数: {para_cnt}")
    print(f"实体向量条数: {ent_cnt_vec}")
    print(f"关系向量条数: {rel_cnt_vec}")

    print("\n==== KG 图统计 ====")
    print(f"节点总数: {len(nodes)}")
    print(f"边总数: {len(edges)}")
    print(f"段落节点数: {len(para_nodes)}")
    print(f"实体节点数: {len(ent_nodes)}")

    # ent_appear_cnt 状态
    ent_cnt_meta = len(kg.ent_appear_cnt)
    print(f"\n实体计数表条目数: {ent_cnt_meta}")

    # 抽样查看剩余段落/实体内容
    print("\n==== 剩余段落示例（最多 3 条） ====")
    for nid in para_nodes[:3]:
        nd = kg.graph[nid]
        content = nd["content"] if "content" in nd else nid
        print(f"- {nid}: {content[:80]}")

    print("\n==== 剩余实体示例（最多 5 条） ====")
    for nid in ent_nodes[:5]:
        nd = kg.graph[nid]
        content = nd["content"] if "content" in nd else nid
        print(f"- {nid}: {content[:80]}")


if __name__ == "__main__":
    main()

