from dataclasses import dataclass
import json
import os
import math
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# import tqdm
import faiss

from .utils.hash import get_sha256
from .global_logger import logger
from rich.traceback import install
from rich.progress import (
    Progress,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TaskProgressColumn,
    MofNCompleteColumn,
    SpinnerColumn,
    TextColumn,
)
from src.config.config import global_config


install(extra_lines=3)

# 多线程embedding配置常量
DEFAULT_MAX_WORKERS = 10  # 默认最大线程数
DEFAULT_CHUNK_SIZE = 10  # 默认每个线程处理的数据块大小
MIN_CHUNK_SIZE = 1  # 最小分块大小
MAX_CHUNK_SIZE = 50  # 最大分块大小
MIN_WORKERS = 1  # 最小线程数
MAX_WORKERS = 20  # 最大线程数

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EMBEDDING_DATA_DIR = os.path.join(ROOT_PATH, "data", "embedding")
EMBEDDING_DATA_DIR_STR = str(EMBEDDING_DATA_DIR).replace("\\", "/")
TOTAL_EMBEDDING_TIMES = 3  # 统计嵌入次数

# 嵌入模型测试字符串，测试模型一致性，来自开发群的聊天记录
# 这些字符串的嵌入结果应该是固定的，不能随时间变化
EMBEDDING_TEST_STRINGS = [
    "阿卡伊真的太好玩了，神秘性感大女同等着你",
    "你怎么知道我arc12.64了",
    "我是蕾缪乐小姐的狗",
    "关注Oct谢谢喵",
    "不是w6我不草",
    "关注千石可乐谢谢喵",
    "来玩CLANNAD，AIR，樱之诗，樱之刻谢谢喵",
    "关注墨梓柒谢谢喵",
    "Ciallo~",
    "来玩巧克甜恋谢谢喵",
    "水印",
    "我也在纠结晚饭，铁锅炒鸡听着就香！",
    "test你妈喵",
]
EMBEDDING_TEST_FILE = os.path.join(ROOT_PATH, "data", "embedding_model_test.json")
EMBEDDING_SIM_THRESHOLD = 0.99


def cosine_similarity(a, b):
    # 计算余弦相似度
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class EmbeddingStoreItem:
    """嵌入库中的项"""

    def __init__(self, item_hash: str, embedding: List[float], content: str):
        self.hash = item_hash
        self.embedding = embedding
        self.str = content

    def to_dict(self) -> dict:
        """转为dict"""
        return {
            "hash": self.hash,
            "embedding": self.embedding,
            "str": self.str,
        }


class EmbeddingStore:
    def __init__(
        self,
        namespace: str,
        dir_path: str,
        max_workers: int = DEFAULT_MAX_WORKERS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.namespace = namespace
        self.dir = dir_path
        self.embedding_file_path = f"{dir_path}/{namespace}.parquet"
        self.index_file_path = f"{dir_path}/{namespace}.index"
        self.idx2hash_file_path = f"{dir_path}/{namespace}_i2h.json"

        self.dirty = False  # 标记是否有新增数据需要重建索引

        # 多线程配置参数验证和设置
        self.max_workers = max(MIN_WORKERS, min(MAX_WORKERS, max_workers))
        self.chunk_size = max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, chunk_size))

        # 如果配置值被调整，记录日志
        if self.max_workers != max_workers:
            logger.warning(
                f"max_workers 已从 {max_workers} 调整为 {self.max_workers} (范围: {MIN_WORKERS}-{MAX_WORKERS})"
            )
        if self.chunk_size != chunk_size:
            logger.warning(
                f"chunk_size 已从 {chunk_size} 调整为 {self.chunk_size} (范围: {MIN_CHUNK_SIZE}-{MAX_CHUNK_SIZE})"
            )

        self.store = {}

        self.faiss_index = None
        self.idx2hash = None

    @staticmethod
    def hash_texts(namespace: str, texts: List[str]) -> List[str]:
        """将原文计算为带前缀的键"""
        return [f"{namespace}-{get_sha256(t)}" for t in texts]

    def _get_embedding(self, s: str) -> List[float]:
        """获取字符串的嵌入向量，使用完全同步的方式避免事件循环问题"""
        # 创建新的事件循环并在完成后立即关闭
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # 创建新的LLMRequest实例
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config

            llm = LLMRequest(model_set=model_config.model_task_config.embedding, request_type="embedding")

            # 使用新的事件循环运行异步方法
            embedding, _ = loop.run_until_complete(llm.get_embedding(s))

            if embedding and len(embedding) > 0:
                return embedding
            else:
                logger.error(f"获取嵌入失败: {s}")
                return []

        except Exception as e:
            logger.error(f"获取嵌入时发生异常: {s}, 错误: {e}")
            return []
        finally:
            # 确保事件循环被正确关闭
            try:
                loop.close()
            except Exception:
                pass

    def _get_embeddings_batch_threaded(
        self, strs: List[str], chunk_size: int = 10, max_workers: int = 10, progress_callback=None
    ) -> List[Tuple[str, List[float]]]:
        """使用多线程批量获取嵌入向量

        Args:
            strs: 要获取嵌入的字符串列表
            chunk_size: 每个线程处理的数据块大小
            max_workers: 最大线程数
            progress_callback: 进度回调函数，接收一个参数表示完成的数量

        Returns:
            包含(原始字符串, 嵌入向量)的元组列表，保持与输入顺序一致
        """
        if not strs:
            return []

        # 分块
        chunks = []
        for i in range(0, len(strs), chunk_size):
            chunk = strs[i : i + chunk_size]
            chunks.append((i, chunk))  # 保存起始索引以维持顺序

        # 结果存储，使用字典按索引存储以保证顺序
        results = {}

        def process_chunk(chunk_data):
            """处理单个数据块的函数"""
            start_idx, chunk_strs = chunk_data
            chunk_results = []

            # 为每个线程创建独立的LLMRequest实例
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config

            try:
                # 创建线程专用的LLM实例
                llm = LLMRequest(model_set=model_config.model_task_config.embedding, request_type="embedding")

                for i, s in enumerate(chunk_strs):
                    try:
                        # 在线程中创建独立的事件循环
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            embedding = loop.run_until_complete(llm.get_embedding(s))
                        finally:
                            loop.close()

                        if embedding and len(embedding) > 0:
                            chunk_results.append((start_idx + i, s, embedding[0]))  # embedding[0] 是实际的向量
                        else:
                            logger.error(f"获取嵌入失败: {s}")
                            chunk_results.append((start_idx + i, s, []))

                        # 每完成一个嵌入立即更新进度
                        if progress_callback:
                            progress_callback(1)

                    except Exception as e:
                        logger.error(f"获取嵌入时发生异常: {s}, 错误: {e}")
                        chunk_results.append((start_idx + i, s, []))

                        # 即使失败也要更新进度
                        if progress_callback:
                            progress_callback(1)

            except Exception as e:
                logger.error(f"创建LLM实例失败: {e}")
                # 如果创建LLM实例失败，返回空结果
                for i, s in enumerate(chunk_strs):
                    chunk_results.append((start_idx + i, s, []))
                    # 即使失败也要更新进度
                    if progress_callback:
                        progress_callback(1)

            return chunk_results

        # 使用线程池处理
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_chunk = {executor.submit(process_chunk, chunk): chunk for chunk in chunks}

            # 收集结果（进度已在process_chunk中实时更新）
            for future in as_completed(future_to_chunk):
                try:
                    chunk_results = future.result()
                    for idx, s, embedding in chunk_results:
                        results[idx] = (s, embedding)
                except Exception as e:
                    chunk = future_to_chunk[future]
                    logger.error(f"处理数据块时发生异常: {chunk}, 错误: {e}")
                    # 为失败的块添加空结果
                    start_idx, chunk_strs = chunk
                    for i, s in enumerate(chunk_strs):
                        results[start_idx + i] = (s, [])

        # 按原始顺序返回结果
        ordered_results = []
        for i in range(len(strs)):
            if i in results:
                ordered_results.append(results[i])
            else:
                # 防止遗漏
                ordered_results.append((strs[i], []))

        return ordered_results

    def get_test_file_path(self):
        return EMBEDDING_TEST_FILE

    def save_embedding_test_vectors(self):
        """保存测试字符串的嵌入到本地（使用多线程优化）"""
        logger.info("开始保存测试字符串的嵌入向量...")

        # 使用多线程批量获取测试字符串的嵌入
        embedding_results = self._get_embeddings_batch_threaded(
            EMBEDDING_TEST_STRINGS,
            chunk_size=min(self.chunk_size, len(EMBEDDING_TEST_STRINGS)),
            max_workers=min(self.max_workers, len(EMBEDDING_TEST_STRINGS)),
        )

        # 构建测试向量字典
        test_vectors = {}
        for idx, (s, embedding) in enumerate(embedding_results):
            if embedding:
                test_vectors[str(idx)] = embedding
            else:
                logger.error(f"获取测试字符串嵌入失败: {s}")
                # 使用原始单线程方法作为后备
                test_vectors[str(idx)] = self._get_embedding(s)

        with open(self.get_test_file_path(), "w", encoding="utf-8") as f:
            json.dump(test_vectors, f, ensure_ascii=False, indent=2)

        logger.info("测试字符串嵌入向量保存完成")

    def load_embedding_test_vectors(self):
        """加载本地保存的测试字符串嵌入"""
        path = self.get_test_file_path()
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def check_embedding_model_consistency(self):
        """校验当前模型与本地嵌入模型是否一致（使用多线程优化）"""
        local_vectors = self.load_embedding_test_vectors()
        if local_vectors is None:
            logger.warning("未检测到本地嵌入模型测试文件，将保存当前模型的测试嵌入。")
            self.save_embedding_test_vectors()
            return True

        # 检查本地向量完整性
        for idx in range(len(EMBEDDING_TEST_STRINGS)):
            if local_vectors.get(str(idx)) is None:
                logger.warning("本地嵌入模型测试文件缺失部分测试字符串，将重新保存。")
                self.save_embedding_test_vectors()
                return True

        logger.info("开始检验嵌入模型一致性...")

        # 使用多线程批量获取当前模型的嵌入
        embedding_results = self._get_embeddings_batch_threaded(
            EMBEDDING_TEST_STRINGS,
            chunk_size=min(self.chunk_size, len(EMBEDDING_TEST_STRINGS)),
            max_workers=min(self.max_workers, len(EMBEDDING_TEST_STRINGS)),
        )

        # 检查一致性
        for idx, (s, new_emb) in enumerate(embedding_results):
            local_emb = local_vectors.get(str(idx))
            if not new_emb:
                logger.error(f"获取测试字符串嵌入失败: {s}")
                return False

            sim = cosine_similarity(local_emb, new_emb)
            if sim < EMBEDDING_SIM_THRESHOLD:
                logger.error(f"嵌入模型一致性校验失败，字符串: {s}, 相似度: {sim:.4f}")
                return False

        logger.info("嵌入模型一致性校验通过。")
        return True

    def batch_insert_strs(self, strs: List[str], times: int) -> None:
        """向库中存入字符串（使用多线程优化）"""
        if not strs:
            return

        total = len(strs)

        # 过滤已存在的字符串
        new_strs = []
        for s in strs:
            item_hash = self.namespace + "-" + get_sha256(s)
            if item_hash not in self.store:
                new_strs.append(s)

        if not new_strs:
            logger.info(f"所有字符串已存在于{self.namespace}嵌入库中，跳过处理")
            return

        logger.info(f"需要处理 {len(new_strs)}/{total} 个新字符串")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            "•",
            TimeElapsedColumn(),
            "<",
            TimeRemainingColumn(),
            transient=False,
        ) as progress:
            task = progress.add_task(f"存入嵌入库：({times}/{TOTAL_EMBEDDING_TIMES})", total=total)

            # 首先更新已存在项的进度
            already_processed = total - len(new_strs)
            if already_processed > 0:
                progress.update(task, advance=already_processed)

            if new_strs:
                # 使用实例配置的参数，智能调整分块和线程数
                optimal_chunk_size = max(
                    MIN_CHUNK_SIZE,
                    min(
                        self.chunk_size, len(new_strs) // self.max_workers if self.max_workers > 0 else self.chunk_size
                    ),
                )
                optimal_max_workers = min(
                    self.max_workers,
                    max(MIN_WORKERS, len(new_strs) // optimal_chunk_size if optimal_chunk_size > 0 else 1),
                )

                logger.debug(f"使用多线程处理: chunk_size={optimal_chunk_size}, max_workers={optimal_max_workers}")

                # 定义进度更新回调函数
                def update_progress(count):
                    progress.update(task, advance=count)

                # 批量获取嵌入，并实时更新进度
                embedding_results = self._get_embeddings_batch_threaded(
                    new_strs,
                    chunk_size=optimal_chunk_size,
                    max_workers=optimal_max_workers,
                    progress_callback=update_progress,
                )

                # 存入结果（不再需要在这里更新进度，因为已经在回调中更新了）
                for s, embedding in embedding_results:
                    item_hash = self.namespace + "-" + get_sha256(s)
                    if embedding:  # 只有成功获取到嵌入才存入
                        self.store[item_hash] = EmbeddingStoreItem(item_hash, embedding, s)
                        self.dirty = True
                    else:
                        logger.warning(f"跳过存储失败的嵌入: {s[:50]}...")

    def save_to_file(self) -> None:
        """保存到文件"""
        data = []
        logger.info(f"正在保存{self.namespace}嵌入库到文件{self.embedding_file_path}")
        for item in self.store.values():
            data.append(item.to_dict())
        data_frame = pd.DataFrame(data)

        if not os.path.exists(self.dir):
            os.makedirs(self.dir, exist_ok=True)
        if not os.path.exists(self.embedding_file_path):
            open(self.embedding_file_path, "w").close()

        data_frame.to_parquet(self.embedding_file_path, engine="pyarrow", index=False)
        logger.info(f"{self.namespace}嵌入库保存成功")

        if self.faiss_index is not None and self.idx2hash is not None:
            logger.info(f"正在保存{self.namespace}嵌入库的FaissIndex到文件{self.index_file_path}")
            faiss.write_index(self.faiss_index, self.index_file_path)
            logger.info(f"{self.namespace}嵌入库的FaissIndex保存成功")
            logger.info(f"正在保存{self.namespace}嵌入库的idx2hash映射到文件{self.idx2hash_file_path}")
            with open(self.idx2hash_file_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.idx2hash, ensure_ascii=False, indent=4))
            logger.info(f"{self.namespace}嵌入库的idx2hash映射保存成功")

    def load_from_file(self) -> None:
        """从文件中加载"""
        if not os.path.exists(self.embedding_file_path):
            raise Exception(f"文件{self.embedding_file_path}不存在")
        logger.info("正在加载嵌入库...")
        logger.debug(f"正在从文件{self.embedding_file_path}中加载{self.namespace}嵌入库")
        data_frame = pd.read_parquet(self.embedding_file_path, engine="pyarrow")
        total = len(data_frame)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            "•",
            TimeElapsedColumn(),
            "<",
            TimeRemainingColumn(),
            transient=False,
        ) as progress:
            task = progress.add_task("加载嵌入库", total=total)
            for _, row in data_frame.iterrows():
                self.store[row["hash"]] = EmbeddingStoreItem(row["hash"], row["embedding"], row["str"])
                progress.update(task, advance=1)
        logger.info(f"{self.namespace}嵌入库加载成功")

        try:
            if os.path.exists(self.index_file_path):
                logger.info(f"正在加载{self.namespace}嵌入库的FaissIndex...")
                logger.debug(f"正在从文件{self.index_file_path}中加载{self.namespace}嵌入库的FaissIndex")
                self.faiss_index = faiss.read_index(self.index_file_path)
                logger.info(f"{self.namespace}嵌入库的FaissIndex加载成功")
            else:
                raise Exception(f"文件{self.index_file_path}不存在")
            if os.path.exists(self.idx2hash_file_path):
                logger.info(f"正在加载{self.namespace}嵌入库的idx2hash映射...")
                logger.debug(f"正在从文件{self.idx2hash_file_path}中加载{self.namespace}嵌入库的idx2hash映射")
                with open(self.idx2hash_file_path, "r") as f:
                    self.idx2hash = json.load(f)
                logger.info(f"{self.namespace}嵌入库的idx2hash映射加载成功")
            else:
                raise Exception(f"文件{self.idx2hash_file_path}不存在")
        except Exception as e:
            logger.error(f"加载{self.namespace}嵌入库的FaissIndex时发生错误：{e}")
            logger.warning("正在重建Faiss索引")
            self.build_faiss_index()
            logger.info(f"{self.namespace}嵌入库的FaissIndex重建成功")
            self.save_to_file()
        self.dirty = False

    def build_faiss_index(self) -> None:
        """重新构建Faiss索引，以余弦相似度为度量"""
        # 空库直接跳过，清空索引映射
        if not self.store:
            self.idx2hash = {}
            self.faiss_index = None
            self.dirty = False
            return

        # 获取所有的embedding
        array = []
        self.idx2hash = dict()
        for key in self.store:
            array.append(self.store[key].embedding)
            self.idx2hash[str(len(array) - 1)] = key
        embeddings = np.array(array, dtype=np.float32)
        if embeddings.size == 0:
            self.idx2hash = {}
            self.faiss_index = None
            self.dirty = False
            return
        # L2归一化
        faiss.normalize_L2(embeddings)
        # 构建索引
        self.faiss_index = faiss.IndexFlatIP(global_config.lpmm_knowledge.embedding_dimension)
        self.faiss_index.add(embeddings)
        self.dirty = False

    def delete_items(self, hashes: List[str]) -> Tuple[int, int]:
        """删除指定键的嵌入并重建 idx2hash（不直接重建 faiss）

        Args:
            hashes: 需要删除的完整键列表（如 paragraph-xxx）

        Returns:
            (deleted, skipped)
        """
        deleted = 0
        skipped = 0
        for h in hashes:
            if h in self.store:
                self.store.pop(h)
                deleted += 1
            else:
                skipped += 1

        # 重新构建 idx2hash 映射
        self.idx2hash = {}
        for idx, key in enumerate(self.store.keys()):
            self.idx2hash[str(idx)] = key

        # 删除后标记 dirty，faiss 重建由上层统一调用
        self.dirty = True
        return deleted, skipped

    def search_top_k(self, query: List[float], k: int) -> List[Tuple[str, float]]:
        """搜索最相似的k个项，以余弦相似度为度量
        Args:
            query: 查询的embedding
            k: 返回的最相似的k个项
        Returns:
            result: 最相似的k个项的(hash, 余弦相似度)列表
        """
        if self.faiss_index is None:
            logger.debug("FaissIndex尚未构建,返回None")
            return []
        if self.idx2hash is None:
            logger.warning("idx2hash尚未构建,返回None")
            return []

        # L2归一化
        faiss.normalize_L2(np.array([query], dtype=np.float32))
        # 搜索
        distances, indices = self.faiss_index.search(np.array([query]), k)
        # 整理结果
        indices = list(indices.flatten())
        distances = list(distances.flatten())
        result = [
            (self.idx2hash[str(int(idx))], float(sim))
            for (idx, sim) in zip(indices, distances, strict=False)
            if idx in range(len(self.idx2hash))
        ]

        return result


class EmbeddingManager:
    def __init__(self, max_workers: int | None = None, chunk_size: int | None = None):
        """
        初始化EmbeddingManager

        Args:
            max_workers: 最大线程数
            chunk_size: 每个线程处理的数据块大小
        """
        max_workers = max_workers if max_workers is not None else global_config.lpmm_knowledge.max_embedding_workers
        chunk_size = chunk_size if chunk_size is not None else global_config.lpmm_knowledge.embedding_chunk_size
        self.paragraphs_embedding_store = EmbeddingStore(
            "paragraph",  # type: ignore
            EMBEDDING_DATA_DIR_STR,
            max_workers=max_workers,
            chunk_size=chunk_size,
        )
        self.entities_embedding_store = EmbeddingStore(
            "entity",  # type: ignore
            EMBEDDING_DATA_DIR_STR,
            max_workers=max_workers,
            chunk_size=chunk_size,
        )
        self.relation_embedding_store = EmbeddingStore(
            "relation",  # type: ignore
            EMBEDDING_DATA_DIR_STR,
            max_workers=max_workers,
            chunk_size=chunk_size,
        )
        self.stored_pg_hashes = set()

    def check_all_embedding_model_consistency(self):
        """对所有嵌入库做模型一致性校验"""
        return self.paragraphs_embedding_store.check_embedding_model_consistency()

    def _store_pg_into_embedding(self, raw_paragraphs: Dict[str, str]):
        """将段落编码存入Embedding库"""
        self.paragraphs_embedding_store.batch_insert_strs(list(raw_paragraphs.values()), times=1)

    def _store_ent_into_embedding(self, triple_list_data: Dict[str, List[List[str]]]):
        """将实体编码存入Embedding库"""
        entities = set()
        for triple_list in triple_list_data.values():
            for triple in triple_list:
                entities.add(triple[0])
                entities.add(triple[2])
        self.entities_embedding_store.batch_insert_strs(list(entities), times=2)

    def _store_rel_into_embedding(self, triple_list_data: Dict[str, List[List[str]]]):
        """将关系编码存入Embedding库"""
        graph_triples = []  # a list of unique relation triple (in tuple) from all chunks
        for triples in triple_list_data.values():
            graph_triples.extend([tuple(t) for t in triples])
        graph_triples = list(set(graph_triples))
        self.relation_embedding_store.batch_insert_strs([str(triple) for triple in graph_triples], times=3)

    def load_from_file(self):
        """从文件加载"""
        self.paragraphs_embedding_store.load_from_file()
        self.entities_embedding_store.load_from_file()
        self.relation_embedding_store.load_from_file()
        # 从段落库中获取已存储的hash
        self.stored_pg_hashes = set(self.paragraphs_embedding_store.store.keys())

    def store_new_data_set(
        self,
        raw_paragraphs: Dict[str, str],
        triple_list_data: Dict[str, List[List[str]]],
    ):
        if not self.check_all_embedding_model_consistency():
            raise Exception("嵌入模型与本地存储不一致，请检查模型设置或清空嵌入库后重试。")
        """存储新的数据集"""
        self._store_pg_into_embedding(raw_paragraphs)
        self._store_ent_into_embedding(triple_list_data)
        self._store_rel_into_embedding(triple_list_data)
        self.stored_pg_hashes.update(raw_paragraphs.keys())

    def save_to_file(self):
        """保存到文件"""
        self.paragraphs_embedding_store.save_to_file()
        self.entities_embedding_store.save_to_file()
        self.relation_embedding_store.save_to_file()

    def rebuild_faiss_index(self):
        """重建Faiss索引，新增数据后调用，带跳过逻辑"""

        def _rebuild_if_needed(store: EmbeddingStore):
            if (
                not store.dirty
                and store.faiss_index is not None
                and store.idx2hash is not None
                and getattr(store.faiss_index, "ntotal", 0) == len(store.idx2hash) == len(store.store)
            ):
                logger.info(f"{store.namespace} FaissIndex 已是最新，跳过重建")
                return
            store.build_faiss_index()

        _rebuild_if_needed(self.paragraphs_embedding_store)
        _rebuild_if_needed(self.entities_embedding_store)
        _rebuild_if_needed(self.relation_embedding_store)
