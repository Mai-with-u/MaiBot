"""Lifecycle bootstrap/teardown helpers extracted from plugin.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.common.logger import get_logger

from ..embedding import create_embedding_api_adapter
from ..retrieval import SparseBM25Config, SparseBM25Index
from ..storage import (
    GraphStore,
    MetadataStore,
    QuantizationType,
    SparseMatrixFormat,
    VectorStore,
)
from ..utils.runtime_self_check import ensure_runtime_self_check
from ..utils.relation_write_service import RelationWriteService

logger = get_logger("A_Memorix.LifecycleOrchestrator")


async def ensure_initialized(plugin: Any) -> None:
    if plugin._initialized:
        plugin._runtime_ready = plugin._check_storage_ready()
        return

    async with plugin._init_lock:
        if plugin._initialized:
            plugin._runtime_ready = plugin._check_storage_ready()
            return

        logger.info("A_Memorix 插件正在异步初始化存储组件...")
        plugin._validate_runtime_config()
        await initialize_storage_async(plugin)
        report = await ensure_runtime_self_check(plugin, force=True)
        if not bool(report.get("ok", False)):
            logger.error(
                "A_Memorix runtime self-check failed: "
                f"{report.get('message', 'unknown')}; "
                "建议执行 python plugins/A_memorix/scripts/runtime_self_check.py --json"
            )

        if plugin.graph_store and plugin.metadata_store:
            relation_count = plugin.metadata_store.count_relations()
            if relation_count > 0 and not plugin.graph_store.has_edge_hash_map():
                raise RuntimeError(
                    "检测到 relations 数据存在但 edge-hash-map 为空。"
                    " 请先执行 scripts/release_vnext_migrate.py migrate。"
                )

        plugin._initialized = True
        plugin._runtime_ready = plugin._check_storage_ready()
        plugin._update_plugin_config()
        logger.info("A_Memorix 插件异步初始化成功")


def start_background_tasks(plugin: Any) -> None:
    """Start background tasks idempotently."""
    if not hasattr(plugin, "_episode_generation_task"):
        plugin._episode_generation_task = None

    if (
        plugin.get_config("summarization.enabled", True)
        and plugin.get_config("schedule.enabled", True)
        and (plugin._scheduled_import_task is None or plugin._scheduled_import_task.done())
    ):
        plugin._scheduled_import_task = asyncio.create_task(plugin._scheduled_import_loop())

    if (
        plugin.get_config("advanced.enable_auto_save", True)
        and (plugin._auto_save_task is None or plugin._auto_save_task.done())
    ):
        plugin._auto_save_task = asyncio.create_task(plugin._auto_save_loop())

    if (
        plugin.get_config("person_profile.enabled", True)
        and (plugin._person_profile_refresh_task is None or plugin._person_profile_refresh_task.done())
    ):
        plugin._person_profile_refresh_task = asyncio.create_task(plugin._person_profile_refresh_loop())

    if plugin._memory_maintenance_task is None or plugin._memory_maintenance_task.done():
        plugin._memory_maintenance_task = asyncio.create_task(plugin._memory_maintenance_loop())

    rv_cfg = plugin.get_config("retrieval.relation_vectorization", {}) or {}
    if isinstance(rv_cfg, dict):
        rv_enabled = bool(rv_cfg.get("enabled", False))
        rv_backfill = bool(rv_cfg.get("backfill_enabled", False))
    else:
        rv_enabled = False
        rv_backfill = False
    if rv_enabled and rv_backfill and (
        plugin._relation_vector_backfill_task is None or plugin._relation_vector_backfill_task.done()
    ):
        plugin._relation_vector_backfill_task = asyncio.create_task(plugin._relation_vector_backfill_loop())

    episode_task = getattr(plugin, "_episode_generation_task", None)
    episode_loop = getattr(plugin, "_episode_generation_loop", None)
    if (
        callable(episode_loop)
        and bool(plugin.get_config("episode.enabled", True))
        and bool(plugin.get_config("episode.generation_enabled", True))
        and (episode_task is None or episode_task.done())
    ):
        plugin._episode_generation_task = asyncio.create_task(episode_loop())


async def cancel_background_tasks(plugin: Any) -> None:
    """Cancel all background tasks and wait for cleanup."""
    tasks = [
        ("scheduled_import", plugin._scheduled_import_task),
        ("auto_save", plugin._auto_save_task),
        ("person_profile_refresh", plugin._person_profile_refresh_task),
        ("memory_maintenance", plugin._memory_maintenance_task),
        ("relation_vector_backfill", plugin._relation_vector_backfill_task),
        ("episode_generation", getattr(plugin, "_episode_generation_task", None)),
    ]
    for _, task in tasks:
        if task and not task.done():
            task.cancel()

    for name, task in tasks:
        if not task:
            continue
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"后台任务 {name} 退出异常: {e}")

    plugin._scheduled_import_task = None
    plugin._auto_save_task = None
    plugin._person_profile_refresh_task = None
    plugin._memory_maintenance_task = None
    plugin._relation_vector_backfill_task = None
    plugin._episode_generation_task = None


async def initialize_storage_async(plugin: Any) -> None:
    """Initialize storage components asynchronously."""
    data_dir_str = plugin.get_config("storage.data_dir", "./data")
    if data_dir_str.startswith("."):
        plugin_dir = Path(__file__).resolve().parents[2]
        data_dir = (plugin_dir / data_dir_str).resolve()
    else:
        data_dir = Path(data_dir_str)

    logger.info(f"A_Memorix 数据存储路径: {data_dir}")
    data_dir.mkdir(parents=True, exist_ok=True)

    plugin.embedding_manager = create_embedding_api_adapter(
        batch_size=plugin.get_config("embedding.batch_size", 32),
        max_concurrent=plugin.get_config("embedding.max_concurrent", 5),
        default_dimension=plugin.get_config("embedding.dimension", 1024),
        model_name=plugin.get_config("embedding.model_name", "auto"),
        retry_config=plugin.get_config("embedding.retry", {}),
    )
    logger.info("嵌入 API 适配器初始化完成")

    try:
        detected_dimension = await plugin.embedding_manager._detect_dimension()
        logger.info(f"嵌入维度检测成功: {detected_dimension}")
    except Exception as e:
        logger.warning(f"嵌入维度检测失败: {e}，使用默认值")
        detected_dimension = plugin.embedding_manager.default_dimension

    quantization_str = plugin.get_config("embedding.quantization_type", "int8")
    if str(quantization_str or "").strip().lower() != "int8":
        raise ValueError("embedding.quantization_type 在 vNext 仅允许 int8(SQ8)。")
    quantization_type = QuantizationType.INT8

    plugin.vector_store = VectorStore(
        dimension=detected_dimension,
        quantization_type=quantization_type,
        data_dir=data_dir / "vectors",
    )
    plugin.vector_store.min_train_threshold = plugin.get_config("embedding.min_train_threshold", 40)
    logger.info(
        "向量存储初始化完成（"
        f"维度: {detected_dimension}, "
        f"训练阈值: {plugin.vector_store.min_train_threshold}）"
    )

    matrix_format_str = plugin.get_config("graph.sparse_matrix_format", "csr")
    matrix_format_map = {
        "csr": SparseMatrixFormat.CSR,
        "csc": SparseMatrixFormat.CSC,
    }
    matrix_format = matrix_format_map.get(matrix_format_str, SparseMatrixFormat.CSR)

    plugin.graph_store = GraphStore(
        matrix_format=matrix_format,
        data_dir=data_dir / "graph",
    )
    logger.info("图存储初始化完成")

    plugin.metadata_store = MetadataStore(data_dir=data_dir / "metadata")
    plugin.metadata_store.connect()
    logger.info("元数据存储初始化完成")

    plugin.relation_write_service = RelationWriteService(
        metadata_store=plugin.metadata_store,
        graph_store=plugin.graph_store,
        vector_store=plugin.vector_store,
        embedding_manager=plugin.embedding_manager,
    )
    logger.info("关系写入服务初始化完成")

    sparse_cfg_raw = plugin.get_config("retrieval.sparse", {}) or {}
    if not isinstance(sparse_cfg_raw, dict):
        sparse_cfg_raw = {}
    try:
        sparse_cfg = SparseBM25Config(**sparse_cfg_raw)
    except Exception as e:
        logger.warning(f"sparse 配置非法，回退默认配置: {e}")
        sparse_cfg = SparseBM25Config()
    plugin.sparse_index = SparseBM25Index(
        metadata_store=plugin.metadata_store,
        config=sparse_cfg,
    )
    logger.info(
        "稀疏检索组件初始化完成: "
        f"enabled={sparse_cfg.enabled}, "
        f"lazy_load={sparse_cfg.lazy_load}, "
        f"mode={sparse_cfg.mode}, "
        f"tokenizer={sparse_cfg.tokenizer_mode}"
    )
    if sparse_cfg.enabled and not sparse_cfg.lazy_load:
        plugin.sparse_index.ensure_loaded()

    if plugin.vector_store.has_data():
        try:
            plugin.vector_store.load()
            logger.info(f"向量数据已加载，共 {plugin.vector_store.num_vectors} 个向量")
        except Exception as e:
            logger.warning(f"加载向量数据失败: {e}")

    try:
        warmup_summary = plugin.vector_store.warmup_index(force_train=True)
        if warmup_summary.get("ok"):
            logger.info(
                "向量索引预热完成: "
                f"trained={warmup_summary.get('trained')}, "
                f"index_ntotal={warmup_summary.get('index_ntotal')}, "
                f"fallback_ntotal={warmup_summary.get('fallback_ntotal')}, "
                f"bin_count={warmup_summary.get('bin_count')}, "
                f"duration_ms={float(warmup_summary.get('duration_ms', 0.0)):.2f}"
            )
        else:
            logger.warning(
                "向量索引预热失败，继续启用 sparse 降级路径: "
                f"{warmup_summary.get('error', 'unknown')}"
            )
    except Exception as e:
        logger.warning(f"向量索引预热异常，继续启用 sparse 降级路径: {e}")

    if plugin.graph_store.has_data():
        try:
            plugin.graph_store.load()
            logger.info(f"图数据已加载，共 {plugin.graph_store.num_nodes} 个节点")
        except Exception as e:
            logger.warning(f"加载图数据失败: {e}")

    logger.info(f"知识库数据目录: {data_dir}")
