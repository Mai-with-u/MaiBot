import asyncio
import os
from functools import partial
from typing import List, Callable, Any
from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.kg_manager import KGManager
from src.chat.knowledge.qa_manager import QAManager
from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.knowledge import get_qa_manager, lpmm_start_up

logger = get_logger("LPMM-Plugin-API")

class LPMMOperations:
    """
    LPMM 内部操作接口。
    封装了 LPMM 的核心操作，供插件系统 API 或其他内部组件调用。
    """
    
    def __init__(self):
        self._initialized = False

    async def _run_cancellable_executor(
        self, func: Callable, *args, **kwargs
    ) -> Any:
        """
        在线程池中执行可取消的同步操作。
        当任务被取消时（如 Ctrl+C），会立即响应并抛出 CancelledError。
        注意：线程池中的操作可能仍在运行，但协程会立即返回，不会阻塞主进程。
        
        Args:
            func: 要执行的同步函数
            *args: 函数的位置参数
            **kwargs: 函数的关键字参数
            
        Returns:
            函数的返回值
            
        Raises:
            asyncio.CancelledError: 当任务被取消时
        """
        loop = asyncio.get_event_loop()
        # 在线程池中执行，当协程被取消时会立即响应
        # 虽然线程池中的操作可能仍在运行，但协程不会阻塞
        return await loop.run_in_executor(None, func, *args, **kwargs)

    async def _get_managers(self) -> tuple[EmbeddingManager, KGManager, QAManager]:
        """获取并确保 LPMM 管理器已初始化"""
        qa_mgr = get_qa_manager()
        if qa_mgr is None:
            # 如果全局没初始化，尝试初始化
            if not global_config.lpmm_knowledge.enable:
                logger.warning("LPMM 知识库在全局配置中未启用，操作可能受限。")
            
            lpmm_start_up()
            qa_mgr = get_qa_manager()
            
        if qa_mgr is None:
            raise RuntimeError("无法获取 LPMM QAManager，请检查 LPMM 是否已正确安装和配置。")
            
        return qa_mgr.embed_manager, qa_mgr.kg_manager, qa_mgr

    async def add_content(self, text: str, auto_split: bool = True) -> dict:
        """
        向知识库添加新内容。
        
        Args:
            text: 原始文本。
            auto_split: 是否自动按双换行符分割段落。
                - True: 自动分割（默认），支持多段文本（用双换行分隔）
                - False: 不分割，将整个文本作为完整一段处理
            
        Returns:
            dict: {"status": "success/error", "count": 导入段落数, "message": "描述"}
        """
        try:
            embed_mgr, kg_mgr, _ = await self._get_managers()
            
            # 1. 分段处理
            if auto_split:
                # 自动按双换行符分割
                paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            else:
                # 不分割，作为完整一段
                text_stripped = text.strip()
                if not text_stripped:
                    return {"status": "error", "message": "文本内容为空"}
                paragraphs = [text_stripped]
            
            if not paragraphs:
                return {"status": "error", "message": "文本内容为空"}

            # 2. 实体与三元组抽取 (内部调用大模型)
            from src.chat.knowledge.ie_process import IEProcess
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config
            
            llm_ner = LLMRequest(model_set=model_config.model_task_config.lpmm_entity_extract, request_type="lpmm.entity_extract")
            llm_rdf = LLMRequest(model_set=model_config.model_task_config.lpmm_rdf_build, request_type="lpmm.rdf_build")
            ie_process = IEProcess(llm_ner, llm_rdf)
            
            logger.info(f"[Plugin API] 正在对 {len(paragraphs)} 段文本执行信息抽取...")
            extracted_docs = await ie_process.process_paragraphs(paragraphs)
            
            # 3. 构造并导入数据
            # 这里我们手动实现导入逻辑，不依赖外部脚本
            # a. 准备段落
            raw_paragraphs = {doc["idx"]: doc["passage"] for doc in extracted_docs}
            # b. 准备三元组
            triple_list_data = {doc["idx"]: doc["extracted_triples"] for doc in extracted_docs}

            # 向量化并入库
            # 注意：此处模仿 import_openie.py 的核心逻辑
            # 1. 先进行去重检查，只处理新段落
            # store_new_data_set 期望的格式：raw_paragraphs 的键是段落hash（不带前缀），值是段落文本
            new_raw_paragraphs = {}
            new_triple_list_data = {}
            
            for pg_hash, passage in raw_paragraphs.items():
                key = f"paragraph-{pg_hash}"
                if key not in embed_mgr.stored_pg_hashes:
                    new_raw_paragraphs[pg_hash] = passage
                    new_triple_list_data[pg_hash] = triple_list_data[pg_hash]

            if not new_raw_paragraphs:
                return {"status": "success", "count": 0, "message": "内容已存在，无需重复导入"}

            # 2. 使用 EmbeddingManager 的标准方法存储段落、实体和关系的嵌入
            # store_new_data_set 会自动处理嵌入生成和存储
            # 将同步阻塞操作放到线程池中执行，避免阻塞事件循环
            await self._run_cancellable_executor(
                embed_mgr.store_new_data_set,
                new_raw_paragraphs,
                new_triple_list_data
            )

            # 3. 构建知识图谱（只需要三元组数据和embedding_manager）
            await self._run_cancellable_executor(
                kg_mgr.build_kg,
                new_triple_list_data,
                embed_mgr
            )

            # 4. 持久化
            await self._run_cancellable_executor(embed_mgr.rebuild_faiss_index)
            await self._run_cancellable_executor(embed_mgr.save_to_file)
            await self._run_cancellable_executor(kg_mgr.save_to_file)
            
            return {"status": "success", "count": len(new_raw_paragraphs), "message": f"成功导入 {len(new_raw_paragraphs)} 条知识"}
            
        except asyncio.CancelledError:
            logger.warning("[Plugin API] 导入操作被用户中断")
            return {"status": "cancelled", "message": "导入操作已被用户中断"}
        except Exception as e:
            logger.error(f"[Plugin API] 导入知识失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    async def search(self, query: str, top_k: int = 3) -> List[str]:
        """
        检索知识库。
        
        Args:
            query: 查询问题。
            top_k: 返回最相关的条目数。
            
        Returns:
            List[str]: 相关文段列表。
        """
        try:
            _, _, qa_mgr = await self._get_managers()
            # 直接调用 QAManager 的检索接口
            knowledge = qa_mgr.get_knowledge(query, top_k=top_k)
            # 返回通常是拼接好的字符串，这里我们可以尝试按其内部规则切分回列表，或者直接返回
            return [knowledge] if knowledge else []
        except Exception as e:
            logger.error(f"[Plugin API] 检索知识失败: {e}")
            return []

    async def delete(self, keyword: str, exact_match: bool = False) -> dict:
        """
        根据关键词或完整文段删除知识库内容。
        
        Args:
            keyword: 匹配关键词或完整文段。
            exact_match: 是否使用完整文段匹配（True=完全匹配，False=关键词模糊匹配）。
            
        Returns:
            dict: {"status": "success/info", "deleted_count": 删除条数, "message": "描述"}
        """
        try:
            embed_mgr, kg_mgr, _ = await self._get_managers()
            
            # 1. 查找匹配的段落
            to_delete_keys = []
            to_delete_hashes = []
            
            for key, item in embed_mgr.paragraphs_embedding_store.store.items():
                if exact_match:
                    # 完整文段匹配
                    if item.str.strip() == keyword.strip():
                        to_delete_keys.append(key)
                        to_delete_hashes.append(key.replace("paragraph-", "", 1))
                else:
                    # 关键词模糊匹配
                    if keyword in item.str:
                        to_delete_keys.append(key)
                        to_delete_hashes.append(key.replace("paragraph-", "", 1))
            
            if not to_delete_keys:
                match_type = "完整文段" if exact_match else "关键词"
                return {"status": "info", "deleted_count": 0, "message": f"未找到匹配的内容（{match_type}匹配）"}

            # 2. 执行删除
            # 将同步阻塞操作放到线程池中执行，避免阻塞事件循环
            
            # a. 从向量库删除
            deleted_count, _ = await self._run_cancellable_executor(
                embed_mgr.paragraphs_embedding_store.delete_items,
                to_delete_keys
            )
            embed_mgr.stored_pg_hashes = set(embed_mgr.paragraphs_embedding_store.store.keys())
            
            # b. 从知识图谱删除
            # 注意：必须使用关键字参数，避免 True 被误当作 ent_hashes 参数
            # 使用 partial 来传递关键字参数，因为 run_in_executor 不支持 **kwargs
            delete_func = partial(
                kg_mgr.delete_paragraphs,
                to_delete_hashes,
                ent_hashes=None,
                remove_orphan_entities=True
            )
            await self._run_cancellable_executor(delete_func)

            # 3. 持久化
            await self._run_cancellable_executor(embed_mgr.rebuild_faiss_index)
            await self._run_cancellable_executor(embed_mgr.save_to_file)
            await self._run_cancellable_executor(kg_mgr.save_to_file)
            
            match_type = "完整文段" if exact_match else "关键词"
            return {"status": "success", "deleted_count": deleted_count, "message": f"已成功删除 {deleted_count} 条相关知识（{match_type}匹配）"}

        except asyncio.CancelledError:
            logger.warning("[Plugin API] 删除操作被用户中断")
            return {"status": "cancelled", "message": "删除操作已被用户中断"}
        except Exception as e:
            logger.error(f"[Plugin API] 删除知识失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    async def clear_all(self) -> dict:
        """
        清空整个LPMM知识库（删除所有段落、实体、关系和知识图谱数据）。
        
        Returns:
            dict: {"status": "success/error", "message": "描述", "stats": {...}}
        """
        try:
            embed_mgr, kg_mgr, _ = await self._get_managers()
            
            # 记录清空前的统计信息
            before_stats = {
                "paragraphs": len(embed_mgr.paragraphs_embedding_store.store),
                "entities": len(embed_mgr.entities_embedding_store.store),
                "relations": len(embed_mgr.relation_embedding_store.store),
                "kg_nodes": len(kg_mgr.graph.get_node_list()),
                "kg_edges": len(kg_mgr.graph.get_edge_list()),
            }
            
            # 将同步阻塞操作放到线程池中执行，避免阻塞事件循环
            
            # 1. 清空所有向量库
            # 获取所有keys
            para_keys = list(embed_mgr.paragraphs_embedding_store.store.keys())
            ent_keys = list(embed_mgr.entities_embedding_store.store.keys())
            rel_keys = list(embed_mgr.relation_embedding_store.store.keys())
            
            # 删除所有段落向量
            para_deleted, _ = await self._run_cancellable_executor(
                embed_mgr.paragraphs_embedding_store.delete_items,
                para_keys
            )
            embed_mgr.stored_pg_hashes.clear()
            
            # 删除所有实体向量
            if ent_keys:
                ent_deleted, _ = await self._run_cancellable_executor(
                    embed_mgr.entities_embedding_store.delete_items,
                    ent_keys
                )
            else:
                ent_deleted = 0
            
            # 删除所有关系向量
            if rel_keys:
                rel_deleted, _ = await self._run_cancellable_executor(
                    embed_mgr.relation_embedding_store.delete_items,
                    rel_keys
                )
            else:
                rel_deleted = 0
            
            # 2. 清空所有 embedding store 的索引和映射
            # 确保 faiss_index 和 idx2hash 也被重置，并删除旧的索引文件
            def _clear_embedding_indices():
                # 清空段落索引
                embed_mgr.paragraphs_embedding_store.faiss_index = None
                embed_mgr.paragraphs_embedding_store.idx2hash = None
                embed_mgr.paragraphs_embedding_store.dirty = False
                # 删除旧的索引文件
                if os.path.exists(embed_mgr.paragraphs_embedding_store.index_file_path):
                    os.remove(embed_mgr.paragraphs_embedding_store.index_file_path)
                if os.path.exists(embed_mgr.paragraphs_embedding_store.idx2hash_file_path):
                    os.remove(embed_mgr.paragraphs_embedding_store.idx2hash_file_path)
                
                # 清空实体索引
                embed_mgr.entities_embedding_store.faiss_index = None
                embed_mgr.entities_embedding_store.idx2hash = None
                embed_mgr.entities_embedding_store.dirty = False
                # 删除旧的索引文件
                if os.path.exists(embed_mgr.entities_embedding_store.index_file_path):
                    os.remove(embed_mgr.entities_embedding_store.index_file_path)
                if os.path.exists(embed_mgr.entities_embedding_store.idx2hash_file_path):
                    os.remove(embed_mgr.entities_embedding_store.idx2hash_file_path)
                
                # 清空关系索引
                embed_mgr.relation_embedding_store.faiss_index = None
                embed_mgr.relation_embedding_store.idx2hash = None
                embed_mgr.relation_embedding_store.dirty = False
                # 删除旧的索引文件
                if os.path.exists(embed_mgr.relation_embedding_store.index_file_path):
                    os.remove(embed_mgr.relation_embedding_store.index_file_path)
                if os.path.exists(embed_mgr.relation_embedding_store.idx2hash_file_path):
                    os.remove(embed_mgr.relation_embedding_store.idx2hash_file_path)
            
            await self._run_cancellable_executor(_clear_embedding_indices)
            
            # 3. 清空知识图谱
            # 获取所有段落hash
            all_pg_hashes = list(kg_mgr.stored_paragraph_hashes)
            if all_pg_hashes:
                # 删除所有段落节点（这会自动清理相关的边和孤立实体）
                # 注意：必须使用关键字参数，避免 True 被误当作 ent_hashes 参数
                # 使用 partial 来传递关键字参数，因为 run_in_executor 不支持 **kwargs
                delete_func = partial(
                    kg_mgr.delete_paragraphs,
                    all_pg_hashes,
                    ent_hashes=None,
                    remove_orphan_entities=True
                )
                await self._run_cancellable_executor(delete_func)
            
            # 完全清空KG：创建新的空图（无论是否有段落hash都要执行）
            from quick_algo import di_graph
            kg_mgr.graph = di_graph.DiGraph()
            kg_mgr.stored_paragraph_hashes.clear()
            kg_mgr.ent_appear_cnt.clear()
            
            # 4. 保存所有数据（此时所有store都是空的，索引也是None）
            # 注意：即使store为空，save_to_file也会保存空的DataFrame，这是正确的
            await self._run_cancellable_executor(embed_mgr.save_to_file)
            await self._run_cancellable_executor(kg_mgr.save_to_file)
            
            after_stats = {
                "paragraphs": len(embed_mgr.paragraphs_embedding_store.store),
                "entities": len(embed_mgr.entities_embedding_store.store),
                "relations": len(embed_mgr.relation_embedding_store.store),
                "kg_nodes": len(kg_mgr.graph.get_node_list()),
                "kg_edges": len(kg_mgr.graph.get_edge_list()),
            }
            
            return {
                "status": "success",
                "message": f"已成功清空LPMM知识库（删除 {para_deleted} 个段落、{ent_deleted} 个实体、{rel_deleted} 个关系）",
                "stats": {
                    "before": before_stats,
                    "after": after_stats,
                }
            }

        except asyncio.CancelledError:
            logger.warning("[Plugin API] 清空操作被用户中断")
            return {"status": "cancelled", "message": "清空操作已被用户中断"}
        except Exception as e:
            logger.error(f"[Plugin API] 清空知识库失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

# 内部使用的单例
lpmm_ops = LPMMOperations()

