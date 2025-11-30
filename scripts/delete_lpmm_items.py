import argparse
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any
import json
import os

# 强制使用 utf-8，避免控制台编码报错
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 确保能找到 src 包
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.kg_manager import KGManager
from src.common.logger import get_logger
from src.chat.knowledge.utils.hash import get_sha256

logger = get_logger("delete_lpmm_items")


def read_hashes(file_path: Path) -> List[str]:
    """读取哈希列表，跳过空行"""
    hashes: List[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        val = line.strip()
        if not val:
            continue
        hashes.append(val)
    return hashes


def read_openie_hashes(file_path: Path) -> List[str]:
    """从 OpenIE JSON 中提取 idx 作为段落哈希"""
    data: Dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
    docs = data.get("docs", []) if isinstance(data, dict) else []
    hashes: List[str] = []
    for doc in docs:
        idx = doc.get("idx") if isinstance(doc, dict) else None
        if isinstance(idx, str) and idx.strip():
            hashes.append(idx.strip())
    return hashes


def normalize_paragraph_keys(raw_hashes: List[str]) -> Tuple[List[str], List[str]]:
    """将输入规范为完整键和纯哈希两份列表"""
    keys: List[str] = []
    hashes: List[str] = []
    for h in raw_hashes:
        if h.startswith("paragraph-"):
            keys.append(h)
            hashes.append(h.replace("paragraph-", "", 1))
        else:
            keys.append(f"paragraph-{h}")
            hashes.append(h)
    return keys, hashes


def main():
    parser = argparse.ArgumentParser(description="Delete paragraphs from LPMM knowledge base (vectors + graph).")
    parser.add_argument("--hash-file", help="文本文件路径，每行一个 paragraph 哈希或带前缀键")
    parser.add_argument("--openie-file", help="OpenIE 输出文件（JSON），将其 docs.idx 作为待删段落哈希")
    parser.add_argument("--raw-file", help="原始 txt 语料文件（按空行分段），可结合 --raw-index 使用")
    parser.add_argument(
        "--raw-index",
        help="在 --raw-file 中要删除的段落索引，1 基，支持逗号分隔，例如 1,3",
    )
    parser.add_argument("--search-text", help="在当前段落库中按子串搜索匹配段落并交互选择删除")
    parser.add_argument(
        "--search-limit",
        type=int,
        default=10,
        help="--search-text 模式下最多展示的候选段落数量",
    )
    parser.add_argument("--delete-entities", action="store_true", help="同时删除 OpenIE 文件中的实体节点/嵌入")
    parser.add_argument("--delete-relations", action="store_true", help="同时删除 OpenIE 文件中的关系嵌入")
    parser.add_argument("--remove-orphan-entities", action="store_true", help="删除删除后孤立的实体节点")
    parser.add_argument("--dry-run", action="store_true", help="仅预览将删除的项，不实际修改")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认，直接执行删除（谨慎使用）")
    parser.add_argument(
        "--max-delete-nodes",
        type=int,
        default=2000,
        help="单次最大允许删除的节点数量（段落+实体），超过则需要显式确认或调整该参数",
    )
    args = parser.parse_args()

    # 至少需要一种来源
    if not (args.hash_file or args.openie_file or args.raw_file or args.search_text):
        logger.error("必须指定 --hash-file / --openie-file / --raw-file / --search-text 之一")
        sys.exit(1)

    raw_hashes: List[str] = []
    raw_entities: List[str] = []
    raw_relations: List[str] = []

    if args.hash_file:
        hash_file = Path(args.hash_file)
        if not hash_file.exists():
            logger.error(f"哈希文件不存在: {hash_file}")
            sys.exit(1)
        raw_hashes.extend(read_hashes(hash_file))

    if args.openie_file:
        openie_path = Path(args.openie_file)
        if not openie_path.exists():
            logger.error(f"OpenIE 文件不存在: {openie_path}")
            sys.exit(1)
        # 段落
        raw_hashes.extend(read_openie_hashes(openie_path))
        # 实体/关系（实体同时包含 extracted_entities 与三元组主语/宾语，以匹配 KG 构图逻辑）
        try:
            data = json.loads(openie_path.read_text(encoding="utf-8"))
            docs = data.get("docs", []) if isinstance(data, dict) else []
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                ents = doc.get("extracted_entities", [])
                if isinstance(ents, list):
                    raw_entities.extend([e for e in ents if isinstance(e, str)])
                triples = doc.get("extracted_triples", [])
                if isinstance(triples, list):
                    for t in triples:
                        if isinstance(t, list) and len(t) == 3:
                            subj, _, obj = t
                            if isinstance(subj, str):
                                raw_entities.append(subj)
                            if isinstance(obj, str):
                                raw_entities.append(obj)
                            raw_relations.append(str(tuple(t)))
        except Exception as e:
            logger.error(f"读取 OpenIE 文件失败: {e}")
            sys.exit(1)

    # 从原始 txt 语料按段落索引选择删除
    if args.raw_file:
        raw_path = Path(args.raw_file)
        if not raw_path.exists():
            logger.error(f"原始语料文件不存在: {raw_path}")
            sys.exit(1)
        text = raw_path.read_text(encoding="utf-8")
        paragraphs: List[str] = []
        buf = []
        for line in text.splitlines():
            if line.strip() == "":
                if buf:
                    paragraphs.append("\n".join(buf).strip())
                    buf = []
            else:
                buf.append(line)
        if buf:
            paragraphs.append("\n".join(buf).strip())

        if not paragraphs:
            logger.error(f"原始语料文件 {raw_path} 中没有解析到任何段落")
            sys.exit(1)

        if not args.raw_index:
            logger.info(f"{raw_path} 共解析出 {len(paragraphs)} 个段落，请通过 --raw-index 指定要删除的段落，例如 --raw-index 1,3")
            sys.exit(1)

        # 解析索引列表（1-based）
        try:
            idx_list = [int(x.strip()) for x in str(args.raw_index).split(",") if x.strip()]
        except ValueError:
            logger.error(f"--raw-index 解析失败: {args.raw_index}")
            sys.exit(1)

        for idx in idx_list:
            if idx < 1 or idx > len(paragraphs):
                logger.error(f"--raw-index 包含无效索引 {idx}（有效范围 1~{len(paragraphs)}）")
                sys.exit(1)

        logger.info("根据原始语料选择段落：")
        for idx in idx_list:
            para = paragraphs[idx - 1]
            h = get_sha256(para)
            logger.info(f"- 第 {idx} 段，hash={h}，内容预览：{para[:80]}")
            raw_hashes.append(h)

    # 在现有库中按子串搜索候选段落并交互选择
    if args.search_text:
        search_text = args.search_text.strip()
        if not search_text:
            logger.error("--search-text 不能为空")
            sys.exit(1)
        logger.info(f"正在根据关键字在现有段落库中搜索：{search_text!r}")
        em_search = EmbeddingManager()
        try:
            em_search.load_from_file()
        except Exception as e:
            logger.error(f"加载嵌入库失败，无法使用 --search-text 功能: {e}")
            sys.exit(1)

        candidates = []
        for key, item in em_search.paragraphs_embedding_store.store.items():
            if search_text in item.str:
                candidates.append((key, item.str))
            if len(candidates) >= args.search_limit:
                break

        if not candidates:
            logger.info("未在现有段落库中找到包含该关键字的段落")
        else:
            logger.info("找到以下候选段落（输入序号选择要删除的条目，可用逗号分隔，多选）：")
            for i, (key, text) in enumerate(candidates, start=1):
                logger.info(f"{i}. {key} | {text[:80]}")
            choice = input("请输入要删除的序号列表（如 1,3），或直接回车取消：").strip()
            if choice:
                try:
                    idxs = [int(x.strip()) for x in choice.split(",") if x.strip()]
                except ValueError:
                    logger.error("输入的序号列表无法解析，已取消 --search-text 删除")
                else:
                    for i in idxs:
                        if 1 <= i <= len(candidates):
                            key, _ = candidates[i - 1]
                            # key 已是完整的 paragraph-xxx
                            if key.startswith("paragraph-"):
                                raw_hashes.append(key.split("paragraph-", 1)[1])
                        else:
                            logger.warning(f"忽略无效序号: {i}")

    # 去重但保持顺序
    seen = set()
    raw_hashes = [h for h in raw_hashes if not (h in seen or seen.add(h))]

    if not raw_hashes:
        logger.error("未读取到任何待删哈希，无操作")
        sys.exit(1)

    keys, pg_hashes = normalize_paragraph_keys(raw_hashes)

    ent_hashes: List[str] = []
    rel_hashes: List[str] = []
    if args.delete_entities and raw_entities:
        ent_hashes = [get_sha256(e) for e in raw_entities]
    if args.delete_relations and raw_relations:
        rel_hashes = [get_sha256(r) for r in raw_relations]

    logger.info("=== 删除操作预备 ===")
    logger.info("请确保已备份 data/embedding 与 data/rag，必要时可使用 --dry-run 预览")
    logger.info(f"待删除段落数量: {len(keys)}")
    logger.info(f"示例: {keys[:5]}")
    if ent_hashes:
        logger.info(f"待删除实体数量: {len(ent_hashes)}")
    if rel_hashes:
        logger.info(f"待删除关系数量: {len(rel_hashes)}")

    total_nodes_to_delete = len(pg_hashes) + (len(ent_hashes) if args.delete_entities else 0)
    logger.info(f"本次预计删除节点总数（段落+实体）: {total_nodes_to_delete}")

    if args.dry_run:
        logger.info("dry-run 模式，未执行删除")
        return

    # 大批次删除保护
    if total_nodes_to_delete > args.max_delete_nodes and not args.yes:
        logger.error(
            f"本次预计删除节点 {total_nodes_to_delete} 个，超过阈值 {args.max_delete_nodes}。"
            " 为避免误删，请降低批次规模或使用 --max-delete-nodes 调整阈值，并加上 --yes 明确确认。"
        )
        sys.exit(1)

    # 交互确认
    if not args.yes:
        confirm = input("确认删除上述数据？输入大写 YES 以继续，其他任意键取消: ").strip()
        if confirm != "YES":
            logger.info("用户取消删除操作")
            return

    # 加载嵌入与图
    embed_manager = EmbeddingManager()
    kg_manager = KGManager()

    try:
        embed_manager.load_from_file()
        kg_manager.load_from_file()
    except Exception as e:
        logger.error(f"加载现有知识库失败: {e}")
        sys.exit(1)

    # 记录删除前全局统计，便于对比
    before_para_vec = len(embed_manager.paragraphs_embedding_store.store)
    before_ent_vec = len(embed_manager.entities_embedding_store.store)
    before_rel_vec = len(embed_manager.relation_embedding_store.store)
    before_nodes = len(kg_manager.graph.get_node_list())
    before_edges = len(kg_manager.graph.get_edge_list())
    logger.info(
        f"删除前统计: 段落向量={before_para_vec}, 实体向量={before_ent_vec}, 关系向量={before_rel_vec}, "
        f"KG节点={before_nodes}, KG边={before_edges}"
    )

    # 删除向量
    deleted, skipped = embed_manager.paragraphs_embedding_store.delete_items(keys)
    embed_manager.stored_pg_hashes = set(embed_manager.paragraphs_embedding_store.store.keys())
    logger.info(f"段落向量删除完成，删除: {deleted}, 跳过: {skipped}")
    ent_deleted = ent_skipped = rel_deleted = rel_skipped = 0
    if ent_hashes:
        ent_keys = [f"entity-{h}" for h in ent_hashes]
        ent_deleted, ent_skipped = embed_manager.entities_embedding_store.delete_items(ent_keys)
        logger.info(f"实体向量删除完成，删除: {ent_deleted}, 跳过: {ent_skipped}")
    if rel_hashes:
        rel_keys = [f"relation-{h}" for h in rel_hashes]
        rel_deleted, rel_skipped = embed_manager.relation_embedding_store.delete_items(rel_keys)
        logger.info(f"关系向量删除完成，删除: {rel_deleted}, 跳过: {rel_skipped}")

    # 删除图节点/边
    kg_result = kg_manager.delete_paragraphs(
        pg_hashes,
        ent_hashes=ent_hashes if args.delete_entities else None,
        remove_orphan_entities=args.remove_orphan_entities,
    )
    logger.info(
        f"KG 删除完成，删除: {kg_result.get('deleted', 0)}, 跳过: {kg_result.get('skipped', 0)}, "
        f"孤立实体清理: {kg_result.get('orphan_removed', 0)}"
    )

    # 重建索引并保存
    logger.info("重建 Faiss 索引并保存嵌入文件...")
    embed_manager.rebuild_faiss_index()
    embed_manager.save_to_file()

    logger.info("保存 KG 数据...")
    kg_manager.save_to_file()

    # 删除后统计
    after_para_vec = len(embed_manager.paragraphs_embedding_store.store)
    after_ent_vec = len(embed_manager.entities_embedding_store.store)
    after_rel_vec = len(embed_manager.relation_embedding_store.store)
    after_nodes = len(kg_manager.graph.get_node_list())
    after_edges = len(kg_manager.graph.get_edge_list())

    logger.info(
        "删除后统计: 段落向量=%d(%+d), 实体向量=%d(%+d), 关系向量=%d(%+d), KG节点=%d(%+d), KG边=%d(%+d)"
        % (
            after_para_vec,
            after_para_vec - before_para_vec,
            after_ent_vec,
            after_ent_vec - before_ent_vec,
            after_rel_vec,
            after_rel_vec - before_rel_vec,
            after_nodes,
            after_nodes - before_nodes,
            after_edges,
            after_edges - before_edges,
        )
    )

    logger.info("删除流程完成")
    print(
        "\n[NOTICE] 删除脚本执行完毕。如主程序（聊天 / WebUI）已在运行，"
        "请重启主程序，或在主程序内部调用一次 lpmm_start_up() 以应用最新 LPMM 知识库。"
    )
    print("[NOTICE] 如果不清楚 lpmm_start_up 是什么，直接重启主程序即可。")


if __name__ == "__main__":
    main()
