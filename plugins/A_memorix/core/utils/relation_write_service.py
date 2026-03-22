"""
统一关系写入与关系向量化服务。

规则：
1. 元数据是主数据源，向量是从索引。
2. 关系先写 metadata，再写向量。
3. 向量失败不回滚 metadata，依赖状态机与回填任务修复。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.common.logger import get_logger


logger = get_logger("A_Memorix.RelationWriteService")


@dataclass
class RelationWriteResult:
    hash_value: str
    vector_written: bool
    vector_already_exists: bool
    vector_state: str


class RelationWriteService:
    """关系写入收口服务。"""

    ERROR_MAX_LEN = 500

    def __init__(
        self,
        metadata_store: Any,
        graph_store: Any,
        vector_store: Any,
        embedding_manager: Any,
    ):
        self.metadata_store = metadata_store
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.embedding_manager = embedding_manager

    @staticmethod
    def build_relation_vector_text(subject: str, predicate: str, obj: str) -> str:
        s = str(subject or "").strip()
        p = str(predicate or "").strip()
        o = str(obj or "").strip()
        # 双表达：兼容关键词检索与自然语言问句
        return f"{s} {p} {o}\n{s}和{o}的关系是{p}"

    async def ensure_relation_vector(
        self,
        hash_value: str,
        subject: str,
        predicate: str,
        obj: str,
        *,
        max_error_len: int = ERROR_MAX_LEN,
    ) -> RelationWriteResult:
        """
        为已有关系确保向量存在并更新状态。
        """
        if hash_value in self.vector_store:
            self.metadata_store.set_relation_vector_state(hash_value, "ready")
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=False,
                vector_already_exists=True,
                vector_state="ready",
            )

        self.metadata_store.set_relation_vector_state(hash_value, "pending")
        try:
            vector_text = self.build_relation_vector_text(subject, predicate, obj)
            embedding = await self.embedding_manager.encode(vector_text)
            self.vector_store.add(
                vectors=embedding.reshape(1, -1),
                ids=[hash_value],
            )
            self.metadata_store.set_relation_vector_state(hash_value, "ready")
            logger.info(
                "metric.relation_vector_write_success=1 "
                "metric.relation_vector_write_success_count=1 "
                f"hash={hash_value[:16]}"
            )
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=True,
                vector_already_exists=False,
                vector_state="ready",
            )
        except ValueError:
            # 向量已存在冲突，按成功处理
            self.metadata_store.set_relation_vector_state(hash_value, "ready")
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=False,
                vector_already_exists=True,
                vector_state="ready",
            )
        except Exception as e:
            err = str(e)[:max_error_len]
            self.metadata_store.set_relation_vector_state(
                hash_value,
                "failed",
                error=err,
                bump_retry=True,
            )
            logger.warning(
                "metric.relation_vector_write_fail=1 "
                "metric.relation_vector_write_fail_count=1 "
                f"hash={hash_value[:16]} "
                f"err={err}"
            )
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=False,
                vector_already_exists=False,
                vector_state="failed",
            )

    async def upsert_relation_with_vector(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 1.0,
        source_paragraph: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        *,
        write_vector: bool = True,
    ) -> RelationWriteResult:
        """
        统一关系写入：
        1) 写 metadata relation
        2) 写 graph edge relation_hash
        3) 按需写 relation vector
        """
        rel_hash = self.metadata_store.add_relation(
            subject=subject,
            predicate=predicate,
            obj=obj,
            confidence=confidence,
            source_paragraph=source_paragraph,
            metadata=metadata or {},
        )
        self.graph_store.add_edges([(subject, obj)], relation_hashes=[rel_hash])

        if not write_vector:
            self.metadata_store.set_relation_vector_state(rel_hash, "none")
            return RelationWriteResult(
                hash_value=rel_hash,
                vector_written=False,
                vector_already_exists=False,
                vector_state="none",
            )

        return await self.ensure_relation_vector(
            hash_value=rel_hash,
            subject=subject,
            predicate=predicate,
            obj=obj,
        )
