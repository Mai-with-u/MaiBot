#!/usr/bin/env python3
"""
LPMM 到 A_memorix 存储转换器

功能：
1. 读取 LPMM parquet 文件 (paragraph.parquet, entity.parquet, relation.parquet)
2. 读取 LPMM 图文件 (graph.graphml 或 graph_structure.pkl)
3. 直接写入 A_memorix 二进制 VectorStore 和稀疏 GraphStore
4. 绕过 Embedding 生成以节省 Token
"""

import sys
import os
import json
import argparse
import asyncio
import pickle
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np
import tomlkit

# 设置路径
current_dir = Path(__file__).resolve().parent
plugin_root = current_dir.parent
project_root = plugin_root.parent.parent
sys.path.insert(0, str(project_root))

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 LPMM 数据转换为 A_memorix 格式")
    parser.add_argument("--input", "-i", required=True, help="包含 LPMM 数据的输入目录 (parquet, graphml)")
    parser.add_argument("--output", "-o", required=True, help="A_memorix 数据的输出目录")
    parser.add_argument("--dim", type=int, default=384, help="Embedding 维度 (必须与 LPMM 模型匹配)")
    parser.add_argument("--batch-size", type=int, default=1024, help="Parquet 分批读取大小 (默认 1024)")
    parser.add_argument(
        "--skip-relation-vector-rebuild",
        action="store_true",
        help="跳过按关系元数据重建关系向量（默认开启）",
    )
    return parser


# --help/-h fast path: avoid heavy host/plugin bootstrap
if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    sys.exit(0)

# 设置日志：优先复用 MaiBot 统一日志体系，失败时回退到标准 logging。
try:
    from src.common.logger import get_logger

    logger = get_logger("A_Memorix.LPMMConverter")
except Exception:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger = logging.getLogger("A_Memorix.LPMMConverter")

try:
    import networkx as nx
    from scipy import sparse
    import pyarrow.parquet as pq
except ImportError as e:
    logger.error(f"缺少依赖: {e}")
    logger.error("请安装: pip install pandas pyarrow networkx scipy")
    sys.exit(1)

try:
    # 优先采取相对导入 (将插件根目录加入路径)
    # 这样可以避免硬编码插件名称 (plugins.A_memorix)
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    
    from core.storage.vector_store import VectorStore
    from core.storage.graph_store import GraphStore
    from core.storage.metadata_store import MetadataStore
    from core.storage import QuantizationType, SparseMatrixFormat
    from core.embedding import create_embedding_api_adapter
    from core.utils.relation_write_service import RelationWriteService
    
except ImportError as e:
    logger.error(f"无法导入 A_memorix 核心模块: {e}")
    logger.error("请确保在正确的环境中运行，且已安装所有依赖。")
    sys.exit(1)


class LPMMConverter:
    def __init__(
        self,
        lpmm_data_dir: Path,
        output_dir: Path,
        dimension: int = 384,
        batch_size: int = 1024,
        rebuild_relation_vectors: bool = True,
    ):
        self.lpmm_dir = lpmm_data_dir
        self.output_dir = output_dir
        self.dimension = dimension
        self.batch_size = max(1, int(batch_size))
        self.rebuild_relation_vectors = bool(rebuild_relation_vectors)
        
        self.vector_dir = output_dir / "vectors"
        self.graph_dir = output_dir / "graph"
        self.metadata_dir = output_dir / "metadata"
        
        self.vector_store = None
        self.graph_store = None
        self.metadata_store = None
        self.embedding_manager = None
        self.relation_write_service = None
        # LPMM 原 ID -> A_memorix ID 映射（用于图重写）
        self.id_mapping: Dict[str, str] = {}

    def _register_id_mapping(self, raw_id: Any, mapped_id: str, p_type: str) -> None:
        """记录 ID 映射，兼容带/不带类型前缀两种格式。"""
        if raw_id is None:
            return

        raw = str(raw_id).strip()
        if not raw:
            return

        self.id_mapping[raw] = mapped_id

        prefix = f"{p_type}-"
        if raw.startswith(prefix):
            self.id_mapping[raw[len(prefix):]] = mapped_id
        else:
            self.id_mapping[prefix + raw] = mapped_id

    def _map_node_id(self, node: Any) -> str:
        """将图节点 ID 映射到转换后的 A_memorix ID。"""
        node_key = str(node)
        return self.id_mapping.get(node_key, node_key)
        
    def initialize_stores(self):
        """初始化空的 A_memorix 存储"""
        logger.info(f"正在初始化存储于 {self.output_dir}...")
        
        # 初始化 VectorStore (A_memorix 默认使用 INT8 量化)
        self.vector_store = VectorStore(
            dimension=self.dimension,
            quantization_type=QuantizationType.INT8,
            data_dir=self.vector_dir
        )
        self.vector_store.clear() # 清空旧数据
        
        # 初始化 GraphStore (使用 CSR 格式)
        self.graph_store = GraphStore(
            matrix_format=SparseMatrixFormat.CSR,
            data_dir=self.graph_dir
        )
        self.graph_store.clear()
        
        # 初始化 MetadataStore
        self.metadata_store = MetadataStore(data_dir=self.metadata_dir)
        self.metadata_store.connect()
        # 清空元数据表？理想情况下是的，但要小心。
        # 对于转换，我们假设是全新的开始或覆盖。
        # A_memorix 中的 MetadataStore 通常使用 SQLite。
        # 如果目录是新的，我们会依赖它创建新文件。
        if self.rebuild_relation_vectors:
            self._init_relation_vector_service()

    def _load_plugin_config(self) -> Dict[str, Any]:
        config_path = plugin_root / "config.toml"
        if not config_path.exists():
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                parsed = tomlkit.load(f)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception as e:
            logger.warning(f"读取 config.toml 失败，使用默认 embedding 配置: {e}")
            return {}

    def _init_relation_vector_service(self) -> None:
        if not self.rebuild_relation_vectors:
            return
        cfg = self._load_plugin_config()
        emb_cfg = cfg.get("embedding", {}) if isinstance(cfg, dict) else {}
        if not isinstance(emb_cfg, dict):
            emb_cfg = {}
        try:
            self.embedding_manager = create_embedding_api_adapter(
                batch_size=int(emb_cfg.get("batch_size", 32)),
                max_concurrent=int(emb_cfg.get("max_concurrent", 5)),
                default_dimension=int(emb_cfg.get("dimension", self.dimension)),
                model_name=str(emb_cfg.get("model_name", "auto")),
                retry_config=emb_cfg.get("retry", {}) if isinstance(emb_cfg.get("retry", {}), dict) else {},
            )
            self.relation_write_service = RelationWriteService(
                metadata_store=self.metadata_store,
                graph_store=self.graph_store,
                vector_store=self.vector_store,
                embedding_manager=self.embedding_manager,
            )
        except Exception as e:
            self.embedding_manager = None
            self.relation_write_service = None
            logger.warning(f"初始化关系向量重建服务失败，将跳过关系向量回填: {e}")

    async def _rebuild_relation_vectors(self) -> None:
        if not self.rebuild_relation_vectors:
            return
        if self.relation_write_service is None:
            logger.warning("关系向量重建已启用，但写入服务不可用，已跳过。")
            return

        rows = self.metadata_store.get_relations()
        if not rows:
            logger.info("未发现关系元数据，无需重建关系向量。")
            return

        success = 0
        failed = 0
        skipped = 0
        for row in rows:
            result = await self.relation_write_service.ensure_relation_vector(
                hash_value=str(row["hash"]),
                subject=str(row.get("subject", "")),
                predicate=str(row.get("predicate", "")),
                obj=str(row.get("object", "")),
            )
            if result.vector_state == "ready":
                if result.vector_written:
                    success += 1
                else:
                    skipped += 1
            else:
                failed += 1

        logger.info(
            "关系向量重建完成: "
            f"total={len(rows)} "
            f"success={success} "
            f"skipped={skipped} "
            f"failed={failed}"
        )

    @staticmethod
    def _parse_relation_text(text: str) -> Tuple[str, str, str]:
        raw = str(text or "").strip()
        if not raw:
            return "", "", ""
        if "|" in raw:
            parts = [p.strip() for p in raw.split("|") if p.strip()]
            if len(parts) >= 3:
                return parts[0], parts[1], parts[2]
        if "->" in raw:
            parts = [p.strip() for p in raw.split("->") if p.strip()]
            if len(parts) >= 3:
                return parts[0], parts[1], parts[2]
        pieces = raw.split()
        if len(pieces) >= 3:
            return pieces[0], pieces[1], " ".join(pieces[2:])
        return "", "", ""

    def _import_relation_metadata_from_parquet(self, relation_path: Path) -> int:
        if not relation_path.exists():
            return 0

        try:
            parquet_file = pq.ParquetFile(relation_path)
        except Exception as e:
            logger.warning(f"读取 relation.parquet 失败，跳过关系元数据导入: {e}")
            return 0

        cols = set(parquet_file.schema_arrow.names)
        has_triple_cols = {"subject", "predicate", "object"}.issubset(cols)
        content_col = "str" if "str" in cols else ("content" if "content" in cols else "")

        imported_hashes = set()
        imported = 0
        for record_batch in parquet_file.iter_batches(batch_size=self.batch_size):
            df_batch = record_batch.to_pandas()
            for _, row in df_batch.iterrows():
                subject = ""
                predicate = ""
                obj = ""
                if has_triple_cols:
                    subject = str(row.get("subject", "") or "").strip()
                    predicate = str(row.get("predicate", "") or "").strip()
                    obj = str(row.get("object", "") or "").strip()
                elif content_col:
                    subject, predicate, obj = self._parse_relation_text(row.get(content_col, ""))

                if not (subject and predicate and obj):
                    continue

                rel_hash = self.metadata_store.add_relation(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    source_paragraph=None,
                )
                if rel_hash in imported_hashes:
                    continue
                imported_hashes.add(rel_hash)
                self.graph_store.add_edges([(subject, obj)], relation_hashes=[rel_hash])
                try:
                    self.metadata_store.set_relation_vector_state(rel_hash, "none")
                except Exception:
                    pass
                imported += 1

        return imported
        
    def convert_vectors(self):
        """将 Parquet 向量转换为 VectorStore"""
        # LPMM 默认文件名
        parquet_files = {
            "paragraph": self.lpmm_dir / "paragraph.parquet",
            "entity": self.lpmm_dir / "entity.parquet",
            "relation": self.lpmm_dir / "relation.parquet"
        }
        
        total_vectors = 0
        
        for p_type, p_path in parquet_files.items():
            # 关系向量在当前脚本中无法保证与 MetadataStore 的关系记录一一对应，
            # 直接导入会污染召回结果（命中后无法反查 relation 元数据）。
            if p_type == "relation":
                relation_count = self._import_relation_metadata_from_parquet(p_path)
                logger.warning(
                    "跳过 relation.parquet 向量导入（保持一致性）；"
                    f"已导入关系元数据: {relation_count}"
                )
                continue

            if not p_path.exists():
                logger.warning(f"文件未找到: {p_path}, 跳过 {p_type} 向量。")
                continue
                
            logger.info(f"正在处理 {p_type} 向量，来源: {p_path}...")
            try:
                parquet_file = pq.ParquetFile(p_path)
                total_rows = parquet_file.metadata.num_rows
                if total_rows == 0:
                    logger.info(f"{p_path} 为空，跳过。")
                    continue

                # LPMM Schema: 'hash', 'embedding', 'str'
                cols = parquet_file.schema_arrow.names
                # 兼容性检查
                content_col = 'str' if 'str' in cols else 'content'
                emb_col = 'embedding'
                hash_col = 'hash'
                
                if content_col not in cols or emb_col not in cols:
                    logger.error(f"{p_path} 中缺少必要列 (需包含 {content_col}, {emb_col})。发现: {cols}")
                    continue
                
                batch_columns = [content_col, emb_col]
                if hash_col in cols:
                    batch_columns.append(hash_col)

                processed_rows = 0
                added_for_type = 0
                batch_idx = 0

                for record_batch in parquet_file.iter_batches(
                    batch_size=self.batch_size,
                    columns=batch_columns,
                ):
                    batch_idx += 1
                    df_batch = record_batch.to_pandas()

                    embeddings_list = []
                    ids_list = []

                    # 同时处理元数据映射
                    for _, row in df_batch.iterrows():
                        processed_rows += 1
                        content = row[content_col]
                        emb = row[emb_col]

                        if content is None or (isinstance(content, float) and np.isnan(content)):
                            continue
                        content = str(content).strip()
                        if not content:
                            continue

                        if emb is None or len(emb) == 0:
                            continue

                        # 先写 MetadataStore，并使用其返回的真实 hash 作为向量 ID
                        # 保证检索返回 ID 可以直接反查元数据。
                        store_id = None
                        if p_type == "paragraph":
                            store_id = self.metadata_store.add_paragraph(
                                content=content,
                                source="lpmm_import",
                                knowledge_type="factual",
                            )
                        elif p_type == "entity":
                            store_id = self.metadata_store.add_entity(name=content)
                        else:
                            continue

                        raw_hash = row[hash_col] if hash_col in df_batch.columns else None
                        if raw_hash is not None and not (isinstance(raw_hash, float) and np.isnan(raw_hash)):
                            self._register_id_mapping(raw_hash, store_id, p_type)
                        
                        # 确保 embedding 是 numpy 数组
                        emb_np = np.array(emb, dtype=np.float32)
                        if emb_np.shape[0] != self.dimension:
                            logger.error(f"维度不匹配: {emb_np.shape[0]} vs {self.dimension}")
                            continue
                            
                        embeddings_list.append(emb_np)
                        ids_list.append(store_id)
                    
                    if embeddings_list:
                        # 分批添加到向量存储
                        vectors_np = np.stack(embeddings_list)
                        count = self.vector_store.add(vectors_np, ids_list)
                        added_for_type += count
                        total_vectors += count

                    if batch_idx == 1 or batch_idx % 10 == 0:
                        logger.info(
                            f"[{p_type}] 批次 {batch_idx}: 已扫描 {processed_rows}/{total_rows}, 已导入 {added_for_type}"
                        )

                logger.info(
                    f"{p_type} 向量处理完成：总扫描 {processed_rows}，总导入 {added_for_type}"
                )
                    
            except Exception as e:
                logger.error(f"处理 {p_path} 时出错: {e}")
                
        # 提交向量存储
        self.vector_store.save()
        logger.info(f"向量转换完成。总向量数: {total_vectors}")

    def convert_graph(self):
        """将 LPMM 图转换为 GraphStore"""
        # LPMM 默认文件名是 rag-graph.graphml
        graph_files = [
            self.lpmm_dir / "rag-graph.graphml",
            self.lpmm_dir / "graph.graphml",
            self.lpmm_dir / "graph_structure.pkl"
        ]
        
        nx_graph = None
        
        for g_path in graph_files:
            if g_path.exists():
                logger.info(f"发现图文件: {g_path}")
                try:
                    if g_path.suffix == ".graphml":
                        nx_graph = nx.read_graphml(g_path)
                    elif g_path.suffix == ".pkl":
                        with open(g_path, "rb") as f:
                            data = pickle.load(f)
                            # LPMM 可能会将图存储在包装类中
                            if hasattr(data, "graph") and isinstance(data.graph, nx.Graph):
                                nx_graph = data.graph
                            elif isinstance(data, nx.Graph):
                                nx_graph = data
                    break
                except Exception as e:
                    logger.error(f"加载 {g_path} 失败: {e}")
        
        if nx_graph is None:
            logger.warning("未找到有效的图文件。跳过图转换。")
            return

        logger.info(f"已加载图，包含 {nx_graph.number_of_nodes()} 个节点和 {nx_graph.number_of_edges()} 条边。")
        
        # 1. 添加节点
        # LPMM 节点通常是哈希或带前缀的字符串。
        # 我们需要将它们映射到 A_memorix 格式。
        # 如果 LPMM 使用 "entity-HASH"，则与 A_memorix 匹配。
        
        nodes_to_add = []
        node_attrs = {}
        
        for node, attrs in nx_graph.nodes(data=True):
            # 假设 LPMM 使用一致的命名 "entity-..." 或 "paragraph-..."
            mapped_node = self._map_node_id(node)
            nodes_to_add.append(mapped_node)
            if attrs:
                node_attrs[mapped_node] = attrs
        
        self.graph_store.add_nodes(nodes_to_add, node_attrs)
        
        # 2. 添加边
        edges_to_add = []
        weights = []
        
        for u, v, data in nx_graph.edges(data=True):
            weight = data.get("weight", 1.0)
            edges_to_add.append((self._map_node_id(u), self._map_node_id(v)))
            weights.append(float(weight))
            
            # 如果可能，将关系同步到 MetadataStore
            # 但图的边并不总是包含关系谓词
            # 如果 LPMM 边数据有 'predicate'，我们可以添加到元数据
            # 通常 LPMM 边是加权和，谓词信息可能在简单图中丢失
            
        if edges_to_add:
            self.graph_store.add_edges(edges_to_add, weights)
            
        self.graph_store.save()
        logger.info("图转换完成。")

    def run(self):
        self.initialize_stores()
        self.convert_vectors()
        self.convert_graph()
        asyncio.run(self._rebuild_relation_vectors())
        self.vector_store.save()
        self.graph_store.save()
        self.metadata_store.close()
        logger.info("所有转换成功完成。")


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    
    if not input_path.exists():
        logger.error(f"输入目录不存在: {input_path}")
        sys.exit(1)
        
    converter = LPMMConverter(
        input_path,
        output_path,
        dimension=args.dim,
        batch_size=args.batch_size,
        rebuild_relation_vectors=not bool(args.skip_relation_vector_rebuild),
    )
    converter.run()

if __name__ == "__main__":
    main()
