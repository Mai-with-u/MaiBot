import asyncio
import os
import sys
from typing import List, Dict, Any

# 强制使用 utf-8，避免控制台编码报错影响 Embedding 加载
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 确保能导入 src.*
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.knowledge import lpmm_start_up
from src.memory_system.retrieval_tools.query_lpmm_knowledge import query_lpmm_knowledge

logger = get_logger("test_lpmm_retrieval")


TEST_CASES: List[Dict[str, Any]] = [
    {
        "name": "回滚一批知识",
        "query": "LPMM是什么?",
        "expect_keywords": ["哈希列表", "删除脚本", "OpenIE"],
    },
    {
        "name": "调整 LPMM 检索参数",
        "query": "不同用词习惯带来的检索偏差该如何解决",
        "expect_keywords": ["bot_config.toml", "lpmm_knowledge", "qa_paragraph_search_top_k"],
    },
]


async def run_tests() -> None:
    """简单测试 LPMM 知识库检索能力"""
    if not global_config.lpmm_knowledge.enable:
        logger.warning("当前配置中 lpmm_knowledge.enable 为 False，检索测试可能直接返回“未启用”。")

    logger.info("开始初始化 LPMM 知识库...")
    lpmm_start_up()
    logger.info("LPMM 知识库初始化完成，开始执行测试用例。")

    for case in TEST_CASES:
        name = case["name"]
        query = case["query"]
        expect_keywords: List[str] = case.get("expect_keywords", [])

        print("\n" + "=" * 60)
        print(f"[TEST] {name}")
        print(f"[Q] {query}")

        result = await query_lpmm_knowledge(query, limit=3)

        print("\n[RAW RESULT]")
        print(result)

        status = "UNKNOWN"
        hit_keywords: List[str] = []

        if isinstance(result, str):
            if "未启用" in result or "未初始化" in result or "查询失败" in result:
                status = "ERROR"
            elif "未找到与" in result:
                status = "NO_HIT"
            else:
                if expect_keywords:
                    hit_keywords = [kw for kw in expect_keywords if kw in result]
                    status = "PASS" if hit_keywords else "WARN"
                else:
                    status = "PASS"

        print("\n[CHECK]")
        print(f"Status: {status}")
        if expect_keywords:
            print(f"Expected keywords: {expect_keywords}")
            print(f"Hit keywords: {hit_keywords}")

    print("\n" + "=" * 60)
    print("LPMM 检索测试完成。请根据每条用例的 Status 和命中关键词判断检索效果是否符合预期。")


def main() -> None:
    asyncio.run(run_tests())


if __name__ == "__main__":
    main()
