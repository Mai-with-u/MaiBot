#!/usr/bin/env python3
"""
知识库自动导入脚本 (Strategy-Aware Version)

功能：
1. 扫描 plugins/A_memorix/data/raw 下的 .txt 文件
2. 检查 data/import_manifest.json 确认是否已导入
3. 使用 Strategy 模式处理文件 (Narrative/Factual/Quote)
4. 将生成的数据直接存入 VectorStore/GraphStore/MetadataStore
5. 更新 manifest
"""

import sys
import os
import json
import asyncio
import time
import random
import hashlib
import tomlkit
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

console = Console()

class LLMGenerationError(Exception):
    pass

# 路径设置
current_dir = Path(__file__).resolve().parent
plugin_root = current_dir.parent
workspace_root = plugin_root.parent
maibot_root = workspace_root / "MaiBot"
for path in (workspace_root, maibot_root, plugin_root):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# 数据目录
DATA_DIR = plugin_root / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MANIFEST_PATH = DATA_DIR / "import_manifest.json"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A_Memorix Knowledge Importer (Strategy-Aware)")
    parser.add_argument("--force", action="store_true", help="Force re-import")
    parser.add_argument("--clear-manifest", action="store_true", help="Clear manifest")
    parser.add_argument(
        "--type",
        "-t",
        default="auto",
        help="Target import strategy override (auto/narrative/factual/quote)",
    )
    parser.add_argument("--concurrency", "-c", type=int, default=5)
    parser.add_argument(
        "--chat-log",
        action="store_true",
        help="聊天记录导入模式：强制 narrative 策略，并使用 LLM 语义抽取 event_time/event_time_range",
    )
    parser.add_argument(
        "--chat-reference-time",
        default=None,
        help="chat_log 模式的相对时间参考点（如 2026/02/12 10:30）；不传则使用当前本地时间",
    )
    return parser


# --help/-h fast path: avoid heavy host/plugin bootstrap
if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    sys.exit(0)


try:
    import A_memorix.core as core_module
    import A_memorix.core.storage as storage_module
    from src.common.logger import get_logger
    from src.services import llm_service as llm_api
    from src.config.config import global_config, model_config

    VectorStore = core_module.VectorStore
    GraphStore = core_module.GraphStore
    MetadataStore = core_module.MetadataStore
    ImportStrategy = core_module.ImportStrategy
    create_embedding_api_adapter = core_module.create_embedding_api_adapter
    RelationWriteService = getattr(core_module, "RelationWriteService", None)

    looks_like_quote_text = storage_module.looks_like_quote_text
    parse_import_strategy = storage_module.parse_import_strategy
    resolve_stored_knowledge_type = storage_module.resolve_stored_knowledge_type
    select_import_strategy = storage_module.select_import_strategy

    from A_memorix.core.utils.time_parser import normalize_time_meta
    from A_memorix.core.utils.import_payloads import normalize_paragraph_import_item
    from A_memorix.core.strategies.base import BaseStrategy, ProcessedChunk, KnowledgeType as StratKnowledgeType
    from A_memorix.core.strategies.narrative import NarrativeStrategy
    from A_memorix.core.strategies.factual import FactualStrategy
    from A_memorix.core.strategies.quote import QuoteStrategy

except ImportError as e:
    print(f"❌ 无法导入模块: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

logger = get_logger("A_Memorix.AutoImport")


def _log_before_retry(retry_state) -> None:
    """使用项目统一日志风格记录重试信息。"""
    exc = None
    if getattr(retry_state, "outcome", None) is not None and retry_state.outcome.failed:
        exc = retry_state.outcome.exception()
    next_sleep = getattr(getattr(retry_state, "next_action", None), "sleep", None)
    logger.warning(
        "LLM 调用即将重试: "
        f"attempt={getattr(retry_state, 'attempt_number', '?')} "
        f"next_sleep={next_sleep} "
        f"error={exc}"
    )

class AutoImporter:
    def __init__(
        self,
        force: bool = False,
        clear_manifest: bool = False,
        target_type: str = "auto",
        concurrency: int = 5,
        chat_log: bool = False,
        chat_reference_time: Optional[str] = None,
    ):
        self.vector_store: Optional[VectorStore] = None
        self.graph_store: Optional[GraphStore] = None
        self.metadata_store: Optional[MetadataStore] = None
        self.embedding_manager = None
        self.relation_write_service = None
        self.plugin_config = {}
        self.manifest = {}
        self.force = force
        self.clear_manifest = clear_manifest
        self.chat_log = chat_log
        parsed_target_type = parse_import_strategy(target_type, default=ImportStrategy.AUTO)
        self.target_type = ImportStrategy.NARRATIVE.value if chat_log else parsed_target_type.value
        self.chat_reference_dt = self._parse_reference_time(chat_reference_time)
        if self.chat_log and parsed_target_type not in {ImportStrategy.AUTO, ImportStrategy.NARRATIVE}:
            logger.warning(
                f"chat_log 模式已启用，target_type={target_type} 将被覆盖为 narrative"
            )
        self.concurrency_limit = concurrency
        self.semaphore = None
        self.storage_lock = None

    async def initialize(self):
        logger.info(f"正在初始化... (并发数: {self.concurrency_limit})")
        self.semaphore = asyncio.Semaphore(self.concurrency_limit)
        self.storage_lock = asyncio.Lock()
        
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        
        if self.clear_manifest:
            logger.info("🧹 清理 Mainfest")
            self.manifest = {}
            self._save_manifest()
        elif MANIFEST_PATH.exists():
            try:
                with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                    self.manifest = json.load(f)
            except Exception:
                self.manifest = {}
        
        config_path = plugin_root / "config.toml"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.plugin_config = tomlkit.load(f)
        except Exception as e:
            logger.error(f"加载插件配置失败: {e}")
            return False

        try:
            await self._init_stores()
        except Exception as e:
            logger.error(f"初始化存储失败: {e}")
            return False
            
        return True

    async def _init_stores(self):
        # ... (Same as original)
        self.embedding_manager = create_embedding_api_adapter(
            batch_size=self.plugin_config.get("embedding", {}).get("batch_size", 32),
            default_dimension=self.plugin_config.get("embedding", {}).get("dimension", 384),
            model_name=self.plugin_config.get("embedding", {}).get("model_name", "auto"),
            retry_config=self.plugin_config.get("embedding", {}).get("retry", {}),
        )
        try:
            dim = await self.embedding_manager._detect_dimension()
        except:
            dim = self.embedding_manager.default_dimension
            
        q_type_str = str(self.plugin_config.get("embedding", {}).get("quantization_type", "int8") or "int8").lower()
        # Need to access QuantizationType from storage_module if not imported globally
        QuantizationType = storage_module.QuantizationType
        if q_type_str != "int8":
            raise ValueError(
                "embedding.quantization_type 在 vNext 仅允许 int8(SQ8)。"
                " 请先执行 scripts/release_vnext_migrate.py migrate。"
            )

        self.vector_store = VectorStore(
            dimension=dim,
            quantization_type=QuantizationType.INT8,
            data_dir=DATA_DIR / "vectors"
        )
        
        SparseMatrixFormat = storage_module.SparseMatrixFormat
        m_fmt_str = self.plugin_config.get("graph", {}).get("sparse_matrix_format", "csr")
        m_map = {"csr": SparseMatrixFormat.CSR, "csc": SparseMatrixFormat.CSC}
        
        self.graph_store = GraphStore(
            matrix_format=m_map.get(m_fmt_str, SparseMatrixFormat.CSR),
            data_dir=DATA_DIR / "graph"
        )
        
        self.metadata_store = MetadataStore(data_dir=DATA_DIR / "metadata")
        self.metadata_store.connect()

        if RelationWriteService is not None:
            self.relation_write_service = RelationWriteService(
                metadata_store=self.metadata_store,
                graph_store=self.graph_store,
                vector_store=self.vector_store,
                embedding_manager=self.embedding_manager,
            )
        
        if self.vector_store.has_data(): self.vector_store.load()
        if self.graph_store.has_data(): self.graph_store.load()

    def _should_write_relation_vectors(self) -> bool:
        retrieval_cfg = self.plugin_config.get("retrieval", {})
        if not isinstance(retrieval_cfg, dict):
            return False
        rv_cfg = retrieval_cfg.get("relation_vectorization", {})
        if not isinstance(rv_cfg, dict):
            return False
        return bool(rv_cfg.get("enabled", False)) and bool(rv_cfg.get("write_on_import", True))

    def load_file(self, file_path: Path) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def get_file_hash(self, content: str) -> str:
        return hashlib.md5(content.encode("utf-8")).hexdigest()
    
    def _parse_reference_time(self, value: Optional[str]) -> datetime:
        """解析 chat_log 模式的参考时间（用于相对时间语义解析）。"""
        if not value:
            return datetime.now()
        formats = [
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d",
            "%Y-%m-%d",
        ]
        text = str(value).strip()
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        logger.warning(
            f"无法解析 chat_reference_time={value}，将回退为当前本地时间"
        )
        return datetime.now()

    async def _extract_chat_time_meta_with_llm(
        self,
        text: str,
        model_config: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        使用 LLM 从聊天文本语义中抽取时间信息。
        支持将相对时间表达转换为绝对时间。
        """
        if not text.strip():
            return None

        reference_now = self.chat_reference_dt.strftime("%Y/%m/%d %H:%M")
        prompt = f"""You are a time extraction engine for chat logs.
Extract temporal information from the following chat paragraph.

Rules:
1. Use semantic understanding, not regex matching.
2. Convert relative expressions (e.g., yesterday evening, last Friday morning) to absolute local datetime using reference_now.
3. If a time span exists, return event_time_start/event_time_end.
4. If only one point in time exists, return event_time.
5. If no reliable time can be inferred, return all time fields as null.
6. Output ONLY valid JSON. No markdown, no explanation.

reference_now: {reference_now}
timezone: local system timezone

Allowed output formats for time values:
- "YYYY/MM/DD"
- "YYYY/MM/DD HH:mm"

JSON schema:
{{
  "event_time": null,
  "event_time_start": null,
  "event_time_end": null,
  "time_range": null,
  "time_granularity": "day",
  "time_confidence": 0.0
}}

Chat paragraph:
\"\"\"{text}\"\"\"
"""
        try:
            result = await self._llm_call(prompt, model_config)
        except Exception as e:
            logger.warning(f"chat_log 时间语义抽取失败: {e}")
            return None

        if not isinstance(result, dict):
            return None

        raw_time_meta = {
            "event_time": result.get("event_time"),
            "event_time_start": result.get("event_time_start"),
            "event_time_end": result.get("event_time_end"),
            "time_range": result.get("time_range"),
            "time_granularity": result.get("time_granularity"),
            "time_confidence": result.get("time_confidence"),
        }
        try:
            normalized = normalize_time_meta(raw_time_meta)
        except Exception as e:
            logger.warning(f"chat_log 时间语义抽取结果不可用，已忽略: {e}")
            return None

        has_effective_time = any(
            key in normalized
            for key in ("event_time", "event_time_start", "event_time_end")
        )
        if not has_effective_time:
            return None

        return normalized

    def _determine_strategy(self, filename: str, content: str) -> BaseStrategy:
        """Layer 1: Global Strategy Routing"""
        strategy = select_import_strategy(
            content,
            override=self.target_type,
            chat_log=self.chat_log,
        )
        if self.chat_log:
            logger.info(f"chat_log 模式: {filename} 强制使用 NarrativeStrategy")
        elif strategy == ImportStrategy.QUOTE:
            logger.info(f"Auto-detected Quote/Lyric type for {filename}")

        if strategy == ImportStrategy.FACTUAL:
            return FactualStrategy(filename)
        if strategy == ImportStrategy.QUOTE:
            return QuoteStrategy(filename)
        return NarrativeStrategy(filename)

    def _chunk_rescue(self, chunk: ProcessedChunk, filename: str) -> Optional[BaseStrategy]:
        """Layer 2: Chunk-level rescue strategies"""
        # If we are already in Quote strategy, no need to rescue
        if chunk.type == StratKnowledgeType.QUOTE:
            return None

        if looks_like_quote_text(chunk.chunk.text):
            logger.info(f"  > Rescuing chunk {chunk.chunk.index} as Quote")
            return QuoteStrategy(filename)

        return None

    async def process_and_import(self):
        if not await self.initialize(): return

        files = list(RAW_DIR.glob("*.txt"))
        logger.info(f"扫描到 {len(files)} 个文件 in {RAW_DIR}")

        if not files: return

        tasks = []
        for file_path in files:
            tasks.append(asyncio.create_task(self._process_single_file(file_path)))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in results if r is True)
        logger.info(f"本次主处理完成，共成功处理 {success_count}/{len(files)} 个文件")
        
        if self.vector_store: self.vector_store.save()
        if self.graph_store: self.graph_store.save()

    async def _process_single_file(self, file_path: Path) -> bool:
        filename = file_path.name
        async with self.semaphore:
            try:
                content = self.load_file(file_path)
                file_hash = self.get_file_hash(content)
                
                if not self.force and filename in self.manifest:
                    record = self.manifest[filename]
                    if record.get("hash") == file_hash and record.get("imported"):
                        logger.info(f"跳过已导入文件: {filename}")
                        return False
                
                logger.info(f">>> 开始处理: {filename}")
                
                # 1. Strategy Selection
                strategy = self._determine_strategy(filename, content)
                logger.info(f"  策略: {strategy.__class__.__name__}")
                
                # 2. Split (Strategy-Aware)
                initial_chunks = strategy.split(content)
                logger.info(f"  初步分块: {len(initial_chunks)}")
                
                processed_data = {"paragraphs": [], "entities": [], "relations": []}
                
                # 3. Extract Loop
                model_config = await self._select_model()
                
                for i, chunk in enumerate(initial_chunks):
                    current_strategy = strategy
                    # Layer 2: Chunk Rescue
                    rescue_strategy = self._chunk_rescue(chunk, filename)
                    if rescue_strategy:
                        # Re-split? No, just re-process this text as a single chunk using the rescue strategy
                        # But rescue strategy might want to split it further?
                        # Simplification: Treat the whole chunk text as one block for the rescue strategy 
                        # OR create a single chunk object for it.
                        # Creating a new chunk using rescue strategy logic might be complex if split behavior differs.
                        # Let's just instantiate a chunk of the new type manually
                        chunk.type = StratKnowledgeType.QUOTE
                        chunk.flags.verbatim = True
                        chunk.flags.requires_llm = False # Quotes don't usually need LLM
                        current_strategy = rescue_strategy
                    
                    # Extraction
                    if chunk.flags.requires_llm:
                        result_chunk = await current_strategy.extract(chunk, lambda p: self._llm_call(p, model_config))
                    else:
                         # For quotes, extract might be just pass through or regex
                        result_chunk = await current_strategy.extract(chunk)
                    
                    time_meta = None
                    if self.chat_log:
                        time_meta = await self._extract_chat_time_meta_with_llm(
                            result_chunk.chunk.text,
                            model_config,
                        )

                    # Normalize Data
                    self._normalize_and_aggregate(
                        result_chunk,
                        processed_data,
                        time_meta=time_meta,
                    )
                    
                    logger.info(f"  已处理块 {i+1}/{len(initial_chunks)}")
                
                # 4. Save Json
                json_path = PROCESSED_DIR / f"{file_path.stem}.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(processed_data, f, ensure_ascii=False, indent=2)
                
                # 5. Import to DB
                async with self.storage_lock:
                    await self._import_to_db(processed_data)
                    
                    self.manifest[filename] = {
                        "hash": file_hash,
                        "timestamp": time.time(),
                        "imported": True
                    }
                    self._save_manifest()
                    self.vector_store.save()
                    self.graph_store.save()
                    logger.info(f"✅ 文件 {filename} 处理并导入完成")
                    return True

            except Exception as e:
                logger.error(f"❌ 处理失败 {filename}: {e}")
                import traceback
                traceback.print_exc()
                return False

    def _normalize_and_aggregate(
        self,
        chunk: ProcessedChunk,
        all_data: Dict,
        time_meta: Optional[Dict[str, Any]] = None,
    ):
        """Convert strategy-specific data to unified generic format for storage."""
        # Generic fields
        para_item = {
            "content": chunk.chunk.text,
            "source": chunk.source.file,
            "knowledge_type": resolve_stored_knowledge_type(
                chunk.type.value,
                content=chunk.chunk.text,
            ).value,
            "entities": [],
            "relations": []
        }
        
        data = chunk.data
        
        # 1. Triples (Factual)
        if "triples" in data:
            for t in data["triples"]:
                para_item["relations"].append({
                    "subject": t.get("subject"),
                    "predicate": t.get("predicate"),
                    "object": t.get("object")
                })
                # Auto-add entities from triples
                para_item["entities"].extend([t.get("subject"), t.get("object")])
        
        # 2. Events & Relations (Narrative)
        if "events" in data:
            # Store events as content/metadata? Or entities?
            # For now maybe just keep them in logic, or add as 'Event' entities?
            # Creating entities for events is good.
            para_item["entities"].extend(data["events"])
        
        if "relations" in data: # Narrative also outputs relations list
             para_item["relations"].extend(data["relations"])
             for r in data["relations"]:
                 para_item["entities"].extend([r.get("subject"), r.get("object")])

        # 3. Verbatim Entities (Quote)
        if "verbatim_entities" in data:
            para_item["entities"].extend(data["verbatim_entities"])
            
        # Dedupe per paragraph
        para_item["entities"] = list(set([e for e in para_item["entities"] if e]))

        if time_meta:
            para_item["time_meta"] = time_meta
        
        all_data["paragraphs"].append(para_item)
        all_data["entities"].extend(para_item["entities"])
        if "relations" in para_item:
             all_data["relations"].extend(para_item["relations"])

    @retry(
        retry=retry_if_exception_type((LLMGenerationError, json.JSONDecodeError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=_log_before_retry
    )
    async def _llm_call(self, prompt: str, model_config: Any) -> Dict:
        """Generic LLM Caller"""
        success, response, _, _ = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="Script.ProcessKnowledge"
        )
        if success:
            txt = response.strip()
            if "```" in txt:
                txt = txt.split("```json")[-1].split("```")[0].strip()
            try:
                return json.loads(txt)
            except json.JSONDecodeError:
                # Fallback: try to find first { and last }
                start = txt.find('{')
                end = txt.rfind('}')
                if start != -1 and end != -1:
                    return json.loads(txt[start:end+1])
                raise
        else:
            raise LLMGenerationError("LLM generation failed")

    async def _select_model(self) -> Any:
        models = llm_api.get_available_models()
        if not models: raise ValueError("No LLM models")
        
        config_model = self.plugin_config.get("advanced", {}).get("extraction_model", "auto")
        if config_model != "auto" and config_model in models:
            return models[config_model]
            
        for task_key in ["lpmm_entity_extract", "lpmm_rdf_build", "embedding"]:
            if task_key in models: return models[task_key]
            
        return models[list(models.keys())[0]]

    # Re-use existing methods
    async def _add_entity_with_vector(self, name: str, source_paragraph: Optional[str] = None) -> str:
        # Same as before
        hash_value = self.metadata_store.add_entity(name, source_paragraph=source_paragraph)
        self.graph_store.add_nodes([name])
        try:
            emb = await self.embedding_manager.encode(name)
            try:
                self.vector_store.add(emb.reshape(1, -1), [hash_value])
            except ValueError: pass
        except Exception: pass
        return hash_value

    async def import_json_data(self, data: Dict, filename: str = "script_import", progress_callback=None):
        """Public import entrypoint for pre-processed JSON payloads."""
        if not self.storage_lock:
            raise RuntimeError("Importer is not initialized. Call initialize() first.")

        async with self.storage_lock:
            await self._import_to_db(data, progress_callback=progress_callback)
            self.manifest[filename] = {
                "hash": self.get_file_hash(json.dumps(data, ensure_ascii=False, sort_keys=True)),
                "timestamp": time.time(),
                "imported": True,
            }
            self._save_manifest()
            self.vector_store.save()
            self.graph_store.save()

    async def _import_to_db(self, data: Dict, progress_callback=None):
        # Same logic, but ensure robust
        with self.graph_store.batch_update():
            for item in data.get("paragraphs", []):
                paragraph = normalize_paragraph_import_item(
                    item,
                    default_source="script",
                )
                content = paragraph["content"]
                source = paragraph["source"]
                k_type_val = paragraph["knowledge_type"]

                h_val = self.metadata_store.add_paragraph(
                    content=content,
                    source=source,
                    knowledge_type=k_type_val,
                    time_meta=paragraph["time_meta"],
                )
                
                if h_val not in self.vector_store:
                    try:
                        emb = await self.embedding_manager.encode(content)
                        self.vector_store.add(emb.reshape(1, -1), [h_val])
                    except Exception as e:
                        logger.error(f"  Vector fail: {e}")

                para_entities = paragraph["entities"]
                for entity in para_entities:
                    if entity:
                        await self._add_entity_with_vector(entity, source_paragraph=h_val)
                
                para_relations = paragraph["relations"]
                for rel in para_relations:
                    s, p, o = rel.get("subject"), rel.get("predicate"), rel.get("object")
                    if s and p and o:
                        await self._add_entity_with_vector(s, source_paragraph=h_val)
                        await self._add_entity_with_vector(o, source_paragraph=h_val)
                        confidence = float(rel.get("confidence", 1.0) or 1.0)
                        rel_meta = rel.get("metadata", {})
                        write_vector = self._should_write_relation_vectors()
                        if self.relation_write_service is not None:
                            await self.relation_write_service.upsert_relation_with_vector(
                                subject=s,
                                predicate=p,
                                obj=o,
                                confidence=confidence,
                                source_paragraph=h_val,
                                metadata=rel_meta if isinstance(rel_meta, dict) else {},
                                write_vector=write_vector,
                            )
                        else:
                            rel_hash = self.metadata_store.add_relation(
                                s,
                                p,
                                o,
                                confidence=confidence,
                                source_paragraph=h_val,
                                metadata=rel_meta if isinstance(rel_meta, dict) else {},
                            )
                            self.graph_store.add_edges([(s, o)], relation_hashes=[rel_hash])
                            try:
                                self.metadata_store.set_relation_vector_state(rel_hash, "none")
                            except Exception:
                                pass
                        
                if progress_callback: progress_callback(1)
    
    async def close(self):
        if self.metadata_store: self.metadata_store.close()
    
    def _save_manifest(self):
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, ensure_ascii=False, indent=2)

async def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    if not global_config: return
    
    importer = AutoImporter(
        force=args.force, 
        clear_manifest=args.clear_manifest, 
        target_type=args.type,
        concurrency=args.concurrency,
        chat_log=args.chat_log,
        chat_reference_time=args.chat_reference_time,
    )
    await importer.process_and_import()
    await importer.close()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
