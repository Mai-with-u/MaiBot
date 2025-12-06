import os
import sys

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 确保能导入 src.*
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.knowledge import lpmm_start_up, get_qa_manager

logger = get_logger("refresh_lpmm_knowledge")


def main() -> None:
    logger.info("开始刷新 LPMM 知识库（重新加载向量库与 KG）...")

    if not global_config.lpmm_knowledge.enable:
        logger.warning(
            "当前配置中 lpmm_knowledge.enable = false，本次仅刷新磁盘数据与内存结构，"
            "但聊天侧如未启用 LPMM 仍不会在问答中使用知识库。"
        )

    # 调用标准启动逻辑，内部会加载 data/embedding 与 data/rag
    lpmm_start_up()

    qa_manager = get_qa_manager()
    if qa_manager is None:
        logger.error("刷新后 qa_manager 仍为 None，请检查是否已经成功导入过 LPMM 知识库。")
        return

    # 简要输出当前知识库规模，方便人工确认
    embed_manager = qa_manager.embed_manager
    kg_manager = qa_manager.kg_manager

    para_vec = len(embed_manager.paragraphs_embedding_store.store)
    ent_vec = len(embed_manager.entities_embedding_store.store)
    rel_vec = len(embed_manager.relation_embedding_store.store)
    nodes = len(kg_manager.graph.get_node_list())
    edges = len(kg_manager.graph.get_edge_list())

    logger.info("LPMM 知识库刷新完成，当前规模：")
    logger.info(
        "段落向量=%d, 实体向量=%d, 关系向量=%d, KG节点=%d, KG边=%d",
        para_vec,
        ent_vec,
        rel_vec,
        nodes,
        edges,
    )

    print("\n[REFRESH] 刷新完成，请注意：")
    print("- 本脚本是在独立进程内执行的，用于验证磁盘数据可以正常加载。")
    print("- 若主程序已在运行且未在内部调用 lpmm_start_up() 重新初始化，仍需重启或新增管理入口来热刷新。")
    print("- 如果不清楚 lpmm_start_up 是什么，只需要重启主程序即可。")


if __name__ == "__main__":
    main()
