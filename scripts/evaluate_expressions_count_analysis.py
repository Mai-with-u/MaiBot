"""
表达方式按count分组的LLM评估和统计分析脚本

功能：
1. 随机选择50条表达，至少要有20条count>1的项目，然后进行LLM评估
2. 比较不同count之间的LLM评估合格率是否有显著差异
   - 首先每个count分开比较
   - 然后比较count为1和count大于1的两种
"""

import asyncio
import random
import json
import sys
import os
import re
from typing import List, Dict, Set, Tuple
from datetime import datetime
from collections import defaultdict

# 添加项目根目录到路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from src.common.database.database_model import Expression
from src.common.database.database import db
from src.common.logger import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config

logger = get_logger("expression_evaluator_count_analysis_llm")

# 评估结果文件路径
TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")
COUNT_ANALYSIS_FILE = os.path.join(TEMP_DIR, "count_analysis_evaluation_results.json")


def load_existing_results() -> tuple[List[Dict], Set[Tuple[str, str]]]:
    """
    加载已有的评估结果
    
    Returns:
        (已有结果列表, 已评估的项目(situation, style)元组集合)
    """
    if not os.path.exists(COUNT_ANALYSIS_FILE):
        return [], set()
    
    try:
        with open(COUNT_ANALYSIS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            results = data.get("evaluation_results", [])
            # 使用 (situation, style) 作为唯一标识
            evaluated_pairs = {(r["situation"], r["style"]) for r in results if "situation" in r and "style" in r}
            logger.info(f"已加载 {len(results)} 条已有评估结果")
            return results, evaluated_pairs
    except Exception as e:
        logger.error(f"加载已有评估结果失败: {e}")
        return [], set()


def save_results(evaluation_results: List[Dict]):
    """
    保存评估结果到文件
    
    Args:
        evaluation_results: 评估结果列表
    """
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        data = {
            "last_updated": datetime.now().isoformat(),
            "total_count": len(evaluation_results),
            "evaluation_results": evaluation_results
        }
        
        with open(COUNT_ANALYSIS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"评估结果已保存到: {COUNT_ANALYSIS_FILE}")
        print(f"\n✓ 评估结果已保存（共 {len(evaluation_results)} 条）")
    except Exception as e:
        logger.error(f"保存评估结果失败: {e}")
        print(f"\n✗ 保存评估结果失败: {e}")


def select_expressions_for_evaluation(
    evaluated_pairs: Set[Tuple[str, str]] = None
) -> List[Expression]:
    """
    选择用于评估的表达方式
    选择所有count>1的项目，然后选择两倍数量的count=1的项目
    
    Args:
        evaluated_pairs: 已评估的项目集合，用于避免重复
        
    Returns:
        选中的表达方式列表
    """
    if evaluated_pairs is None:
        evaluated_pairs = set()
    
    try:
        # 查询所有表达方式
        all_expressions = list(Expression.select())
        
        if not all_expressions:
            logger.warning("数据库中没有表达方式记录")
            return []
        
        # 过滤出未评估的项目
        unevaluated = [
            expr for expr in all_expressions 
            if (expr.situation, expr.style) not in evaluated_pairs
        ]
        
        if not unevaluated:
            logger.warning("所有项目都已评估完成")
            return []
        
        # 按count分组
        count_eq1 = [expr for expr in unevaluated if expr.count == 1]
        count_gt1 = [expr for expr in unevaluated if expr.count > 1]
        
        logger.info(f"未评估项目中：count=1的有{len(count_eq1)}条，count>1的有{len(count_gt1)}条")
        
        # 选择所有count>1的项目
        selected_count_gt1 = count_gt1.copy()
        
        # 选择count=1的项目，数量为count>1数量的2倍
        count_gt1_count = len(selected_count_gt1)
        count_eq1_needed = count_gt1_count * 2
        
        if len(count_eq1) < count_eq1_needed:
            logger.warning(f"count=1的项目只有{len(count_eq1)}条，少于需要的{count_eq1_needed}条，将选择全部{len(count_eq1)}条")
            count_eq1_needed = len(count_eq1)
        
        # 随机选择count=1的项目
        selected_count_eq1 = random.sample(count_eq1, count_eq1_needed) if count_eq1 and count_eq1_needed > 0 else []
        
        selected = selected_count_gt1 + selected_count_eq1
        random.shuffle(selected)  # 打乱顺序
        
        logger.info(f"已选择{len(selected)}条表达方式：count>1的有{len(selected_count_gt1)}条（全部），count=1的有{len(selected_count_eq1)}条（2倍）")
        
        return selected
        
    except Exception as e:
        logger.error(f"选择表达方式失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def create_evaluation_prompt(situation: str, style: str) -> str:
    """
    创建评估提示词
    
    Args:
        situation: 情境
        style: 风格
        
    Returns:
        评估提示词
    """
    prompt = f"""请评估以下表达方式或语言风格以及使用条件或使用情景是否合适：
使用条件或使用情景：{situation}
表达方式或言语风格：{style}

请从以下方面进行评估：
1. 表达方式或言语风格 是否与使用条件或使用情景 匹配
2. 允许部分语法错误或口头化或缺省出现
3. 表达方式不能太过特指，需要具有泛用性
4. 一般不涉及具体的人名或名称

请以JSON格式输出评估结果：
{{
    "suitable": true/false,
    "reason": "评估理由（如果不合适，请说明原因）"

}}
如果合适，suitable设为true；如果不合适，suitable设为false，并在reason中说明原因。
请严格按照JSON格式输出，不要包含其他内容。"""
    
    return prompt


async def _single_llm_evaluation(situation: str, style: str, llm: LLMRequest) -> tuple[bool, str, str | None]:
    """
    执行单次LLM评估
    
    Args:
        situation: 情境
        style: 风格
        llm: LLM请求实例
        
    Returns:
        (suitable, reason, error) 元组，如果出错则 suitable 为 False，error 包含错误信息
    """
    try:
        prompt = create_evaluation_prompt(situation, style)
        logger.debug(f"正在评估表达方式: situation={situation}, style={style}")
        
        response, (reasoning, model_name, _) = await llm.generate_response_async(
            prompt=prompt,
            temperature=0.6,
            max_tokens=1024
        )
        
        logger.debug(f"LLM响应: {response}")
        
        # 解析JSON响应
        try:
            evaluation = json.loads(response)
        except json.JSONDecodeError as e:
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


async def llm_evaluate_expression(expression: Expression, llm: LLMRequest) -> Dict:
    """
    使用LLM评估单个表达方式
    
    Args:
        expression: 表达方式对象
        llm: LLM请求实例
        
    Returns:
        评估结果字典
    """
    logger.info(f"开始评估表达方式: situation={expression.situation}, style={expression.style}, count={expression.count}")
    
    suitable, reason, error = await _single_llm_evaluation(expression.situation, expression.style, llm)
    
    if error:
        suitable = False
    
    logger.info(f"评估完成: {'通过' if suitable else '不通过'}")
    
    return {
        "situation": expression.situation,
        "style": expression.style,
        "count": expression.count,
        "suitable": suitable,
        "reason": reason,
        "error": error,
        "evaluator": "llm",
        "evaluated_at": datetime.now().isoformat()
    }


def perform_statistical_analysis(evaluation_results: List[Dict]):
    """
    对评估结果进行统计分析
    
    Args:
        evaluation_results: 评估结果列表
    """
    if not evaluation_results:
        print("\n没有评估结果可供分析")
        return
    
    print("\n" + "=" * 60)
    print("统计分析结果")
    print("=" * 60)
    
    # 按count分组统计
    count_groups = defaultdict(lambda: {"total": 0, "suitable": 0, "unsuitable": 0})
    
    for result in evaluation_results:
        count = result.get("count", 1)
        suitable = result.get("suitable", False)
        count_groups[count]["total"] += 1
        if suitable:
            count_groups[count]["suitable"] += 1
        else:
            count_groups[count]["unsuitable"] += 1
    
    # 显示每个count的统计
    print("\n【按count分组统计】")
    print("-" * 60)
    for count in sorted(count_groups.keys()):
        group = count_groups[count]
        total = group["total"]
        suitable = group["suitable"]
        unsuitable = group["unsuitable"]
        pass_rate = (suitable / total * 100) if total > 0 else 0
        
        print(f"Count = {count}:")
        print(f"  总数: {total}")
        print(f"  通过: {suitable} ({pass_rate:.2f}%)")
        print(f"  不通过: {unsuitable} ({100-pass_rate:.2f}%)")
        print()
    
    # 比较count=1和count>1
    count_eq1_group = {"total": 0, "suitable": 0, "unsuitable": 0}
    count_gt1_group = {"total": 0, "suitable": 0, "unsuitable": 0}
    
    for result in evaluation_results:
        count = result.get("count", 1)
        suitable = result.get("suitable", False)
        
        if count == 1:
            count_eq1_group["total"] += 1
            if suitable:
                count_eq1_group["suitable"] += 1
            else:
                count_eq1_group["unsuitable"] += 1
        else:
            count_gt1_group["total"] += 1
            if suitable:
                count_gt1_group["suitable"] += 1
            else:
                count_gt1_group["unsuitable"] += 1
    
    print("\n【Count=1 vs Count>1 对比】")
    print("-" * 60)
    
    eq1_total = count_eq1_group["total"]
    eq1_suitable = count_eq1_group["suitable"]
    eq1_pass_rate = (eq1_suitable / eq1_total * 100) if eq1_total > 0 else 0
    
    gt1_total = count_gt1_group["total"]
    gt1_suitable = count_gt1_group["suitable"]
    gt1_pass_rate = (gt1_suitable / gt1_total * 100) if gt1_total > 0 else 0
    
    print("Count = 1:")
    print(f"  总数: {eq1_total}")
    print(f"  通过: {eq1_suitable} ({eq1_pass_rate:.2f}%)")
    print(f"  不通过: {eq1_total - eq1_suitable} ({100-eq1_pass_rate:.2f}%)")
    print()
    print("Count > 1:")
    print(f"  总数: {gt1_total}")
    print(f"  通过: {gt1_suitable} ({gt1_pass_rate:.2f}%)")
    print(f"  不通过: {gt1_total - gt1_suitable} ({100-gt1_pass_rate:.2f}%)")
    print()
    
    # 进行卡方检验（简化版，使用2x2列联表）
    if eq1_total > 0 and gt1_total > 0:
        print("【统计显著性检验】")
        print("-" * 60)
        
        # 构建2x2列联表
        #         通过  不通过
        # count=1  a     b
        # count>1  c     d
        a = eq1_suitable
        b = eq1_total - eq1_suitable
        c = gt1_suitable
        d = gt1_total - gt1_suitable
        
        # 计算卡方统计量（简化版，使用Pearson卡方检验）
        n = eq1_total + gt1_total
        if n > 0:
            # 期望频数
            e_a = (eq1_total * (a + c)) / n
            e_b = (eq1_total * (b + d)) / n
            e_c = (gt1_total * (a + c)) / n
            e_d = (gt1_total * (b + d)) / n
            
            # 检查期望频数是否足够大（卡方检验要求每个期望频数>=5）
            min_expected = min(e_a, e_b, e_c, e_d)
            if min_expected < 5:
                print("警告：期望频数小于5，卡方检验可能不准确")
                print("建议使用Fisher精确检验")
            
            # 计算卡方值
            chi_square = 0
            if e_a > 0:
                chi_square += ((a - e_a) ** 2) / e_a
            if e_b > 0:
                chi_square += ((b - e_b) ** 2) / e_b
            if e_c > 0:
                chi_square += ((c - e_c) ** 2) / e_c
            if e_d > 0:
                chi_square += ((d - e_d) ** 2) / e_d
            
            # 自由度 = (行数-1) * (列数-1) = 1
            df = 1
            
            # 临界值（α=0.05）
            chi_square_critical_005 = 3.841
            chi_square_critical_001 = 6.635
            
            print(f"卡方统计量: {chi_square:.4f}")
            print(f"自由度: {df}")
            print(f"临界值 (α=0.05): {chi_square_critical_005}")
            print(f"临界值 (α=0.01): {chi_square_critical_001}")
            
            if chi_square >= chi_square_critical_001:
                print("结论: 在α=0.01水平下，count=1和count>1的合格率存在显著差异（p<0.01）")
            elif chi_square >= chi_square_critical_005:
                print("结论: 在α=0.05水平下，count=1和count>1的合格率存在显著差异（p<0.05）")
            else:
                print("结论: 在α=0.05水平下，count=1和count>1的合格率不存在显著差异（p≥0.05）")
            
            # 计算差异大小
            diff = abs(eq1_pass_rate - gt1_pass_rate)
            print(f"\n合格率差异: {diff:.2f}%")
            if diff > 10:
                print("差异较大（>10%）")
            elif diff > 5:
                print("差异中等（5-10%）")
            else:
                print("差异较小（<5%）")
        else:
            print("数据不足，无法进行统计检验")
    else:
        print("数据不足，无法进行count=1和count>1的对比分析")
    
    # 保存统计分析结果
    analysis_result = {
        "analysis_time": datetime.now().isoformat(),
        "count_groups": {str(k): v for k, v in count_groups.items()},
        "count_eq1": count_eq1_group,
        "count_gt1": count_gt1_group,
        "total_evaluated": len(evaluation_results)
    }
    
    try:
        analysis_file = os.path.join(TEMP_DIR, "count_analysis_statistics.json")
        with open(analysis_file, "w", encoding="utf-8") as f:
            json.dump(analysis_result, f, ensure_ascii=False, indent=2)
        print(f"\n✓ 统计分析结果已保存到: {analysis_file}")
    except Exception as e:
        logger.error(f"保存统计分析结果失败: {e}")


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("开始表达方式按count分组的LLM评估和统计分析")
    logger.info("=" * 60)
    
    # 初始化数据库连接
    try:
        db.connect(reuse_if_open=True)
        logger.info("数据库连接成功")
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return
    
    # 加载已有评估结果
    existing_results, evaluated_pairs = load_existing_results()
    evaluation_results = existing_results.copy()
    
    if evaluated_pairs:
        print(f"\n已加载 {len(existing_results)} 条已有评估结果")
        print(f"已评估项目数: {len(evaluated_pairs)}")
    
    # 检查是否需要继续评估（检查是否还有未评估的count>1项目）
    # 先查询未评估的count>1项目数量
    try:
        all_expressions = list(Expression.select())
        unevaluated_count_gt1 = [
            expr for expr in all_expressions 
            if expr.count > 1 and (expr.situation, expr.style) not in evaluated_pairs
        ]
        has_unevaluated = len(unevaluated_count_gt1) > 0
    except Exception as e:
        logger.error(f"查询未评估项目失败: {e}")
        has_unevaluated = False
    
    if has_unevaluated:
        print("\n" + "=" * 60)
        print("开始LLM评估")
        print("=" * 60)
        print("评估结果会自动保存到文件\n")
        
        # 创建LLM实例
        print("创建LLM实例...")
        try:
            llm = LLMRequest(
                model_set=model_config.model_task_config.tool_use,
                request_type="expression_evaluator_count_analysis_llm"
            )
            print("✓ LLM实例创建成功\n")
        except Exception as e:
            logger.error(f"创建LLM实例失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            print(f"\n✗ 创建LLM实例失败: {e}")
            db.close()
            return
        
        # 选择需要评估的表达方式（选择所有count>1的项目，然后选择两倍数量的count=1的项目）
        expressions = select_expressions_for_evaluation(
            evaluated_pairs=evaluated_pairs
        )
        
        if not expressions:
            print("\n没有可评估的项目")
        else:
            print(f"\n已选择 {len(expressions)} 条表达方式进行评估")
            print(f"其中 count>1 的有 {sum(1 for e in expressions if e.count > 1)} 条")
            print(f"其中 count=1 的有 {sum(1 for e in expressions if e.count == 1)} 条\n")
            
            batch_results = []
            for i, expression in enumerate(expressions, 1):
                print(f"LLM评估进度: {i}/{len(expressions)}")
                print(f"  Situation: {expression.situation}")
                print(f"  Style: {expression.style}")
                print(f"  Count: {expression.count}")
                
                llm_result = await llm_evaluate_expression(expression, llm)
                
                print(f"  结果: {'通过' if llm_result['suitable'] else '不通过'}")
                if llm_result.get('error'):
                    print(f"  错误: {llm_result['error']}")
                print()
                
                batch_results.append(llm_result)
                # 使用 (situation, style) 作为唯一标识
                evaluated_pairs.add((llm_result["situation"], llm_result["style"]))
                
                # 添加延迟以避免API限流
                await asyncio.sleep(0.3)
            
            # 将当前批次结果添加到总结果中
            evaluation_results.extend(batch_results)
            
            # 保存结果
            save_results(evaluation_results)
    else:
        print(f"\n所有count>1的项目都已评估完成，已有 {len(evaluation_results)} 条评估结果")
    
    # 进行统计分析
    if len(evaluation_results) > 0:
        perform_statistical_analysis(evaluation_results)
    else:
        print("\n没有评估结果可供分析")
    
    # 关闭数据库连接
    try:
        db.close()
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.warning(f"关闭数据库连接时出错: {e}")


if __name__ == "__main__":
    asyncio.run(main())

