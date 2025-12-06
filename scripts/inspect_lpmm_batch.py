import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Tuple

# 确保能导入 src.*
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.knowledge.utils.hash import get_sha256
from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.kg_manager import KGManager
from src.common.logger import get_logger

logger = get_logger("inspect_lpmm_batch")


def load_openie_hashes(path: Path) -> Tuple[List[str], List[str], List[str]]:
    """从 OpenIE JSON 中提取段落 / 实体 / 关系的哈希

    注意：实体既包括 extracted_entities 中的条目，也包括三元组中的主语/宾语，
    以与 KG 构图逻辑保持一致。
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    pg_hashes: List[str] = []
    ent_hashes: List[str] = []
    rel_hashes: List[str] = []

    for doc in data.get("docs", []):
        if not isinstance(doc, dict):
            continue
        idx = doc.get("idx")
        if isinstance(idx, str) and idx.strip():
            pg_hashes.append(idx.strip())

        ents = doc.get("extracted_entities", [])
        if isinstance(ents, list):
            for e in ents:
                if isinstance(e, str):
                    ent_hashes.append(get_sha256(e))

        triples = doc.get("extracted_triples", [])
        if isinstance(triples, list):
            for t in triples:
                if isinstance(t, list) and len(t) == 3:
                    # 主语/宾语作为实体参与构图
                    subj, _, obj = t
                    if isinstance(subj, str):
                        ent_hashes.append(get_sha256(subj))
                    if isinstance(obj, str):
                        ent_hashes.append(get_sha256(obj))
                    rel_hashes.append(get_sha256(str(tuple(t))))

    # 去重但保留顺序
    def unique(seq: List[str]) -> List[str]:
        seen = set()
        return [x for x in seq if not (x in seen or seen.add(x))]

    return unique(pg_hashes), unique(ent_hashes), unique(rel_hashes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="检查指定 OpenIE 文件对应批次在当前向量库与 KG 中的存在情况（用于验证删除效果）。"
    )
    parser.add_argument("--openie-file", required=True, help="OpenIE 输出 JSON 文件路径")
    args = parser.parse_args()

    openie_path = Path(args.openie_file)
    if not openie_path.exists():
        logger.error(f"OpenIE 文件不存在: {openie_path}")
        sys.exit(1)

    pg_hashes, ent_hashes, rel_hashes = load_openie_hashes(openie_path)
    logger.info(
        f"从 {openie_path.name} 解析到 段落 {len(pg_hashes)} 条，实体 {len(ent_hashes)} 个，关系 {len(rel_hashes)} 条"
    )

    # 加载当前嵌入与 KG
    em = EmbeddingManager()
    kg = KGManager()
    try:
        em.load_from_file()
        kg.load_from_file()
    except Exception as e:
        logger.error(f"加载当前知识库失败: {e}")
        sys.exit(1)

    graph_nodes = set(kg.graph.get_node_list())

    # 检查段落
    pg_keys = [f"paragraph-{h}" for h in pg_hashes]
    pg_in_vec = sum(1 for k in pg_keys if k in em.paragraphs_embedding_store.store)
    pg_in_kg = sum(1 for k in pg_keys if k in graph_nodes)

    # 检查实体
    ent_keys = [f"entity-{h}" for h in ent_hashes]
    ent_in_vec = sum(1 for k in ent_keys if k in em.entities_embedding_store.store)
    ent_in_kg = sum(1 for k in ent_keys if k in graph_nodes)

    # 检查关系（只针对向量库）
    rel_keys = [f"relation-{h}" for h in rel_hashes]
    rel_in_vec = sum(1 for k in rel_keys if k in em.relation_embedding_store.store)

    print("==== 批次存在情况（删除前/后对比用） ====")
    print(f"段落: 总计 {len(pg_keys)}, 向量库剩余 {pg_in_vec}, KG 中剩余 {pg_in_kg}")
    print(f"实体: 总计 {len(ent_keys)}, 向量库剩余 {ent_in_vec}, KG 中剩余 {ent_in_kg}")
    print(f"关系: 总计 {len(rel_keys)}, 向量库剩余 {rel_in_vec}")

    # 打印少量仍存在的样例，便于检查内容是否正常
    sample_pg = [k for k in pg_keys if k in graph_nodes][:3]
    if sample_pg:
        print("\n仍在 KG 中的段落节点示例：")
        for k in sample_pg:
            nd = kg.graph[k]
            content = nd["content"] if "content" in nd else k
            print(f"- {k}: {content[:80]}")

    sample_ent = [k for k in ent_keys if k in graph_nodes][:3]
    if sample_ent:
        print("\n仍在 KG 中的实体节点示例：")
        for k in sample_ent:
            nd = kg.graph[k]
            content = nd["content"] if "content" in nd else k
            print(f"- {k}: {content[:80]}")


if __name__ == "__main__":
    main()
