"""
表达方式LLM评估脚本

功能：
1. 读取已保存的人工评估结果（作为效标）
2. 使用LLM对相同项目进行评估
3. 对比人工评估和LLM评估的结果，输出分析报告
"""

import asyncio
import argparse
import json
import random
import sys
import os
import glob
from typing import List, Dict, Set, Tuple

# 添加项目根目录到路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config
from src.common.logger import get_logger

logger = get_logger("expression_evaluator_llm")

# 评估结果文件路径
TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")


def load_manual_results() -> List[Dict]:
    """
    加载人工评估结果（自动读取temp目录下所有JSON文件并合并）
    
    Returns:
        人工评估结果列表（已去重）
    """
    if not os.path.exists(TEMP_DIR):
        logger.error(f"未找到temp目录: {TEMP_DIR}")
        print("\n✗ 错误：未找到temp目录")
        print("   请先运行 evaluate_expressions_manual.py 进行人工评估")
        return []
    
    # 查找所有JSON文件
    json_files = glob.glob(os.path.join(TEMP_DIR, "*.json"))
    
    if not json_files:
        logger.error(f"temp目录下未找到JSON文件: {TEMP_DIR}")
        print("\n✗ 错误：temp目录下未找到JSON文件")
        print("   请先运行 evaluate_expressions_manual.py 进行人工评估")
        return []
    
    logger.info(f"找到 {len(json_files)} 个JSON文件")
    print(f"\n找到 {len(json_files)} 个JSON文件:")
    for json_file in json_files:
        print(f"  - {os.path.basename(json_file)}")
    
    # 读取并合并所有JSON文件
    all_results = []
    seen_pairs: Set[Tuple[str, str]] = set()  # 用于去重
    
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                results = data.get("manual_results", [])
                
                # 去重：使用(situation, style)作为唯一标识
                for result in results:
                    if "situation" not in result or "style" not in result:
                        logger.warning(f"跳过无效数据（缺少必要字段）: {result}")
                        continue
                    
                    pair = (result["situation"], result["style"])
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        all_results.append(result)
                
                logger.info(f"从 {os.path.basename(json_file)} 加载了 {len(results)} 条结果")
        except Exception as e:
            logger.error(f"加载文件 {json_file} 失败: {e}")
            print(f"  警告：加载文件 {os.path.basename(json_file)} 失败: {e}")
            continue
    
    logger.info(f"成功合并 {len(all_results)} 条人工评估结果（去重后）")
    print(f"\n✓ 成功合并 {len(all_results)} 条人工评估结果（已去重）")
    
    return all_results


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


async def evaluate_expression_llm(situation: str, style: str, llm: LLMRequest) -> Dict:
    """
    使用LLM评估单个表达方式
    
    Args:
        situation: 情境
        style: 风格
        llm: LLM请求实例
        
    Returns:
        评估结果字典
    """
    logger.info(f"开始评估表达方式: situation={situation}, style={style}")
    
    suitable, reason, error = await _single_llm_evaluation(situation, style, llm)
    
    if error:
        suitable = False
    
    logger.info(f"评估完成: {'通过' if suitable else '不通过'}")
    
    return {
        "situation": situation,
        "style": style,
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
    # 按(situation, style)建立映射
    llm_dict = {(r["situation"], r["style"]): r for r in llm_results}
    
    total = len(manual_results)
    matched = 0
    true_positives = 0
    true_negatives = 0
    false_positives = 0
    false_negatives = 0
    
    for manual_result in manual_results:
        pair = (manual_result["situation"], manual_result["style"])
        llm_result = llm_dict.get(pair)
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
    
    # 计算人工效标的不合适率
    manual_unsuitable_count = true_negatives + false_positives  # 人工评估不合适的总数
    manual_unsuitable_rate = (manual_unsuitable_count / total * 100) if total > 0 else 0
    
    # 计算经过LLM删除后剩余项目中的不合适率
    # 在所有项目中，移除LLM判定为不合适的项目后，剩下的项目 = TP + FP（LLM判定为合适的项目）
    # 在这些剩下的项目中，按人工评定的不合适项目 = FP（人工认为不合适，但LLM认为合适）
    llm_kept_count = true_positives + false_positives  # LLM判定为合适的项目总数（保留的项目）
    llm_kept_unsuitable_rate = (false_positives / llm_kept_count * 100) if llm_kept_count > 0 else 0
    
    # 两者百分比相减（评估LLM评定修正后的不合适率是否有降低）
    rate_difference = manual_unsuitable_rate - llm_kept_unsuitable_rate
    
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
        "specificity": specificity,
        "manual_unsuitable_rate": manual_unsuitable_rate,
        "llm_kept_unsuitable_rate": llm_kept_unsuitable_rate,
        "rate_difference": rate_difference
    }


async def main(count: int | None = None):
    """
    主函数
    
    Args:
        count: 随机选取的数据条数，如果为None则使用全部数据
    """
    logger.info("=" * 60)
    logger.info("开始表达方式LLM评估")
    logger.info("=" * 60)
    
    # 1. 加载人工评估结果
    print("\n步骤1: 加载人工评估结果")
    manual_results = load_manual_results()
    if not manual_results:
        return
    
    print(f"成功加载 {len(manual_results)} 条人工评估结果")
    
    # 如果指定了数量，随机选择指定数量的数据
    if count is not None:
        if count <= 0:
            print(f"\n✗ 错误：指定的数量必须大于0，当前值: {count}")
            return
        if count > len(manual_results):
            print(f"\n⚠ 警告：指定的数量 ({count}) 大于可用数据量 ({len(manual_results)})，将使用全部数据")
        else:
            random.seed()  # 使用系统时间作为随机种子
            manual_results = random.sample(manual_results, count)
            print(f"随机选取 {len(manual_results)} 条数据进行评估")
    
    # 验证数据完整性
    valid_manual_results = []
    for r in manual_results:
        if "situation" in r and "style" in r:
            valid_manual_results.append(r)
        else:
            logger.warning(f"跳过无效数据: {r}")
    
    if len(valid_manual_results) != len(manual_results):
        print(f"警告：{len(manual_results) - len(valid_manual_results)} 条数据缺少必要字段，已跳过")
    
    print(f"有效数据: {len(valid_manual_results)} 条")
    
    # 2. 创建LLM实例并评估
    print("\n步骤2: 创建LLM实例")
    try:
        llm = LLMRequest(
            model_set=model_config.model_task_config.tool_use,
            request_type="expression_evaluator_llm"
        )
    except Exception as e:
        logger.error(f"创建LLM实例失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return
    
    print("\n步骤3: 开始LLM评估")
    llm_results = []
    for i, manual_result in enumerate(valid_manual_results, 1):
        print(f"LLM评估进度: {i}/{len(valid_manual_results)}")
        llm_results.append(await evaluate_expression_llm(
            manual_result["situation"],
            manual_result["style"],
            llm
        ))
        await asyncio.sleep(0.3)
    
    # 5. 输出FP和FN项目（在评估结果之前）
    llm_dict = {(r["situation"], r["style"]): r for r in llm_results}
    
    # 5.1 输出FP项目（人工评估不通过但LLM误判为通过）
    print("\n" + "=" * 60)
    print("人工评估不通过但LLM误判为通过的项目（FP - False Positive）")
    print("=" * 60)
    
    fp_items = []
    for manual_result in valid_manual_results:
        pair = (manual_result["situation"], manual_result["style"])
        llm_result = llm_dict.get(pair)
        if llm_result is None:
            continue
        
        # 人工评估不通过，但LLM评估通过（FP情况）
        if not manual_result["suitable"] and llm_result["suitable"]:
            fp_items.append({
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
            print(f"--- [{idx}] ---")
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
    
    # 5.2 输出FN项目（人工评估通过但LLM误判为不通过）
    print("\n" + "=" * 60)
    print("人工评估通过但LLM误判为不通过的项目（FN - False Negative）")
    print("=" * 60)
    
    fn_items = []
    for manual_result in valid_manual_results:
        pair = (manual_result["situation"], manual_result["style"])
        llm_result = llm_dict.get(pair)
        if llm_result is None:
            continue
        
        # 人工评估通过，但LLM评估不通过（FN情况）
        if manual_result["suitable"] and not llm_result["suitable"]:
            fn_items.append({
                "situation": manual_result["situation"],
                "style": manual_result["style"],
                "manual_suitable": manual_result["suitable"],
                "llm_suitable": llm_result["suitable"],
                "llm_reason": llm_result.get("reason", "未提供理由"),
                "llm_error": llm_result.get("error")
            })
    
    if fn_items:
        print(f"\n共找到 {len(fn_items)} 条误删项目：\n")
        for idx, item in enumerate(fn_items, 1):
            print(f"--- [{idx}] ---")
            print(f"Situation: {item['situation']}")
            print(f"Style: {item['style']}")
            print("人工评估: 通过 ✅")
            print("LLM评估: 不通过 ❌ (误删)")
            if item.get('llm_error'):
                print(f"LLM错误: {item['llm_error']}")
            print(f"LLM理由: {item['llm_reason']}")
            print()
    else:
        print("\n✓ 没有误删项目（所有人工评估通过的项目都被LLM正确识别为通过）")
    
    # 6. 对比分析并输出结果
    comparison = compare_evaluations(valid_manual_results, llm_results, "LLM评估")
    
    print("\n" + "=" * 60)
    print("评估结果（以人工评估为标准）")
    print("=" * 60)
    
    # 详细评估结果（核心指标优先）
    print(f"\n--- {comparison['method']} ---")
    print(f"  总数: {comparison['total']} 条")
    print()
    # print("  【核心能力指标】")
    print(f"  特定负类召回率: {comparison['specificity']:.2f}% (将不合适项目正确提取出来的能力)")
    print(f"     - 计算: TN / (TN + FP) = {comparison['true_negatives']} / ({comparison['true_negatives']} + {comparison['false_positives']})")
    print(f"     - 含义: 在 {comparison['true_negatives'] + comparison['false_positives']} 个实际不合适的项目中，正确识别出 {comparison['true_negatives']} 个")
    # print(f"     - 随机水平: 50.00% (当前高于随机: {comparison['specificity'] - 50.0:+.2f}%)")
    print()
    print(f"  召回率: {comparison['recall']:.2f}% (尽可能少的误删合适项目的能力)")
    print(f"     - 计算: TP / (TP + FN) = {comparison['true_positives']} / ({comparison['true_positives']} + {comparison['false_negatives']})")
    print(f"     - 含义: 在 {comparison['true_positives'] + comparison['false_negatives']} 个实际合适的项目中，正确识别出 {comparison['true_positives']} 个")
    # print(f"     - 随机水平: 50.00% (当前高于随机: {comparison['recall'] - 50.0:+.2f}%)")
    print()
    print("  【其他指标】")
    print(f"  准确率: {comparison['accuracy']:.2f}% (整体判断正确率)")
    print(f"  精确率: {comparison['precision']:.2f}% (判断为合适的项目中，实际合适的比例)")
    print(f"  F1分数: {comparison['f1_score']:.2f} (精确率和召回率的调和平均)")
    print(f"  匹配数: {comparison['matched']}/{comparison['total']}")
    print()
    print("  【不合适率分析】")
    print(f"  人工效标的不合适率: {comparison['manual_unsuitable_rate']:.2f}%")
    print(f"     - 计算: (TN + FP) / 总数 = ({comparison['true_negatives']} + {comparison['false_positives']}) / {comparison['total']}")
    print(f"     - 含义: 在人工评估中，有 {comparison['manual_unsuitable_rate']:.2f}% 的项目被判定为不合适")
    print()
    print(f"  经过LLM删除后剩余项目中的不合适率: {comparison['llm_kept_unsuitable_rate']:.2f}%")
    print(f"     - 计算: FP / (TP + FP) = {comparison['false_positives']} / ({comparison['true_positives']} + {comparison['false_positives']})")
    print(f"     - 含义: 在所有项目中，移除LLM判定为不合适的项目后，在剩下的 {comparison['true_positives'] + comparison['false_positives']} 个项目中，人工认为不合适的项目占 {comparison['llm_kept_unsuitable_rate']:.2f}%")
    print()
    # print(f"  两者百分比差值: {comparison['rate_difference']:+.2f}%")
    # print(f"     - 计算: 人工效标不合适率 - LLM删除后剩余项目不合适率 = {comparison['manual_unsuitable_rate']:.2f}% - {comparison['llm_kept_unsuitable_rate']:.2f}%")
    # print(f"     - 含义: {'LLM删除后剩余项目中的不合适率降低了' if comparison['rate_difference'] > 0 else 'LLM删除后剩余项目中的不合适率反而升高了' if comparison['rate_difference'] < 0 else '两者相等'} ({'✓ LLM删除有效' if comparison['rate_difference'] > 0 else '✗ LLM删除效果不佳' if comparison['rate_difference'] < 0 else '效果相同'})")
    # print()
    print("  【分类统计】")
    print(f"  TP (正确识别为合适): {comparison['true_positives']}")
    print(f"  TN (正确识别为不合适): {comparison['true_negatives']} ⭐")
    print(f"  FP (误判为合适): {comparison['false_positives']} ⚠️")
    print(f"  FN (误删合适项目): {comparison['false_negatives']} ⚠️")
    
    # 7. 保存结果到JSON文件
    output_file = os.path.join(project_root, "data", "expression_evaluation_llm.json")
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "manual_results": valid_manual_results,
                "llm_results": llm_results,
                "comparison": comparison
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"\n评估结果已保存到: {output_file}")
    except Exception as e:
        logger.warning(f"保存结果到文件失败: {e}")
    
    print("\n" + "=" * 60)
    print("评估完成")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="表达方式LLM评估脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python evaluate_expressions_llm_v6.py              # 使用全部数据
  python evaluate_expressions_llm_v6.py -n 50       # 随机选取50条数据
  python evaluate_expressions_llm_v6.py --count 100 # 随机选取100条数据
        """
    )
    parser.add_argument(
        "-n", "--count",
        type=int,
        default=None,
        help="随机选取的数据条数（默认：使用全部数据）"
    )
    
    args = parser.parse_args()
    asyncio.run(main(count=args.count))

