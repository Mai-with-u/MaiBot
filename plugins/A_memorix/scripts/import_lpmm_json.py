#!/usr/bin/env python3
"""
LPMM OpenIE JSON 导入工具。

功能：
1. 读取符合 LPMM 规范的 OpenIE JSON 文件
2. 转换为 A_Memorix 的统一导入格式
3. 复用 `process_knowledge.py` 中的 `AutoImporter` 直接入库
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()

CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
WORKSPACE_ROOT = PLUGIN_ROOT.parent
MAIBOT_ROOT = WORKSPACE_ROOT / "MaiBot"
for path in (CURRENT_DIR, WORKSPACE_ROOT, MAIBOT_ROOT, PLUGIN_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 LPMM OpenIE JSON 导入 A_Memorix")
    parser.add_argument("path", help="LPMM JSON 文件路径或目录")
    parser.add_argument("--force", action="store_true", help="强制重新导入")
    parser.add_argument("--concurrency", "-c", type=int, default=5, help="并发数")
    return parser


if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    raise SystemExit(0)


try:
    from process_knowledge import AutoImporter
    from A_memorix.core.utils.hash import compute_paragraph_hash
    from src.common.logger import get_logger
except ImportError as exc:  # pragma: no cover - script bootstrap
    print(f"导入模块失败，请确认 PYTHONPATH 与工作区结构: {exc}")
    raise SystemExit(1)


logger = get_logger("A_Memorix.LPMMImport")


class LPMMConverter:
    def convert_lpmm_to_memorix(self, lpmm_data: Dict[str, Any], filename: str) -> Dict[str, Any]:
        memorix_data = {"paragraphs": [], "entities": []}
        docs = lpmm_data.get("docs", []) or []
        if not docs:
            logger.warning(f"文件中未找到 docs 字段: {filename}")
            return memorix_data

        all_entities = set()
        for doc in docs:
            content = str(doc.get("passage", "") or "").strip()
            if not content:
                continue

            relations: List[Dict[str, str]] = []
            for triple in doc.get("extracted_triples", []) or []:
                if isinstance(triple, list) and len(triple) == 3:
                    relations.append(
                        {
                            "subject": str(triple[0] or "").strip(),
                            "predicate": str(triple[1] or "").strip(),
                            "object": str(triple[2] or "").strip(),
                        }
                    )

            entities = [str(item or "").strip() for item in doc.get("extracted_entities", []) or [] if str(item or "").strip()]
            all_entities.update(entities)
            for relation in relations:
                if relation["subject"]:
                    all_entities.add(relation["subject"])
                if relation["object"]:
                    all_entities.add(relation["object"])

            memorix_data["paragraphs"].append(
                {
                    "hash": compute_paragraph_hash(content),
                    "content": content,
                    "source": filename,
                    "entities": entities,
                    "relations": relations,
                }
            )

        memorix_data["entities"] = sorted(all_entities)
        return memorix_data


async def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    target_path = Path(args.path)
    if not target_path.exists():
        logger.error(f"路径不存在: {target_path}")
        return

    if target_path.is_dir():
        files_to_process = list(target_path.glob("*-openie.json")) or list(target_path.glob("*.json"))
    else:
        files_to_process = [target_path]

    if not files_to_process:
        logger.error("未找到可处理的 JSON 文件")
        return

    importer = AutoImporter(force=bool(args.force), concurrency=int(args.concurrency))
    if not await importer.initialize():
        logger.error("初始化存储失败")
        return

    converter = LPMMConverter()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        for json_file in files_to_process:
            logger.info(f"正在转换并导入: {json_file.name}")
            try:
                with open(json_file, "r", encoding="utf-8") as handle:
                    lpmm_data = json.load(handle)
                memorix_data = converter.convert_lpmm_to_memorix(lpmm_data, json_file.name)
                total_items = len(memorix_data.get("paragraphs", []))
                if total_items <= 0:
                    logger.warning(f"转换结果为空: {json_file.name}")
                    continue

                task_id = progress.add_task(f"Importing {json_file.name}", total=total_items)

                def update_progress(step: int = 1) -> None:
                    progress.advance(task_id, advance=step)

                await importer.import_json_data(
                    memorix_data,
                    filename=f"lpmm_{json_file.name}",
                    progress_callback=update_progress,
                )
            except Exception as exc:
                logger.error(f"处理文件 {json_file.name} 失败: {exc}\n{traceback.format_exc()}")

    await importer.close()
    logger.info("全部处理完成")


if __name__ == "__main__":
    if sys.platform == "win32":  # pragma: no cover
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
