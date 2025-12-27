"""
表达方式自动检查定时任务

功能：
1. 定期随机选取指定数量的表达方式
2. 使用LLM进行评估
3. 通过评估的：rejected=0, checked=1
4. 未通过评估的：rejected=1, checked=1
"""

import asyncio
import json
import random
from typing import List

from src.common.database.database_model import Expression
from src.common.logger import get_logger
from src.config.config import global_config
from src.config.config import model_config
from src.llm_models.utils_model import LLMRequest
from src.manager.async_task_manager import AsyncTask

logger = get_logger("expression_auto_check_task")


def create_evaluation_prompt(situation: str, style: str) -> str:
    """
    创建评估提示词
    
    Args:
        situation: 情境
        style: 风格
        
    Returns:
        评估提示词
    """
    # 基础评估标准
    base_criteria = [
        "表达方式或言语风格 是否与使用条件或使用情景 匹配",
        "允许部分语法错误或口头化或缺省出现",
        "表达方式不能太过特指，需要具有泛用性",
        "一般不涉及具体的人名或名称"
    ]
    
    # 从配置中获取额外的自定义标准
    custom_criteria = global_config.expression.expression_auto_check_custom_criteria
    
    # 合并所有评估标准
    all_criteria = base_criteria.copy()
    if custom_criteria:
        all_criteria.extend(custom_criteria)
    
    # 构建评估标准列表字符串
    criteria_list = "\n".join([f"{i+1}. {criterion}" for i, criterion in enumerate(all_criteria)])
    
    prompt = f"""请评估以下表达方式或语言风格以及使用条件或使用情景是否合适：
使用条件或使用情景：{situation}
表达方式或言语风格：{style}

请从以下方面进行评估：
{criteria_list}

请以JSON格式输出评估结果：
{{
    "suitable": true/false,
    "reason": "评估理由（如果不合适，请说明原因）"

}}
如果合适，suitable设为true；如果不合适，suitable设为false，并在reason中说明原因。
请严格按照JSON格式输出，不要包含其他内容。"""
    
    return prompt

judge_llm = LLMRequest(
    model_set=model_config.model_task_config.tool_use,
    request_type="expression_check"
)

async def single_expression_check(situation: str, style: str) -> tuple[bool, str, str]:
    """
    执行单次LLM评估
    
    Args:
        situation: 情境
        style: 风格
        
    Returns:
        (suitable, reason, error) 元组，如果出错则 suitable 为 False，error 包含错误信息
    """
    try:
        prompt = create_evaluation_prompt(situation, style)
        logger.debug(f"正在评估表达方式: situation={situation}, style={style}")
        
        response, (reasoning, model_name, _) = await judge_llm.generate_response_async(
            prompt=prompt,
            temperature=0.6,
            max_tokens=1024
        )
        
        logger.debug(f"LLM响应: {response}")
        
        # 解析JSON响应
        try:
            evaluation = json.loads(response)
        except json.JSONDecodeError as e:
            import re
            json_match = re.search(r'\{[^{}]*"suitable"[^{}]*\}', response, re.DOTALL)
            if json_match:
                evaluation = json.loads(json_match.group())
            else:
                raise ValueError("无法从响应中提取JSON格式的评估结果") from e
        
        suitable = evaluation.get("suitable", False)
        reason = evaluation.get("reason", "未提供理由")
        
        logger.debug(f"评估结果: {'通过' if suitable else '不通过'}")
        return suitable, reason, None
            
    except Exception as e:
        logger.error(f"评估表达方式 (situation={situation}, style={style}) 时出错: {e}")
        return False, f"评估过程出错: {str(e)}", str(e)


class ExpressionAutoCheckTask(AsyncTask):
    """表达方式自动检查定时任务"""

    def __init__(self):
        # 从配置中获取检查间隔和一次检查数量
        check_interval = global_config.expression.expression_auto_check_interval
        super().__init__(
            task_name="Expression Auto Check Task",
            wait_before_start=60,  # 启动后等待60秒再开始第一次检查
            run_interval=check_interval
        )

    async def _select_expressions(self, count: int) -> List[Expression]:
        """
        随机选择指定数量的未检查表达方式
        
        Args:
            count: 需要选择的数量
            
        Returns:
            选中的表达方式列表
        """
        try:
            # 查询所有未检查的表达方式（checked=False）
            unevaluated_expressions = list(
                Expression.select().where(~Expression.checked)
            )
            
            if not unevaluated_expressions:
                logger.info("没有未检查的表达方式")
                return []
            
            # 随机选择指定数量
            selected_count = min(count, len(unevaluated_expressions))
            selected = random.sample(unevaluated_expressions, selected_count)
            
            logger.info(f"从 {len(unevaluated_expressions)} 条未检查表达方式中随机选择了 {selected_count} 条")
            return selected
            
        except Exception as e:
            logger.error(f"选择表达方式时出错: {e}")
            return []

    async def _evaluate_expression(self, expression: Expression) -> bool:
        """
        评估单个表达方式
        
        Args:
            expression: 要评估的表达方式
            
        Returns:
            True表示通过，False表示不通过
        """
        
        suitable, reason, error = await single_expression_check(
            expression.situation,
            expression.style,
        )
        
        # 更新数据库
        try:
            expression.checked = True
            expression.rejected = not suitable  # 通过则rejected=0，不通过则rejected=1
            expression.save()
            
            status = "通过" if suitable else "不通过"
            logger.info(
                f"表达方式评估完成 [ID: {expression.id}] - {status} | "
                f"Situation: {expression.situation}... | "
                f"Style: {expression.style}... | "
                f"Reason: {reason[:50]}..."
            )
            
            if error:
                logger.warning(f"表达方式评估时出现错误 [ID: {expression.id}]: {error}")
            
            return suitable
            
        except Exception as e:
            logger.error(f"更新表达方式状态失败 [ID: {expression.id}]: {e}")
            return False

    async def run(self):
        """执行检查任务"""
        try:
            # 检查是否启用自动检查
            if not global_config.expression.expression_self_reflect:
                logger.debug("表达方式自动检查未启用，跳过本次执行")
                return
            
            check_count = global_config.expression.expression_auto_check_count
            if check_count <= 0:
                logger.warning(f"检查数量配置无效: {check_count}，跳过本次执行")
                return
            
            logger.info(f"开始执行表达方式自动检查，本次将检查 {check_count} 条")
            
            
            # 选择要检查的表达方式
            expressions = await self._select_expressions(check_count)
            
            if not expressions:
                logger.info("没有需要检查的表达方式")
                return
            
            # 逐个评估
            passed_count = 0
            failed_count = 0
            
            for i, expression in enumerate(expressions, 1):
                logger.info(f"正在评估 [{i}/{len(expressions)}]: ID={expression.id}")
                
                if await self._evaluate_expression(expression):
                    passed_count += 1
                else:
                    failed_count += 1
                
                # 避免请求过快
                await asyncio.sleep(0.3)
            
            logger.info(
                f"表达方式自动检查完成: 总计 {len(expressions)} 条，"
                f"通过 {passed_count} 条，不通过 {failed_count} 条"
            )
            
        except Exception as e:
            logger.error(f"执行表达方式自动检查任务时出错: {e}", exc_info=True)

