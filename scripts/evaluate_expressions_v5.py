"""
表达方式评估脚本

功能：
1. 随机读取指定数量的表达方式，获取其situation和style
2. 先进行人工评估（逐条手动评估）
3. 然后使用LLM进行评估
4. 对比人工评估和LLM评估的正确率、精确率、召回率、F1分数等指标（以人工评估为标准）
5. 不真正修改数据库，只是做评估
"""

import asyncio
import random
import json
import sys
import os
from typing import List, Dict

# 添加项目根目录到路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from src.common.database.database_model import Expression
from src.common.database.database import db
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config
from src.common.logger import get_logger

logger = get_logger("expression_evaluator_comparison")


def get_random_expressions(count: int = 10) -> List[Expression]:
    """
    随机读取指定数量的表达方式
    
    Args:
        count: 要读取的数量，默认10条
        
    Returns:
        表达方式列表
    """
    try:
        # 查询所有表达方式
        all_expressions = list(Expression.select())
        
        if not all_expressions:
            logger.warning("数据库中没有表达方式记录")
            return []
        
        # 如果总数少于请求数量，返回所有
        if len(all_expressions) <= count:
            logger.info(f"数据库中共有 {len(all_expressions)} 条表达方式，全部返回")
            return all_expressions
        
        # 随机选择指定数量
        selected = random.sample(all_expressions, count)
        logger.info(f"从 {len(all_expressions)} 条表达方式中随机选择了 {len(selected)} 条")
        return selected
        
    except Exception as e:
        logger.error(f"随机读取表达方式失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def manual_evaluate_expression(expression: Expression, index: int, total: int) -> Dict:
    """
    人工评估单个表达方式
    
    Args:
        expression: 表达方式对象
        index: 当前索引（从1开始）
        total: 总数
        
    Returns:
        评估结果字典，包含：
        - expression_id: 表达方式ID
        - situation: 情境
        - style: 风格
        - suitable: 是否合适（人工评估）
        - reason: 评估理由（始终为None）
    """
    print("\n" + "=" * 60)
    print(f"人工评估 [{index}/{total}]")
    print("=" * 60)
    print(f"Situation: {expression.situation}")
    print(f"Style: {expression.style}")
    print("\n请评估该表达方式是否合适：")
    print("  输入 'y' 或 'yes' 或 '1' 表示合适（通过）")
    print("  输入 'n' 或 'no' 或 '0' 表示不合适（不通过）")
    print("  输入 'q' 或 'quit' 退出评估")
    
    while True:
        user_input = input("\n您的评估 (y/n/q): ").strip().lower()
        
        if user_input in ['q', 'quit']:
            print("退出评估")
            return None
        
        if user_input in ['y', 'yes', '1', '是', '通过']:
            suitable = True
            break
        elif user_input in ['n', 'no', '0', '否', '不通过']:
            suitable = False
            break
        else:
            print("输入无效，请重新输入 (y/n/q)")
    
    result = {
        "expression_id": expression.id,
        "situation": expression.situation,
        "style": expression.style,
        "suitable": suitable,
        "reason": None,
        "evaluator": "manual"
    }
    
    print(f"\n✓ 已记录：{'通过' if suitable else '不通过'}")
    
    return result


def create_evaluation_prompt(situation: str, style: str) -> str:
    """
    创建评估提示词
    
    Args:
        situation: 情境
        style: 风格
        
    Returns:
        评估提示词
    """
    prompt = f"""请评估以下表达方式是否合适：

情境（situation）：{situation}
风格（style）：{style}

请从以下方面进行评估：
1. 情境描述是否清晰、准确
2. 风格表达是否合理、自然
3. 情境和风格是否匹配
4. 允许部分语法错误出现
5. 允许口头化或缺省表达
6. 允许部分上下文缺失

请以JSON格式输出评估结果：
{{
    "suitable": true/false,
    "reason": "评估理由（如果不合适，请说明原因）"
}}

如果合适，suitable设为true；如果不合适，suitable设为false，并在reason中说明原因。
请严格按照JSON格式输出，不要包含其他内容。"""
    
    return prompt


async def _single_llm_evaluation(expression: Expression, llm: LLMRequest) -> tuple[bool, str, str | None]:
    """
    执行单次LLM评估
    
    Args:
        expression: 表达方式对象
        llm: LLM请求实例
        
    Returns:
        (suitable, reason, error) 元组，如果出错则 suitable 为 False，error 包含错误信息
    """
    try:
        prompt = create_evaluation_prompt(expression.situation, expression.style)
        logger.debug(f"正在评估表达方式 ID: {expression.id}")
        
        response, (reasoning, model_name, _) = await llm.generate_response_async(
            prompt=prompt,
            temperature=0.6,
            max_tokens=1024
        )
        
        logger.debug(f"LLM响应: {response}")
        
        # 解析JSON响应
        try:
            evaluation = json.loads(response)
        except json.JSONDecodeError:
            import re
            json_match = re.search(r'\{[^{}]*"suitable"[^{}]*\}', response, re.DOTALL)
            if json_match:
                evaluation = json.loads(json_match.group())
            else:
                raise ValueError("无法从响应中提取JSON格式的评估结果")
        
        suitable = evaluation.get("suitable", False)
        reason = evaluation.get("reason", "未提供理由")
        
        logger.debug(f"评估结果: {'通过' if suitable else '不通过'}")
        return suitable, reason, None
            
    except Exception as e:
        logger.error(f"评估表达方式 ID: {expression.id} 时出错: {e}")
        return False, f"评估过程出错: {str(e)}", str(e)


async def evaluate_expression_llm(expression: Expression, llm: LLMRequest) -> Dict:
    """
    使用LLM评估单个表达方式
    
    Args:
        expression: 表达方式对象
        llm: LLM请求实例
        
    Returns:
        评估结果字典
    """
    logger.info(f"开始评估表达方式 ID: {expression.id}")
    
    suitable, reason, error = await _single_llm_evaluation(expression, llm)
    
    if error:
        suitable = False
    
    logger.info(f"评估完成: {'通过' if suitable else '不通过'}")
    
    return {
        "expression_id": expression.id,
        "situation": expression.situation,
        "style": expression.style,
        "suitable": suitable,
        "reason": reason,
        "error": error,
        "evaluator": "llm"
    }


def compare_evaluations(manual_results: List[Dict], llm_results: List[Dict], method_name: str) -> Dict:
    """
    对比人工评估和LLM评估的结果
    
    Args:
        manual_results: 人工评估结果列表
        llm_results: LLM评估结果列表
        method_name: 评估方法名称（用于标识）
        
    Returns:
        对比分析结果字典
    """
    # 按expression_id建立映射
    llm_dict = {r["expression_id"]: r for r in llm_results}
    
    total = len(manual_results)
    matched = 0
    true_positives = 0
    true_negatives = 0
    false_positives = 0
    false_negatives = 0
    
    for manual_result in manual_results:
        llm_result = llm_dict.get(manual_result["expression_id"])
        if llm_result is None:
            continue
        
        manual_suitable = manual_result["suitable"]
        llm_suitable = llm_result["suitable"]
        
        if manual_suitable == llm_suitable:
            matched += 1
        
        if manual_suitable and llm_suitable:
            true_positives += 1
        elif not manual_suitable and not llm_suitable:
            true_negatives += 1
        elif not manual_suitable and llm_suitable:
            false_positives += 1
        elif manual_suitable and not llm_suitable:
            false_negatives += 1
    
    accuracy = (matched / total * 100) if total > 0 else 0
    precision = (true_positives / (true_positives + false_positives) * 100) if (true_positives + false_positives) > 0 else 0
    recall = (true_positives / (true_positives + false_negatives) * 100) if (true_positives + false_negatives) > 0 else 0
    f1_score = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0
    specificity = (true_negatives / (true_negatives + false_positives) * 100) if (true_negatives + false_positives) > 0 else 0
    
    random_baseline = 50.0
    accuracy_above_random = accuracy - random_baseline
    accuracy_improvement_ratio = (accuracy / random_baseline) if random_baseline > 0 else 0
    
    return {
        "method": method_name,
        "total": total,
        "matched": matched,
        "accuracy": accuracy,
        "accuracy_above_random": accuracy_above_random,
        "accuracy_improvement_ratio": accuracy_improvement_ratio,
        "true_positives": true_positives,
        "true_negatives": true_negatives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1_score": f1_score,
        "specificity": specificity
    }




async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("开始表达方式评估")
    logger.info("=" * 60)
    
    # 初始化数据库连接
    try:
        db.connect(reuse_if_open=True)
        logger.info("数据库连接成功")
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return
    
    # 1. 随机读取表达方式
    logger.info("\n步骤1: 随机读取表达方式")
    expressions = get_random_expressions(10)
    if not expressions:
        logger.error("没有可用的表达方式，退出")
        return
    logger.info(f"成功读取 {len(expressions)} 条表达方式")
    
    # 2. 人工评估
    print("\n" + "=" * 60)
    print("开始人工评估")
    print("=" * 60)
    print(f"共需要评估 {len(expressions)} 条表达方式")
    print("请逐条进行评估...\n")
    
    manual_results = []
    for i, expression in enumerate(expressions, 1):
        manual_result = manual_evaluate_expression(expression, i, len(expressions))
        if manual_result is None:
            print("\n评估已中断")
            return
        manual_results.append(manual_result)
    
    print("\n" + "=" * 60)
    print("人工评估完成")
    print("=" * 60)
    
    # 3. 创建LLM实例并评估
    logger.info("\n步骤3: 创建LLM实例")
    try:
        llm = LLMRequest(
            model_set=model_config.model_task_config.tool_use,
            request_type="expression_evaluator_comparison"
        )
    except Exception as e:
        logger.error(f"创建LLM实例失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return
    
    logger.info("\n步骤4: 开始LLM评估")
    llm_results = []
    for i, expression in enumerate(expressions, 1):
        logger.info(f"LLM评估进度: {i}/{len(expressions)}")
        llm_results.append(await evaluate_expression_llm(expression, llm))
        await asyncio.sleep(0.3)
    
    # 4. 对比分析并输出结果
    comparison = compare_evaluations(manual_results, llm_results, "LLM评估")
    
    print("\n" + "=" * 60)
    print("评估结果（以人工评估为标准）")
    print("=" * 60)
    print("\n评估目标：")
    print("  1. 核心能力：将不合适的项目正确提取出来（特定负类召回率）")
    print("  2. 次要能力：尽可能少的误删合适的项目（召回率）")
    
    # 详细评估结果（核心指标优先）
    print("\n【详细对比】")
    print(f"\n--- {comparison['method']} ---")
    print(f"  总数: {comparison['total']} 条")
    print()
    print("  【核心能力指标】")
    print(f"  ⭐ 特定负类召回率: {comparison['specificity']:.2f}% (将不合适项目正确提取出来的能力)")
    print(f"     - 计算: TN / (TN + FP) = {comparison['true_negatives']} / ({comparison['true_negatives']} + {comparison['false_positives']})")
    print(f"     - 含义: 在 {comparison['true_negatives'] + comparison['false_positives']} 个实际不合适的项目中，正确识别出 {comparison['true_negatives']} 个")
    print(f"     - 随机水平: 50.00% (当前高于随机: {comparison['specificity'] - 50.0:+.2f}%)")
    print()
    print(f"  ⭐ 召回率: {comparison['recall']:.2f}% (尽可能少的误删合适项目的能力)")
    print(f"     - 计算: TP / (TP + FN) = {comparison['true_positives']} / ({comparison['true_positives']} + {comparison['false_negatives']})")
    print(f"     - 含义: 在 {comparison['true_positives'] + comparison['false_negatives']} 个实际合适的项目中，正确识别出 {comparison['true_positives']} 个")
    print(f"     - 随机水平: 50.00% (当前高于随机: {comparison['recall'] - 50.0:+.2f}%)")
    print()
    print("  【其他指标】")
    print(f"  准确率: {comparison['accuracy']:.2f}% (整体判断正确率)")
    print(f"  精确率: {comparison['precision']:.2f}% (判断为合适的项目中，实际合适的比例)")
    print(f"  F1分数: {comparison['f1_score']:.2f} (精确率和召回率的调和平均)")
    print(f"  匹配数: {comparison['matched']}/{comparison['total']}")
    print()
    print("  【分类统计】")
    print(f"  TP (正确识别为合适): {comparison['true_positives']}")
    print(f"  TN (正确识别为不合适): {comparison['true_negatives']} ⭐")
    print(f"  FP (误判为合适): {comparison['false_positives']} ⚠️")
    print(f"  FN (误删合适项目): {comparison['false_negatives']} ⚠️")
    
    # 5. 输出人工评估不通过但LLM误判为通过的详细信息
    print("\n" + "=" * 60)
    print("人工评估不通过但LLM误判为通过的项目（FP - False Positive）")
    print("=" * 60)
    
    # 按expression_id建立映射
    llm_dict = {r["expression_id"]: r for r in llm_results}
    
    fp_items = []
    for manual_result in manual_results:
        llm_result = llm_dict.get(manual_result["expression_id"])
        if llm_result is None:
            continue
        
        # 人工评估不通过，但LLM评估通过（FP情况）
        if not manual_result["suitable"] and llm_result["suitable"]:
            fp_items.append({
                "expression_id": manual_result["expression_id"],
                "situation": manual_result["situation"],
                "style": manual_result["style"],
                "manual_suitable": manual_result["suitable"],
                "llm_suitable": llm_result["suitable"],
                "llm_reason": llm_result.get("reason", "未提供理由"),
                "llm_error": llm_result.get("error")
            })
    
    if fp_items:
        print(f"\n共找到 {len(fp_items)} 条误判项目：\n")
        for idx, item in enumerate(fp_items, 1):
            print(f"--- [{idx}] 项目 ID: {item['expression_id']} ---")
            print(f"Situation: {item['situation']}")
            print(f"Style: {item['style']}")
            print("人工评估: 不通过 ❌")
            print("LLM评估: 通过 ✅ (误判)")
            if item.get('llm_error'):
                print(f"LLM错误: {item['llm_error']}")
            print(f"LLM理由: {item['llm_reason']}")
            print()
    else:
        print("\n✓ 没有误判项目（所有人工评估不通过的项目都被LLM正确识别为不通过）")
    
    # 6. 保存结果到JSON文件
    output_file = os.path.join(project_root, "data", "expression_evaluation_comparison.json")
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "manual_results": manual_results,
                "llm_results": llm_results,
                "comparison": comparison
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"\n评估结果已保存到: {output_file}")
    except Exception as e:
        logger.warning(f"保存结果到文件失败: {e}")
    
    print("\n" + "=" * 60)
    print("评估完成")
    print("=" * 60)
    
    # 关闭数据库连接
    try:
        db.close()
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.warning(f"关闭数据库连接时出错: {e}")


if __name__ == "__main__":
    asyncio.run(main())

