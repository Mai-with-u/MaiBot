import json
import os
import time
from typing import Dict, List, Tuple, Set
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
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
from quick_algo import di_graph, pagerank


from .utils.hash import get_sha256
from .embedding_store import EmbeddingManager, EmbeddingStoreItem
from src.config.config import global_config

from .global_logger import logger


def _get_kg_dir():
    """
    安全地获取KG数据目录路径
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_path: str = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
    kg_dir = os.path.join(root_path, "data/rag")

    return str(kg_dir).replace("\\", "/")


# 延迟初始化，避免在模块加载时就访问可能未初始化的 local_storage
def get_kg_dir_str():
    """获取KG目录字符串"""
    return _get_kg_dir()


class KGManager:
    def __init__(self):
        # 会被保存的字段
        # 存储段落的hash值，用于去重
        self.stored_paragraph_hashes = set()
        # 实体出现次数
        self.ent_appear_cnt = {}
        # KG
        self.graph = di_graph.DiGraph()

        # 持久化相关 - 使用延迟初始化的路径
        self.dir_path = get_kg_dir_str()
        self.graph_data_path = self.dir_path + "/" + "rag-graph" + ".graphml"
        self.ent_cnt_data_path = self.dir_path + "/" + "rag-ent-cnt" + ".parquet"
        self.pg_hash_file_path = self.dir_path + "/" + "rag-pg-hash" + ".json"

    def save_to_file(self):
        """将KG数据保存到文件"""
        # 确保目录存在
        if not os.path.exists(self.dir_path):
            os.makedirs(self.dir_path, exist_ok=True)

        # 保存KG
        di_graph.save_to_file(self.graph, self.graph_data_path)

        # 保存实体计数到文件
        ent_cnt_df = pd.DataFrame([{"hash_key": k, "appear_cnt": v} for k, v in self.ent_appear_cnt.items()])
        ent_cnt_df.to_parquet(self.ent_cnt_data_path, engine="pyarrow", index=False)

        # 保存段落hash到文件
        with open(self.pg_hash_file_path, "w", encoding="utf-8") as f:
            data = {"stored_paragraph_hashes": list(self.stored_paragraph_hashes)}
            f.write(json.dumps(data, ensure_ascii=False, indent=4))

    def load_from_file(self):
        """从文件加载KG数据"""
        # 确保文件存在
        if not os.path.exists(self.pg_hash_file_path):
            raise FileNotFoundError(f"KG段落hash文件{self.pg_hash_file_path}不存在")
        if not os.path.exists(self.ent_cnt_data_path):
            raise FileNotFoundError(f"KG实体计数文件{self.ent_cnt_data_path}不存在")
        if not os.path.exists(self.graph_data_path):
            raise FileNotFoundError(f"KG图文件{self.graph_data_path}不存在")

        # 加载段落hash
        with open(self.pg_hash_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.stored_paragraph_hashes = set(data["stored_paragraph_hashes"])

        # 加载实体计数
        ent_cnt_df = pd.read_parquet(self.ent_cnt_data_path, engine="pyarrow")
        self.ent_appear_cnt = dict({row["hash_key"]: row["appear_cnt"] for _, row in ent_cnt_df.iterrows()})

        # 加载KG
        self.graph = di_graph.load_from_file(self.graph_data_path)

    def _rebuild_metadata_from_graph(self) -> None:
        """根据当前图重建 stored_paragraph_hashes 与 ent_appear_cnt"""
        nodes = self.graph.get_node_list()
        edges = self.graph.get_edge_list()

        # 段落 hash：paragraph-{hash}
        self.stored_paragraph_hashes = set()
        for node_id in nodes:
            if node_id.startswith("paragraph-"):
                self.stored_paragraph_hashes.add(node_id.split("paragraph-", 1)[1])

        # 实体出现次数：基于 entity -> paragraph 的边权
        ent_appear_cnt: Dict[str, float] = {}
        for edge_tuple in edges:
            src, tgt = edge_tuple[0], edge_tuple[1]
            if src.startswith("entity") and tgt.startswith("paragraph"):
                edge_data = self.graph[src, tgt]
                weight = edge_data["weight"] if "weight" in edge_data else 1.0
                ent_appear_cnt[src] = ent_appear_cnt.get(src, 0.0) + float(weight)

        self.ent_appear_cnt = ent_appear_cnt

    def _build_edges_between_ent(
        self,
        node_to_node: Dict[Tuple[str, str], float],
        triple_list_data: Dict[str, List[List[str]]],
    ):
        """构建实体节点之间的关系，同时统计实体出现次数"""
        for triple_list in triple_list_data.values():
            entity_set = set()
            for triple in triple_list:
                if triple[0] == triple[2]:
                    # 避免自连接
                    continue
                # 一个triple就是一条边（同时构建双向联系）
                hash_key1 = "entity" + "-" + get_sha256(triple[0])
                hash_key2 = "entity" + "-" + get_sha256(triple[2])
                node_to_node[(hash_key1, hash_key2)] = node_to_node.get((hash_key1, hash_key2), 0) + 1.0
                node_to_node[(hash_key2, hash_key1)] = node_to_node.get((hash_key2, hash_key1), 0) + 1.0
                entity_set.add(hash_key1)
                entity_set.add(hash_key2)

            # 实体出现次数统计
            for hash_key in entity_set:
                self.ent_appear_cnt[hash_key] = self.ent_appear_cnt.get(hash_key, 0) + 1.0

    @staticmethod
    def _build_edges_between_ent_pg(
        node_to_node: Dict[Tuple[str, str], float],
        triple_list_data: Dict[str, List[List[str]]],
    ):
        """构建实体节点与文段节点之间的关系"""
        for idx in triple_list_data:
            for triple in triple_list_data[idx]:
                ent_hash_key = "entity" + "-" + get_sha256(triple[0])
                pg_hash_key = "paragraph" + "-" + str(idx)
                node_to_node[(ent_hash_key, pg_hash_key)] = node_to_node.get((ent_hash_key, pg_hash_key), 0) + 1.0

    @staticmethod
    def _synonym_connect(
        node_to_node: Dict[Tuple[str, str], float],
        triple_list_data: Dict[str, List[List[str]]],
        embedding_manager: EmbeddingManager,
    ) -> int:
        """同义词连接"""
        new_edge_cnt = 0
        # 获取所有实体节点的hash值
        ent_hash_list = set()
        for triple_list in triple_list_data.values():
            for triple in triple_list:
                ent_hash_list.add("entity" + "-" + get_sha256(triple[0]))
                ent_hash_list.add("entity" + "-" + get_sha256(triple[2]))
        ent_hash_list = list(ent_hash_list)
        # 性能保护：限制同义连接的实体数量
        max_synonym_entities = global_config.lpmm_knowledge.max_synonym_entities
        if max_synonym_entities and len(ent_hash_list) > max_synonym_entities:
            logger.warning(
                f"同义连接实体数 {len(ent_hash_list)} 超过阈值 {max_synonym_entities}，跳过同义边构建以保护性能"
            )
            return 0

        synonym_hash_set = set()
        synonym_result = {}

        # rich 进度条
        total = len(ent_hash_list)
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
            task = progress.add_task("同义词连接", total=total)
            for ent_hash in ent_hash_list:
                if ent_hash in synonym_hash_set:
                    progress.update(task, advance=1)
                    continue
                ent = embedding_manager.entities_embedding_store.store.get(ent_hash)
                if ent is None:
                    progress.update(task, advance=1)
                    continue
                assert isinstance(ent, EmbeddingStoreItem)
                # 查询相似实体
                similar_ents = embedding_manager.entities_embedding_store.search_top_k(
                    ent.embedding, global_config.lpmm_knowledge.rag_synonym_search_top_k
                )
                res_ent = []  # Debug
                for res_ent_hash, similarity in similar_ents:
                    if res_ent_hash == ent_hash:
                        # 避免自连接
                        continue
                    if similarity < global_config.lpmm_knowledge.rag_synonym_threshold:
                        # 相似度阈值
                        continue
                    node_to_node[(res_ent_hash, ent_hash)] = similarity
                    node_to_node[(ent_hash, res_ent_hash)] = similarity
                    synonym_hash_set.add(res_ent_hash)
                    new_edge_cnt += 1
                    res_ent.append(
                        (
                            embedding_manager.entities_embedding_store.store[res_ent_hash].str,
                            similarity,
                        )
                    )  # Debug
                    synonym_result[ent.str] = res_ent
                progress.update(task, advance=1)

        for k, v in synonym_result.items():
            print(f'"{k}"的相似实体为：{v}')
        return new_edge_cnt

    def _update_graph(
        self,
        node_to_node: Dict[Tuple[str, str], float],
        embedding_manager: EmbeddingManager,
    ):
        """更新KG图结构

        流程：
        1. 更新图结构：遍历所有待添加的新边
            - 若是新边，则添加到图中
            - 若是已存在的边，则更新边的权重
        2. 更新新节点的属性
        """
        existed_nodes = self.graph.get_node_list()
        existed_edges = [str((edge[0], edge[1])) for edge in self.graph.get_edge_list()]

        now_time = time.time()

        # 更新图结构
        for src_tgt, weight in node_to_node.items():
            key = str(src_tgt)
            # 检查边是否已存在
            if key not in existed_edges:
                # 新边
                self.graph.add_edge(
                    di_graph.DiEdge(
                        src_tgt[0],
                        src_tgt[1],
                        {
                            "weight": weight,
                            "create_time": now_time,
                            "update_time": now_time,
                        },
                    )
                )
            else:
                # 已存在的边
                edge_item = self.graph[src_tgt[0], src_tgt[1]]
                edge_item["weight"] += weight
                edge_item["update_time"] = now_time
                self.graph.update_edge(edge_item)

        # 更新新节点属性
        for src_tgt in node_to_node.keys():
            for node_hash in src_tgt:
                if node_hash not in existed_nodes:
                    if node_hash.startswith("entity"):
                        # 新增实体节点
                        node = embedding_manager.entities_embedding_store.store.get(node_hash)
                        if node is None:
                            logger.warning(f"实体节点 {node_hash} 在嵌入库中不存在，跳过")
                            continue
                        assert isinstance(node, EmbeddingStoreItem)
                        node_item = self.graph[node_hash]
                        node_item["content"] = node.str
                        node_item["type"] = "ent"
                        node_item["create_time"] = now_time
                        self.graph.update_node(node_item)
                    elif node_hash.startswith("paragraph"):
                        # 新增文段节点
                        node = embedding_manager.paragraphs_embedding_store.store.get(node_hash)
                        if node is None:
                            logger.warning(f"段落节点 {node_hash} 在嵌入库中不存在，跳过")
                            continue
                        assert isinstance(node, EmbeddingStoreItem)
                        content = node.str.replace("\n", " ")
                        node_item = self.graph[node_hash]
                        node_item["content"] = content if len(content) < 8 else content[:8] + "..."
                        node_item["type"] = "pg"
                        node_item["create_time"] = now_time
                        self.graph.update_node(node_item)

    def build_kg(
        self,
        triple_list_data: Dict[str, List[List[str]]],
        embedding_manager: EmbeddingManager,
    ):
        """增量式构建KG

        注意：应当在调用该方法后保存KG

        Args:
            triple_list_data: 三元组数据
            embedding_manager: EmbeddingManager对象
        """
        # 实体之间的联系
        node_to_node = dict()

        # 构建实体节点之间的关系，同时统计实体出现次数
        logger.info("正在构建KG实体节点之间的关系，同时统计实体出现次数")
        # 从三元组提取实体对
        self._build_edges_between_ent(node_to_node, triple_list_data)

        # 构建实体节点与文段节点之间的关系
        logger.info("正在构建KG实体节点与文段节点之间的关系")
        self._build_edges_between_ent_pg(node_to_node, triple_list_data)

        # 近义词扩展链接
        # 对每个实体节点，找到最相似的实体节点，建立扩展连接
        logger.info("正在进行近义词扩展链接")
        self._synonym_connect(node_to_node, triple_list_data, embedding_manager)

        # 构建图
        self._update_graph(node_to_node, embedding_manager)

        # 记录已处理（存储）的段落hash
        for idx in triple_list_data:
            self.stored_paragraph_hashes.add(str(idx))

    def kg_search(
        self,
        relation_search_result: List[Tuple[Tuple[str, str, str], float]],
        paragraph_search_result: List[Tuple[str, float]],
        embed_manager: EmbeddingManager,
    ):
        """RAG搜索与PageRank

        Args:
            relation_search_result: RelationEmbedding的搜索结果（relation_tripple, similarity）
            paragraph_search_result: ParagraphEmbedding的搜索结果（paragraph_hash, similarity）
            embed_manager: EmbeddingManager对象
        """
        # 图中存在的节点总集
                # 性能保护：超限或关闭时直接返回向量检索结果
        if (
            not global_config.lpmm_knowledge.enable_ppr
            or len(self.graph.get_node_list()) > global_config.lpmm_knowledge.ppr_node_cap
            or len(relation_search_result) > global_config.lpmm_knowledge.ppr_relation_cap
        ):
            logger.info("PPR 已禁用或超出阈值，使用纯向量检索结果")
            return paragraph_search_result, None
        existed_nodes = self.graph.get_node_list()

        # 准备PPR使用的数据
        # 节点权重：实体
        ent_weights = {}
        # 节点权重：文段
        pg_weights = {}

        # 以下部分处理实体权重ent_weights

        # 针对每个关系，提取出其中的主宾短语作为两个实体，并记录对应的三元组的相似度作为权重依据
        ent_sim_scores = {}
        for relation_hash, similarity, _ in relation_search_result:
            # 提取主宾短语
            relation = embed_manager.relation_embedding_store.store.get(relation_hash).str
            assert relation is not None  # 断言：relation不为空
            # 关系三元组
            triple = relation[2:-2].split("', '")
            for ent in [(triple[0]), (triple[2])]:
                ent_hash = "entity" + "-" + get_sha256(ent)
                if ent_hash in existed_nodes:  # 该实体需在KG中存在
                    if ent_hash not in ent_sim_scores:  # 尚未记录的实体
                        ent_sim_scores[ent_hash] = []
                    ent_sim_scores[ent_hash].append(similarity)

        ent_mean_scores = {}  # 记录实体的平均相似度
        for ent_hash, scores in ent_sim_scores.items():
            # 先对相似度进行累加，然后与实体计数相除获取最终权重
            # 保护：有些实体在当前图中可能只有实体-实体关系，不会出现在 ent_appear_cnt 中
            appear_cnt = self.ent_appear_cnt.get(ent_hash)
            if not appear_cnt or appear_cnt <= 0:
                logger.debug(
                    f"实体 {ent_hash} 在 ent_appear_cnt 中不存在或计数为 0，"
                    f"将使用 1.0 作为默认出现次数参与权重计算"
                )
                appear_cnt = 1.0
            ent_weights[ent_hash] = float(np.sum(scores)) / float(appear_cnt)
            # 记录实体的平均相似度，用于后续的top_k筛选
            ent_mean_scores[ent_hash] = float(np.mean(scores))
        del ent_sim_scores

        ent_weights_max = max(ent_weights.values())
        ent_weights_min = min(ent_weights.values())
        if ent_weights_max == ent_weights_min:
            # 只有一个相似度，则全赋值为1
            for ent_hash in ent_weights.keys():
                ent_weights[ent_hash] = 1.0
        else:
            down_edge = global_config.lpmm_knowledge.qa_paragraph_node_weight
            # 缩放取值区间至[down_edge, 1]
            for ent_hash, score in ent_weights.items():
                # 缩放相似度
                ent_weights[ent_hash] = (
                    (score - ent_weights_min) * (1 - down_edge) / (ent_weights_max - ent_weights_min)
                ) + down_edge

        # 取平均相似度的top_k实体
        top_k = global_config.lpmm_knowledge.qa_ent_filter_top_k
        if len(ent_mean_scores) > top_k:
            # 从大到小排序，取后len - k个
            ent_mean_scores = {k: v for k, v in sorted(ent_mean_scores.items(), key=lambda item: item[1], reverse=True)}
            for ent_hash, _ in ent_mean_scores.items():
                # 删除被淘汰的实体节点权重设置
                del ent_weights[ent_hash]
        del top_k, ent_mean_scores

        # 以下部分处理文段权重pg_weights

        # 将搜索结果中文段的相似度归一化作为权重
        pg_sim_scores = {}
        pg_sim_score_max = 0.0
        pg_sim_score_min = 1.0
        for pg_hash, similarity in paragraph_search_result:
            # 查找最大和最小值
            pg_sim_score_max = max(pg_sim_score_max, similarity)
            pg_sim_score_min = min(pg_sim_score_min, similarity)
            pg_sim_scores[pg_hash] = similarity

        # 归一化
        for pg_hash, similarity in pg_sim_scores.items():
            # 归一化相似度
            pg_sim_scores[pg_hash] = (similarity - pg_sim_score_min) / (pg_sim_score_max - pg_sim_score_min)
        del pg_sim_score_max, pg_sim_score_min

        for pg_hash, score in pg_sim_scores.items():
            pg_weights[pg_hash] = (
                score * global_config.lpmm_knowledge.qa_paragraph_node_weight
            )  # 文段权重 = 归一化相似度 * 文段节点权重参数
        del pg_sim_scores

        # 最终权重数据 = 实体权重 + 文段权重
        ppr_node_weights = {k: v for d in [ent_weights, pg_weights] for k, v in d.items()}
        del ent_weights, pg_weights

        # PersonalizedPageRank
        ppr_res = pagerank.run_pagerank(
            self.graph,
            personalization=ppr_node_weights,
            max_iter=100,
            alpha=global_config.lpmm_knowledge.qa_ppr_damping,
        )

        # 获取最终结果
        # 从搜索结果中提取文段节点的结果
        passage_node_res = [
            (node_key, score) for node_key, score in ppr_res.items() if node_key.startswith("paragraph")
        ]
        del ppr_res

        # 排序：按照分数从大到小
        passage_node_res = sorted(passage_node_res, key=lambda item: item[1], reverse=True)

        return passage_node_res, ppr_node_weights

    def delete_paragraphs(
        self,
        pg_hashes: List[str],
        ent_hashes: List[str] | None = None,
        remove_orphan_entities: bool = False,
    ) -> Dict[str, int]:
        """删除段落/实体节点及相关边（基于 GraphML），可选清理孤立实体，并重建元数据"""
        # 要删除的节点 ID
        nodes_to_delete: Set[str] = {f"paragraph-{h}" for h in pg_hashes}
        if ent_hashes:
            nodes_to_delete.update({f"entity-{h}" for h in ent_hashes})

        if not os.path.exists(self.graph_data_path):
            raise FileNotFoundError(f"KG图文件{self.graph_data_path}不存在")

        tree = ET.parse(self.graph_data_path)
        root = tree.getroot()

        # GraphML 可能带命名空间，用尾缀判断
        def is_node(elem: ET.Element) -> bool:
            return elem.tag.endswith("node")

        def is_edge(elem: ET.Element) -> bool:
            return elem.tag.endswith("edge")

        graph_elem = None
        for child in root:
            if child.tag.endswith("graph"):
                graph_elem = child
                break
        if graph_elem is None:
            raise RuntimeError("GraphML 中未找到 <graph> 节点")

        # 统计现有节点
        existing_nodes: Set[str] = set()
        for elem in graph_elem:
            if is_node(elem):
                node_id = elem.get("id")
                if node_id:
                    existing_nodes.add(node_id)

        deleted_nodes = len(nodes_to_delete & existing_nodes)
        skipped_nodes = len(nodes_to_delete - existing_nodes)

        # 先删除指定节点及相关边
        # 删除节点
        for elem in list(graph_elem):
            if is_node(elem):
                node_id = elem.get("id")
                if node_id and node_id in nodes_to_delete:
                    graph_elem.remove(elem)

        # 删除 incident edges
        for elem in list(graph_elem):
            if is_edge(elem):
                src = elem.get("source")
                tgt = elem.get("target")
                if src in nodes_to_delete or tgt in nodes_to_delete:
                    graph_elem.remove(elem)

        orphan_removed = 0
        if remove_orphan_entities:
            # 计算仍然参与边的节点
            used_nodes: Set[str] = set()
            for elem in graph_elem:
                if is_edge(elem):
                    src = elem.get("source")
                    tgt = elem.get("target")
                    if src:
                        used_nodes.add(src)
                    if tgt:
                        used_nodes.add(tgt)

            # 找出没有任何边的实体节点
            orphan_entities: Set[str] = set()
            for elem in graph_elem:
                if is_node(elem):
                    node_id = elem.get("id")
                    if node_id and node_id.startswith("entity") and node_id not in used_nodes:
                        orphan_entities.add(node_id)

            orphan_removed = len(orphan_entities)

            if orphan_entities:
                # 删除孤立实体节点
                for elem in list(graph_elem):
                    if is_node(elem):
                        node_id = elem.get("id")
                        if node_id in orphan_entities:
                            graph_elem.remove(elem)

                # 删除与孤立实体相关的边（理论上已无，但做一次防御性清理）
                for elem in list(graph_elem):
                    if is_edge(elem):
                        src = elem.get("source")
                        tgt = elem.get("target")
                        if src in orphan_entities or tgt in orphan_entities:
                            graph_elem.remove(elem)

        # 写回 GraphML
        tree.write(self.graph_data_path, encoding="utf-8", xml_declaration=True)

        # 重新加载图并重建元数据
        self.graph = di_graph.load_from_file(self.graph_data_path)
        self._rebuild_metadata_from_graph()

        return {
            "deleted": deleted_nodes,
            "skipped": skipped_nodes,
            "orphan_removed": orphan_removed,
        }
