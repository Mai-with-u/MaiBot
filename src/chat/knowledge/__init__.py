from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.qa_manager import QAManager
from src.chat.knowledge.kg_manager import KGManager
from src.chat.knowledge.global_logger import logger
from src.config.config import global_config
import os

INVALID_ENTITY = [
    "",
    "你",
    "他",
    "她",
    "它",
    "我们",
    "你们",
    "他们",
    "她们",
    "它们",
]

RAG_GRAPH_NAMESPACE = "rag-graph"
RAG_ENT_CNT_NAMESPACE = "rag-ent-cnt"
RAG_PG_HASH_NAMESPACE = "rag-pg-hash"


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_PATH = os.path.join(ROOT_PATH, "data")


qa_manager = None
inspire_manager = None


def get_qa_manager():
    return qa_manager


def lpmm_start_up():  # sourcery skip: extract-duplicate-method
    # 检查LPMM知识库是否启用
    if global_config.lpmm_knowledge.enable:
        logger.info("正在初始化Mai-LPMM")
        logger.info("创建LLM客户端")

        # 初始化Embedding库
        embed_manager = EmbeddingManager()
        logger.info("正在从文件加载Embedding库")
        try:
            embed_manager.load_from_file()
        except Exception as e:
            logger.warning(f"此消息不会影响正常使用：从文件加载Embedding库时，{e}")
            # logger.warning("如果你是第一次导入知识，或者还未导入知识，请忽略此错误")
        logger.info("Embedding库加载完成")
        # 初始化KG
        kg_manager = KGManager()
        logger.info("正在从文件加载KG")
        try:
            kg_manager.load_from_file()
        except Exception as e:
            logger.warning(f"此消息不会影响正常使用：从文件加载KG时，{e}")
            # logger.warning("如果你是第一次导入知识，或者还未导入知识，请忽略此错误")
        logger.info("KG加载完成")

        logger.info(f"KG节点数量：{len(kg_manager.graph.get_node_list())}")
        logger.info(f"KG边数量：{len(kg_manager.graph.get_edge_list())}")

        # 数据比对：Embedding库与KG的段落hash集合
        for pg_hash in kg_manager.stored_paragraph_hashes:
            # 使用与EmbeddingStore中一致的命名空间格式
            key = f"paragraph-{pg_hash}"
            if key not in embed_manager.stored_pg_hashes:
                logger.warning(f"KG中存在Embedding库中不存在的段落：{key}")
        global qa_manager
        # 问答系统（用于知识库）
        qa_manager = QAManager(
            embed_manager,
            kg_manager,
        )

        # # 记忆激活（用于记忆库）
        # global inspire_manager
        # inspire_manager = MemoryActiveManager(
        #     embed_manager,
        #     llm_client_list[global_config["embedding"]["provider"]],
        # )
    else:
        logger.info("LPMM知识库已禁用，跳过初始化")
        # 创建空的占位符对象，避免导入错误
