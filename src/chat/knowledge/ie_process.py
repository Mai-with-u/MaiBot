import asyncio
import json
import time
from typing import List, Union, Dict, Any

from .global_logger import logger
from . import prompt_template
from . import INVALID_ENTITY
from src.llm_models.utils_model import LLMRequest
from json_repair import repair_json


def _extract_json_from_text(text: str):
    # sourcery skip: assign-if-exp, extract-method
    """从文本中提取JSON数据的高容错方法"""
    if text is None:
        logger.error("输入文本为None")
        return []

    try:
        fixed_json = repair_json(text)
        if isinstance(fixed_json, str):
            parsed_json = json.loads(fixed_json)
        else:
            parsed_json = fixed_json

        # 如果是列表，直接返回
        if isinstance(parsed_json, list):
            return parsed_json

        # 如果是字典且只有一个项目，可能包装了列表
        if isinstance(parsed_json, dict):
            # 如果字典只有一个键，并且值是列表，返回那个列表
            if len(parsed_json) == 1:
                value = list(parsed_json.values())[0]
                if isinstance(value, list):
                    return value
            return parsed_json

        # 其他情况，尝试转换为列表
        logger.warning(f"解析的JSON不是预期格式: {type(parsed_json)}, 内容: {parsed_json}")
        return []

    except Exception as e:
        logger.error(f"JSON提取失败: {e}, 原始文本: {text[:100] if text else 'None'}...")
        return []


def _entity_extract(llm_req: LLMRequest, paragraph: str) -> List[str]:
    # sourcery skip: reintroduce-else, swap-if-else-branches, use-named-expression
    """对段落进行实体提取，返回提取出的实体列表（JSON格式）"""
    entity_extract_context = prompt_template.build_entity_extract_context(paragraph)

    # 使用 asyncio.run 来运行异步方法
    try:
        # 如果当前已有事件循环在运行，使用它
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(llm_req.generate_response_async(entity_extract_context), loop)
        response, _ = future.result()
    except RuntimeError:
        # 如果没有运行中的事件循环，直接使用 asyncio.run
        response, _ = asyncio.run(llm_req.generate_response_async(entity_extract_context))

    # 添加调试日志
    logger.debug(f"LLM返回的原始响应: {response}")

    entity_extract_result = _extract_json_from_text(response)

    # 检查返回的是否为有效的实体列表
    if not isinstance(entity_extract_result, list):
        if not isinstance(entity_extract_result, dict):
            raise ValueError(f"实体提取结果格式错误，期望列表但得到: {type(entity_extract_result)}")

        # 尝试常见的键名
        for key in ["entities", "result", "data", "items"]:
            if key in entity_extract_result and isinstance(entity_extract_result[key], list):
                entity_extract_result = entity_extract_result[key]
                break
        else:
            # 如果找不到合适的列表，抛出异常
            raise ValueError(f"实体提取结果格式错误，期望列表但得到: {type(entity_extract_result)}")
    # 过滤无效实体
    entity_extract_result = [
        entity
        for entity in entity_extract_result
        if (entity is not None) and (entity != "") and (entity not in INVALID_ENTITY)
    ]

    if not entity_extract_result:
        raise ValueError("实体提取结果为空")

    return entity_extract_result


def _rdf_triple_extract(llm_req: LLMRequest, paragraph: str, entities: list) -> List[List[str]]:
    """对段落进行实体提取，返回提取出的实体列表（JSON格式）"""
    rdf_extract_context = prompt_template.build_rdf_triple_extract_context(
        paragraph, entities=json.dumps(entities, ensure_ascii=False)
    )

    # 使用 asyncio.run 来运行异步方法
    try:
        # 如果当前已有事件循环在运行，使用它
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(llm_req.generate_response_async(rdf_extract_context), loop)
        response, _ = future.result()
    except RuntimeError:
        # 如果没有运行中的事件循环，直接使用 asyncio.run
        response, _ = asyncio.run(llm_req.generate_response_async(rdf_extract_context))

    # 添加调试日志
    logger.debug(f"RDF LLM返回的原始响应: {response}")

    rdf_triple_result = _extract_json_from_text(response)

    # 检查返回的是否为有效的三元组列表
    if not isinstance(rdf_triple_result, list):
        if not isinstance(rdf_triple_result, dict):
            raise ValueError(f"RDF三元组提取结果格式错误，期望列表但得到: {type(rdf_triple_result)}")

        # 尝试常见的键名
        for key in ["triples", "result", "data", "items"]:
            if key in rdf_triple_result and isinstance(rdf_triple_result[key], list):
                rdf_triple_result = rdf_triple_result[key]
                break
        else:
            # 如果找不到合适的列表，抛出异常
            raise ValueError(f"RDF三元组提取结果格式错误，期望列表但得到: {type(rdf_triple_result)}")
    # 验证三元组格式
    for triple in rdf_triple_result:
        if (
            not isinstance(triple, list)
            or len(triple) != 3
            or (triple[0] is None or triple[1] is None or triple[2] is None)
            or "" in triple
        ):
            raise ValueError("RDF提取结果格式错误")

    return rdf_triple_result


def info_extract_from_str(
    llm_client_for_ner: LLMRequest, llm_client_for_rdf: LLMRequest, paragraph: str
) -> Union[tuple[None, None], tuple[list[str], list[list[str]]]]:
    try_count = 0
    while True:
        try:
            entity_extract_result = _entity_extract(llm_client_for_ner, paragraph)
            break
        except Exception as e:
            logger.warning(f"实体提取失败，错误信息：{e}")
            try_count += 1
            if try_count < 3:
                logger.warning("将于5秒后重试")
                time.sleep(5)
            else:
                logger.error("实体提取失败，已达最大重试次数")
                return None, None

    try_count = 0
    while True:
        try:
            rdf_triple_extract_result = _rdf_triple_extract(llm_client_for_rdf, paragraph, entity_extract_result)
            break
        except Exception as e:
            logger.warning(f"实体提取失败，错误信息：{e}")
            try_count += 1
            if try_count < 3:
                logger.warning("将于5秒后重试")
                time.sleep(5)
            else:
                logger.error("实体提取失败，已达最大重试次数")
                return None, None

    return entity_extract_result, rdf_triple_extract_result


class IEProcess:
    """
    信息抽取处理器类，提供更方便的批次处理接口。
    """

    def __init__(self, llm_ner: LLMRequest, llm_rdf: LLMRequest = None):
        self.llm_ner = llm_ner
        self.llm_rdf = llm_rdf or llm_ner

    async def process_paragraphs(self, paragraphs: List[str]) -> List[dict]:
        """
        异步处理多个段落。
        """
        from .utils.hash import get_sha256

        results = []
        total = len(paragraphs)
        
        for i, pg in enumerate(paragraphs, start=1):
            # 打印进度日志，让用户知道没有卡死
            logger.info(f"[IEProcess] 正在处理第 {i}/{total} 段文本 (长度: {len(pg)})...")
            
            # 使用 asyncio.to_thread 包装同步阻塞调用，防止死锁
            # 这样 info_extract_from_str 内部的 asyncio.run 会在独立线程的新 loop 中运行
            try:
                entities, triples = await asyncio.to_thread(
                    info_extract_from_str, self.llm_ner, self.llm_rdf, pg
                )

                if entities is not None:
                    results.append(
                        {
                            "idx": get_sha256(pg),
                            "passage": pg,
                            "extracted_entities": entities,
                            "extracted_triples": triples,
                        }
                    )
                    logger.info(f"[IEProcess] 第 {i}/{total} 段处理完成，提取到 {len(entities)} 个实体")
                else:
                    logger.warning(f"[IEProcess] 第 {i}/{total} 段提取失败（返回为空）")
            except Exception as e:
                logger.error(f"[IEProcess] 处理第 {i}/{total} 段时发生异常: {e}")

        return results
