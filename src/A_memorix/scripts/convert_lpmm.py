#!/usr/bin/env python3
"""
LPMM 到 A_memorix 存储转换器

功能：
1. 读取 LPMM parquet 文件 (paragraph.parquet, entity.parquet, relation.parquet)
2. 读取 LPMM 图文件 (graph.graphml 或 graph_structure.pkl)
3. 直接写入 A_memorix paragraph、graph 双向量池和稀疏 GraphStore
4. 绕过 Embedding 生成以节省 Token
"""

from pathlib import Path
from typing import Any, Dict, Tuple

import argparse
import importlib.util
import json
import logging
import pickle
import sys
import time

import numpy as np
import tomlkit

from _bootstrap import DEFAULT_CONFIG_PATH, DEFAULT_DATA_DIR, resolve_repo_path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 LPMM 数据转换为 A_memorix 格式")
    parser.add_argument("--input", "-i", required=True, help="包含 LPMM 数据的输入目录 (parquet, graphml)")
    parser.add_argument("--output", "-o", required=True, help="A_memorix 数据的输出目录")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="A_Memorix 数据目录")
    parser.add_argument("--dim", type=int, default=384, help="Embedding 维度 (必须与 LPMM 模型匹配)")
    parser.add_argument("--batch-size", type=int, default=1024, help="Parquet 分批读取大小 (默认 1024)")
    parser.add_argument(
        "--skip-relation-vector-rebuild",
        action="store_true",
        help="兼容旧参数；当前会直接使用 relation.parquet 中已有的向量",
    )
    parser.add_argument(
        "--allow-unsafe-pickle",
        action="store_true",
        help="允许读取 LPMM graph_structure.pkl。该格式会反序列化 pickle，只应在信任输入来源时开启。",
    )
    return parser


def _resolve_import_path(raw_path: str, root: Path, label: str) -> Path:
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        repo_candidate = resolve_repo_path(candidate)
        try:
            repo_candidate.relative_to(root)
        except ValueError:
            resolved = (root / candidate).resolve()
        else:
            resolved = repo_candidate
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"{label}必须位于导入目录: {root}") from None
    return resolved


# --help/-h 快速路径：避免加载较重的宿主和插件运行时
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
    import pyarrow.parquet as pq

    if importlib.util.find_spec("scipy") is None:
        raise ImportError("No module named 'scipy'")
except ImportError as e:
    logger.error(f"缺少依赖: {e}")
    logger.error("请安装: pip install pandas pyarrow scipy")
    sys.exit(1)

try:
    from A_memorix.core.storage import QuantizationType, SparseMatrixFormat
    from A_memorix.core.storage.graph_store import GraphStore
    from A_memorix.core.storage.metadata_store import MetadataStore
    from A_memorix.core.storage.vector_store import VectorStore
    from A_memorix.core.embedding import create_embedding_api_adapter
    from A_memorix.core.utils.io import atomic_write
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
        allow_unsafe_pickle: bool = False,
    ):
        self.lpmm_dir = lpmm_data_dir
        self.output_dir = output_dir
        self.dimension = dimension
        self.batch_size = max(1, int(batch_size))
        self.allow_unsafe_pickle = bool(allow_unsafe_pickle)

        self.vector_dir = output_dir / "vectors"
        self.paragraph_vector_dir = self.vector_dir / "paragraph"
        self.graph_vector_dir = self.vector_dir / "graph"
        self.graph_dir = output_dir / "graph"
        self.metadata_dir = output_dir / "metadata"

        self.paragraph_vector_store = None
        self.graph_vector_store = None
        self.graph_store = None
        self.metadata_store = None
        self.embedding_fingerprint: Dict[str, Any] = {}
        self.vector_stats = {item_type: 0 for item_type in ("paragraph", "entity", "relation")}
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
            self.id_mapping[raw[len(prefix) :]] = mapped_id
        else:
            self.id_mapping[prefix + raw] = mapped_id

    def _map_node_id(self, node: Any) -> str:
        """将图节点 ID 映射到转换后的 A_memorix ID。"""
        node_key = str(node)
        return self.id_mapping.get(node_key, node_key)

    def initialize_stores(self):
        """初始化空的 A_memorix 存储"""
        logger.info(f"正在初始化存储于 {self.output_dir}...")

        if self.output_dir.exists() and any(self.output_dir.iterdir()):
            raise RuntimeError(f"输出目录必须为空，已拒绝覆盖: {self.output_dir}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_fingerprint = self._load_embedding_fingerprint()

        # LPMM 向量直接按当前双池布局写入，不再生成需要二次迁移的旧单池。
        self.paragraph_vector_store = VectorStore(
            dimension=self.dimension,
            quantization_type=QuantizationType.INT8,
            data_dir=self.paragraph_vector_dir,
        )
        self.graph_vector_store = VectorStore(
            dimension=self.dimension,
            quantization_type=QuantizationType.INT8,
            data_dir=self.graph_vector_dir,
        )

        # 初始化 GraphStore (使用 CSR 格式)
        self.graph_store = GraphStore(matrix_format=SparseMatrixFormat.CSR, data_dir=self.graph_dir)

        # 初始化 MetadataStore
        self.metadata_store = MetadataStore(data_dir=self.metadata_dir)
        self.metadata_store.connect()

    def _validate_input(self) -> None:
        if not self.lpmm_dir.is_dir():
            raise ValueError(f"输入路径必须为目录: {self.lpmm_dir}")
        required_inputs = (self.lpmm_dir / "paragraph.parquet", self.lpmm_dir / "entity.parquet")
        if not any(path.is_file() for path in required_inputs):
            raise ValueError("输入目录至少需要 paragraph.parquet 或 entity.parquet")

    def _load_plugin_config(self) -> Dict[str, Any]:
        config_path = DEFAULT_CONFIG_PATH
        if not config_path.exists():
            raise FileNotFoundError(f"A_Memorix 配置不存在，无法确定 Embedding 指纹: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            parsed = tomlkit.load(f)
        if not isinstance(parsed, dict):
            raise TypeError(f"A_Memorix 配置根节点必须是对象: {config_path}")
        return dict(parsed)

    def _load_embedding_fingerprint(self) -> Dict[str, Any]:
        cfg = self._load_plugin_config()
        emb_cfg = cfg.get("embedding", {}) if isinstance(cfg, dict) else {}
        if not isinstance(emb_cfg, dict):
            emb_cfg = {}
        embedding_manager = create_embedding_api_adapter(
            batch_size=int(emb_cfg.get("batch_size", 32)),
            max_concurrent=int(emb_cfg.get("max_concurrent", 5)),
            default_dimension=self.dimension,
            model_name=str(emb_cfg.get("model_name", "auto")),
            dimension_request_mode=str(emb_cfg.get("dimension_request_mode", "explicit")),
            retry_config=emb_cfg.get("retry", {}) if isinstance(emb_cfg.get("retry", {}), dict) else {},
        )
        fingerprint = embedding_manager.get_embedding_fingerprint(dimension=self.dimension)
        if not isinstance(fingerprint, dict) or not str(fingerprint.get("hash", "") or "").strip():
            raise RuntimeError("无法确定当前 Embedding 指纹，拒绝生成不可加载的双池")
        return dict(fingerprint)

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

    def convert_vectors(self):
        """将 Parquet 中的既有向量直接转换到 paragraph、graph 双池。"""
        # LPMM 默认文件名
        parquet_files = {
            "paragraph": self.lpmm_dir / "paragraph.parquet",
            "entity": self.lpmm_dir / "entity.parquet",
            "relation": self.lpmm_dir / "relation.parquet",
        }

        for p_type, p_path in parquet_files.items():
            if not p_path.exists():
                logger.warning(f"文件未找到: {p_path}, 跳过 {p_type} 向量。")
                continue

            logger.info(f"正在处理 {p_type} 向量，来源: {p_path}...")
            parquet_file = pq.ParquetFile(p_path)
            total_rows = parquet_file.metadata.num_rows
            if total_rows == 0:
                logger.info(f"{p_path} 为空，跳过。")
                continue

            cols = set(parquet_file.schema_arrow.names)
            content_col = "str" if "str" in cols else ("content" if "content" in cols else "")
            has_triple_cols = {"subject", "predicate", "object"}.issubset(cols)
            if "embedding" not in cols:
                raise ValueError(f"{p_path} 缺少 embedding 列: {sorted(cols)}")
            if p_type != "relation" and not content_col:
                raise ValueError(f"{p_path} 缺少 str 或 content 列: {sorted(cols)}")
            if p_type == "relation" and not has_triple_cols and not content_col:
                raise ValueError(f"{p_path} 缺少关系三元组或可解析文本: {sorted(cols)}")

            batch_columns = ["embedding"]
            for column in ("hash", content_col, "subject", "predicate", "object"):
                if column and column in cols and column not in batch_columns:
                    batch_columns.append(column)

            processed_rows = 0
            for record_batch in parquet_file.iter_batches(
                batch_size=self.batch_size,
                columns=batch_columns,
            ):
                df_batch = record_batch.to_pandas()
                embeddings_list = []
                ids_list = []
                relation_hashes = []
                relation_edges = []
                for _, row in df_batch.iterrows():
                    processed_rows += 1
                    emb = row["embedding"]
                    emb_np = np.asarray(emb, dtype=np.float32)
                    if emb_np.shape != (self.dimension,):
                        raise ValueError(
                            f"{p_type} 第 {processed_rows} 行向量维度不匹配: "
                            f"{emb_np.shape} vs ({self.dimension},)"
                        )
                    if not np.all(np.isfinite(emb_np)):
                        raise ValueError(f"{p_type} 第 {processed_rows} 行向量包含非有限值")

                    if p_type == "relation":
                        if has_triple_cols:
                            subject = str(row.get("subject", "") or "").strip()
                            predicate = str(row.get("predicate", "") or "").strip()
                            obj = str(row.get("object", "") or "").strip()
                        else:
                            subject, predicate, obj = self._parse_relation_text(row.get(content_col, ""))
                        if not (subject and predicate and obj):
                            raise ValueError(f"relation 第 {processed_rows} 行无法解析为完整三元组")
                        store_id = self.metadata_store.add_relation(
                            subject=subject,
                            predicate=predicate,
                            obj=obj,
                            source_paragraph=None,
                        )
                        vector_id = f"relation:{store_id}"
                        relation_hashes.append(store_id)
                        relation_edges.append((subject, obj))
                    else:
                        raw_content = row[content_col]
                        content = str(raw_content or "").strip()
                        if not content:
                            raise ValueError(f"{p_type} 第 {processed_rows} 行内容为空")
                        if p_type == "paragraph":
                            store_id = self.metadata_store.add_paragraph(
                                content=content,
                                source="lpmm_import",
                                knowledge_type="factual",
                            )
                            vector_id = store_id
                        else:
                            store_id = self.metadata_store.add_entity(name=content)
                            vector_id = f"entity:{store_id}"

                    raw_hash = row["hash"] if "hash" in df_batch.columns else None
                    if raw_hash is not None and not (isinstance(raw_hash, float) and np.isnan(raw_hash)):
                        self._register_id_mapping(raw_hash, store_id, p_type)
                    embeddings_list.append(emb_np)
                    ids_list.append(vector_id)

                if embeddings_list:
                    target_store = (
                        self.paragraph_vector_store if p_type == "paragraph" else self.graph_vector_store
                    )
                    added = target_store.add(np.stack(embeddings_list), ids_list)
                    self.vector_stats[p_type] += int(added)
                    if relation_edges:
                        self.graph_store.add_edges(relation_edges, relation_hashes=relation_hashes)
                        for relation_hash in relation_hashes:
                            self.metadata_store.set_relation_vector_state(relation_hash, "ready")

            logger.info(
                f"{p_type} 向量处理完成：总扫描 {processed_rows}，总导入 {self.vector_stats[p_type]}"
            )

        total_vectors = sum(self.vector_stats.values())
        if total_vectors <= 0:
            raise RuntimeError("LPMM 输入没有产生任何可用向量，拒绝发布 ready 标志")
        logger.info(f"向量转换完成。总向量数: {total_vectors}")

    def convert_graph(self):
        """将 LPMM 图转换为 GraphStore"""
        # LPMM 默认文件名是 rag-graph.graphml
        graph_files = [
            self.lpmm_dir / "rag-graph.graphml",
            self.lpmm_dir / "graph.graphml",
            self.lpmm_dir / "graph_structure.pkl",
        ]

        existing_graph_files = [
            path
            for path in graph_files
            if path.exists() and (path.suffix != ".pkl" or self.allow_unsafe_pickle)
        ]
        if not existing_graph_files:
            logger.warning("未找到图文件。关系 Parquet 生成的图仍会保留。")
            return
        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("检测到 LPMM 图文件，但未安装可选依赖 networkx") from exc

        nx_graph = None

        for g_path in existing_graph_files:
            logger.info(f"发现图文件: {g_path}")
            if g_path.suffix == ".graphml":
                nx_graph = nx.read_graphml(g_path)
            elif g_path.suffix == ".pkl":
                if not self.allow_unsafe_pickle:
                    logger.warning(
                        f"跳过不安全的 pickle 图文件: {g_path}。 如确认来源可信，可添加 --allow-unsafe-pickle。"
                    )
                    continue
                with open(g_path, "rb") as f:
                    data = pickle.load(f)
                    # LPMM 可能会将图存储在包装类中
                    if hasattr(data, "graph") and isinstance(data.graph, nx.Graph):
                        nx_graph = data.graph
                    elif isinstance(data, nx.Graph):
                        nx_graph = data
            if nx_graph is not None:
                break

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

        logger.info("图转换完成。")

    def _write_dual_ready_manifest(self) -> None:
        paragraph_count = int(self.paragraph_vector_store.num_vectors)
        graph_count = int(self.graph_vector_store.num_vectors)
        stats = {
            "paragraphs": {"done": int(self.vector_stats["paragraph"]), "failed": 0},
            "entities": {"done": int(self.vector_stats["entity"]), "failed": 0},
            "relations": {"done": int(self.vector_stats["relation"]), "failed": 0},
        }
        payload = {
            "status": "ready",
            "version": 1,
            "mode": "dual",
            "dimension": self.dimension,
            "created_at": time.time(),
            "paragraph_vectors": paragraph_count,
            "graph_vectors": graph_count,
            "stats": stats,
            "migration": {
                item_type: {
                    "copied": int(self.vector_stats[vector_type]),
                    "encoded": 0,
                    "missing": 0,
                }
                for item_type, vector_type in (
                    ("paragraphs", "paragraph"),
                    ("entities", "entity"),
                    ("relations", "relation"),
                )
            },
            "embedding_fingerprint": dict(self.embedding_fingerprint),
            "generation_reason": "lpmm_conversion",
        }
        manifest_path = self.vector_dir / "dual_ready.json"
        with atomic_write(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")

    def run(self):
        self._validate_input()
        self.initialize_stores()
        try:
            self.convert_vectors()
            self.convert_graph()
            self.paragraph_vector_store.save(embedding_fingerprint=self.embedding_fingerprint)
            self.graph_vector_store.save(embedding_fingerprint=self.embedding_fingerprint)
            self.graph_store.save()
            self.metadata_store.close()
            self.metadata_store = None
            self._write_dual_ready_manifest()
        finally:
            if self.metadata_store is not None:
                self.metadata_store.close()
        logger.info("所有转换成功完成。")


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    data_dir = resolve_repo_path(args.data_dir, fallback=DEFAULT_DATA_DIR)
    try:
        input_path = _resolve_import_path(
            args.input,
            (data_dir / "imports" / "source" / "lpmm").resolve(),
            "LPMM 输入",
        )
        output_path = _resolve_import_path(
            args.output,
            (data_dir / "imports" / "converted").resolve(),
            "LPMM 输出",
        )
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    if not input_path.exists():
        logger.error(f"输入目录不存在: {input_path}")
        sys.exit(1)

    converter = LPMMConverter(
        input_path,
        output_path,
        dimension=args.dim,
        batch_size=args.batch_size,
        allow_unsafe_pickle=bool(args.allow_unsafe_pickle),
    )
    converter.run()


if __name__ == "__main__":
    main()
