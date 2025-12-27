"""
表达方式人工评估脚本

功能：
1. 不停随机抽取项目（不重复）进行人工评估
2. 将结果保存到 temp 文件夹下的 JSON 文件，作为效标（标准答案）
3. 支持继续评估（从已有文件中读取已评估的项目，避免重复）
"""

import random
import json
import sys
import os
from typing import List, Dict, Set, Tuple
from datetime import datetime

# 添加项目根目录到路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from src.common.database.database_model import Expression
from src.common.database.database import db
from src.common.logger import get_logger

logger = get_logger("expression_evaluator_manual")

# 评估结果文件路径
TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")
MANUAL_EVAL_FILE = os.path.join(TEMP_DIR, "manual_evaluation_results.json")


def load_existing_results() -> tuple[List[Dict], Set[Tuple[str, str]]]:
    """
    加载已有的评估结果
    
    Returns:
        (已有结果列表, 已评估的项目(situation, style)元组集合)
    """
    if not os.path.exists(MANUAL_EVAL_FILE):
        return [], set()
    
    try:
        with open(MANUAL_EVAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            results = data.get("manual_results", [])
            # 使用 (situation, style) 作为唯一标识
            evaluated_pairs = {(r["situation"], r["style"]) for r in results if "situation" in r and "style" in r}
            logger.info(f"已加载 {len(results)} 条已有评估结果")
            return results, evaluated_pairs
    except Exception as e:
        logger.error(f"加载已有评估结果失败: {e}")
        return [], set()


def save_results(manual_results: List[Dict]):
    """
    保存评估结果到文件
    
    Args:
        manual_results: 评估结果列表
    """
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        data = {
            "last_updated": datetime.now().isoformat(),
            "total_count": len(manual_results),
            "manual_results": manual_results
        }
        
        with open(MANUAL_EVAL_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"评估结果已保存到: {MANUAL_EVAL_FILE}")
        print(f"\n✓ 评估结果已保存（共 {len(manual_results)} 条）")
    except Exception as e:
        logger.error(f"保存评估结果失败: {e}")
        print(f"\n✗ 保存评估结果失败: {e}")


def get_unevaluated_expressions(evaluated_pairs: Set[Tuple[str, str]], batch_size: int = 10) -> List[Expression]:
    """
    获取未评估的表达方式
    
    Args:
        evaluated_pairs: 已评估的项目(situation, style)元组集合
        batch_size: 每次获取的数量
        
    Returns:
        未评估的表达方式列表
    """
    try:
        # 查询所有表达方式
        all_expressions = list(Expression.select())
        
        if not all_expressions:
            logger.warning("数据库中没有表达方式记录")
            return []
        
        # 过滤出未评估的项目：匹配 situation 和 style 均一致
        unevaluated = [
            expr for expr in all_expressions 
            if (expr.situation, expr.style) not in evaluated_pairs
        ]
        
        if not unevaluated:
            logger.info("所有项目都已评估完成")
            return []
        
        # 如果未评估数量少于请求数量，返回所有
        if len(unevaluated) <= batch_size:
            logger.info(f"剩余 {len(unevaluated)} 条未评估项目，全部返回")
            return unevaluated
        
        # 随机选择指定数量
        selected = random.sample(unevaluated, batch_size)
        logger.info(f"从 {len(unevaluated)} 条未评估项目中随机选择了 {len(selected)} 条")
        return selected
        
    except Exception as e:
        logger.error(f"获取未评估表达方式失败: {e}")
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
        评估结果字典，如果用户退出则返回 None
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
    print("  输入 's' 或 'skip' 跳过当前项目")
    
    while True:
        user_input = input("\n您的评估 (y/n/q/s): ").strip().lower()
        
        if user_input in ['q', 'quit']:
            print("退出评估")
            return None
        
        if user_input in ['s', 'skip']:
            print("跳过当前项目")
            return "skip"
        
        if user_input in ['y', 'yes', '1', '是', '通过']:
            suitable = True
            break
        elif user_input in ['n', 'no', '0', '否', '不通过']:
            suitable = False
            break
        else:
            print("输入无效，请重新输入 (y/n/q/s)")
    
    result = {
        "situation": expression.situation,
        "style": expression.style,
        "suitable": suitable,
        "reason": None,
        "evaluator": "manual",
        "evaluated_at": datetime.now().isoformat()
    }
    
    print(f"\n✓ 已记录：{'通过' if suitable else '不通过'}")
    
    return result


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("开始表达方式人工评估")
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
    manual_results = existing_results.copy()
    
    if evaluated_pairs:
        print(f"\n已加载 {len(existing_results)} 条已有评估结果")
        print(f"已评估项目数: {len(evaluated_pairs)}")
    
    print("\n" + "=" * 60)
    print("开始人工评估")
    print("=" * 60)
    print("提示：可以随时输入 'q' 退出，输入 's' 跳过当前项目")
    print("评估结果会自动保存到文件\n")
    
    batch_size = 10
    batch_count = 0
    
    while True:
        # 获取未评估的项目
        expressions = get_unevaluated_expressions(evaluated_pairs, batch_size)
        
        if not expressions:
            print("\n" + "=" * 60)
            print("所有项目都已评估完成！")
            print("=" * 60)
            break
        
        batch_count += 1
        print(f"\n--- 批次 {batch_count}：评估 {len(expressions)} 条项目 ---")
        
        batch_results = []
        for i, expression in enumerate(expressions, 1):
            manual_result = manual_evaluate_expression(expression, i, len(expressions))
            
            if manual_result is None:
                # 用户退出
                print("\n评估已中断")
                if batch_results:
                    # 保存当前批次的结果
                    manual_results.extend(batch_results)
                    save_results(manual_results)
                return
            
            if manual_result == "skip":
                # 跳过当前项目
                continue
            
            batch_results.append(manual_result)
            # 使用 (situation, style) 作为唯一标识
            evaluated_pairs.add((manual_result["situation"], manual_result["style"]))
        
        # 将当前批次结果添加到总结果中
        manual_results.extend(batch_results)
        
        # 保存结果
        save_results(manual_results)
        
        print(f"\n当前批次完成，已评估总数: {len(manual_results)} 条")
        
        # 询问是否继续
        while True:
            continue_input = input("\n是否继续评估下一批？(y/n): ").strip().lower()
            if continue_input in ['y', 'yes', '1', '是', '继续']:
                break
            elif continue_input in ['n', 'no', '0', '否', '退出']:
                print("\n评估结束")
                return
            else:
                print("输入无效，请重新输入 (y/n)")
    
    # 关闭数据库连接
    try:
        db.close()
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.warning(f"关闭数据库连接时出错: {e}")


if __name__ == "__main__":
    main()

