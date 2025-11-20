import time
from typing import Tuple, List, Dict, Optional

from .global_logger import logger
from .embedding_store import EmbeddingManager
from .kg_manager import KGManager

# from .lpmmconfig import global_config
from .utils.dyn_topk import dyn_select_top_k
from src.llm_models.utils_model import LLMRequest
from src.chat.utils.utils import get_embedding
from src.config.config import global_config, model_config

MAX_KNOWLEDGE_LENGTH = 10000  # 最大知识长度


class QAManager:
    def __init__(
        self,
        embed_manager: EmbeddingManager,
        kg_manager: KGManager,
    ):
        self.embed_manager = embed_manager
        self.kg_manager = kg_manager
        self.qa_model = LLMRequest(model_set=model_config.model_task_config.lpmm_qa, request_type="lpmm.qa")

    async def process_query(
        self, question: str
    ) -> Optional[Tuple[List[Tuple[str, float, float]], Optional[Dict[str, float]]]]:
        """处理查询"""

        # 生成问题的Embedding
        part_start_time = time.perf_counter()
        question_embedding = await get_embedding(question)
        if question_embedding is None:
            logger.error("生成问题Embedding失败")
            return None
        part_end_time = time.perf_counter()
        logger.debug(f"Embedding用时：{part_end_time - part_start_time:.5f}s")

        # 根据问题Embedding查询Relation Embedding库
        part_start_time = time.perf_counter()
        relation_search_res = self.embed_manager.relation_embedding_store.search_top_k(
            question_embedding,
            global_config.lpmm_knowledge.qa_relation_search_top_k,
        )
        if relation_search_res is None:
            return None
        # 过滤阈值
        # 考虑动态阈值：当存在显著数值差异的结果时，保留显著结果；否则，保留所有结果
        relation_search_res = dyn_select_top_k(relation_search_res, 0.5, 1.0)
        if not relation_search_res or relation_search_res[0][1] < global_config.lpmm_knowledge.qa_relation_threshold:
            # 未找到相关关系
            logger.debug("未找到相关关系，跳过关系检索")
            relation_search_res = []

        part_end_time = time.perf_counter()
        logger.debug(f"关系检索用时：{part_end_time - part_start_time:.5f}s")

        for res in relation_search_res:
            if store_item := self.embed_manager.relation_embedding_store.store.get(res[0]):
                rel_str = store_item.str
            logger.info(f"找到相关关系，相似度：{(res[1] * 100):.2f}%  -  {rel_str}")

        # TODO: 使用LLM过滤三元组结果
        # logger.info(f"LLM过滤三元组用时：{time.time() - part_start_time:.2f}s")
        # part_start_time = time.time()

        # 根据问题Embedding查询Paragraph Embedding库
        part_start_time = time.perf_counter()
        paragraph_search_res = self.embed_manager.paragraphs_embedding_store.search_top_k(
            question_embedding,
            global_config.lpmm_knowledge.qa_paragraph_search_top_k,
        )
        part_end_time = time.perf_counter()
        logger.debug(f"文段检索用时：{part_end_time - part_start_time:.5f}s")

        if len(relation_search_res) != 0:
            logger.info("找到相关关系，将使用RAG进行检索")
            # 使用KG检索
            part_start_time = time.perf_counter()
            result, ppr_node_weights = self.kg_manager.kg_search(
                relation_search_res, paragraph_search_res, self.embed_manager
            )
            part_end_time = time.perf_counter()
            logger.info(f"RAG检索用时：{part_end_time - part_start_time:.5f}s")
        else:
            logger.info("未找到相关关系，将使用文段检索结果")
            result = paragraph_search_res
            ppr_node_weights = None

        # 过滤阈值
        result = dyn_select_top_k(result, 0.5, 1.0)

        if global_config.debug.show_lpmm_paragraph:
            for res in result:
                raw_paragraph = self.embed_manager.paragraphs_embedding_store.store[res[0]].str
                logger.info(f"找到相关文段，相关系数：{res[1]:.8f}\n{raw_paragraph}\n\n")

        return result, ppr_node_weights

    async def get_knowledge(self, question: str, limit: int = 5) -> Optional[str]:
        """获取知识

        Args:
            question: 查询问题
            limit: 返回的相关知识条数
        """
        # 处理查询
        processed_result = await self.process_query(question)
        if processed_result is not None:
            query_res = processed_result[0]
            # 检查查询结果是否为空
            if not query_res:
                logger.debug("知识库查询结果为空，可能是知识库中没有相关内容")
                return None

            limit = max(1, limit) if isinstance(limit, int) else 5

            knowledge = [
                (
                    self.embed_manager.paragraphs_embedding_store.store[res[0]].str,
                    res[1],
                )
                for res in query_res
            ]

            # max_score = max([k[1] for k in knowledge]) if knowledge else None
            selected_knowledge = knowledge[:limit]

            formatted_knowledge = [
                f"第{i + 1}条知识：{k[0]}\n 该条知识对于问题的相关性：{k[1]}" for i, k in enumerate(selected_knowledge)
            ]
            # if max_score is not None:
            # formatted_knowledge.insert(0, f"最高相关系数：{max_score}")

            found_knowledge = "\n".join(formatted_knowledge)
            if len(found_knowledge) > MAX_KNOWLEDGE_LENGTH:
                found_knowledge = found_knowledge[:MAX_KNOWLEDGE_LENGTH] + "\n"
            return found_knowledge
        else:
            logger.debug("LPMM知识库并未初始化，可能是从未导入过知识...")
            return None
