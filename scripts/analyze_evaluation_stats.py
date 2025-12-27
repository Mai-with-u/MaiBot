"""
评估结果统计脚本

功能：
1. 扫描temp目录下所有JSON文件
2. 分析每个文件的统计信息
3. 输出详细的统计报告
"""

import json
import os
import sys
import glob
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Set, Tuple

# 添加项目根目录到路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from src.common.logger import get_logger

logger = get_logger("evaluation_stats_analyzer")

# 评估结果文件路径
TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")


def parse_datetime(dt_str: str) -> datetime | None:
    """解析ISO格式的日期时间字符串"""
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def analyze_single_file(file_path: str) -> Dict:
    """
    分析单个JSON文件的统计信息
    
    Args:
        file_path: JSON文件路径
        
    Returns:
        统计信息字典
    """
    file_name = os.path.basename(file_path)
    stats = {
        "file_name": file_name,
        "file_path": file_path,
        "file_size": os.path.getsize(file_path),
        "error": None,
        "last_updated": None,
        "total_count": 0,
        "actual_count": 0,
        "suitable_count": 0,
        "unsuitable_count": 0,
        "suitable_rate": 0.0,
        "unique_pairs": 0,
        "evaluators": Counter(),
        "evaluation_dates": [],
        "date_range": None,
        "has_expression_id": False,
        "has_reason": False,
        "reason_count": 0,
    }
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 基本信息
        stats["last_updated"] = data.get("last_updated")
        stats["total_count"] = data.get("total_count", 0)
        
        results = data.get("manual_results", [])
        stats["actual_count"] = len(results)
        
        if not results:
            return stats
        
        # 统计通过/不通过
        suitable_count = sum(1 for r in results if r.get("suitable") is True)
        unsuitable_count = sum(1 for r in results if r.get("suitable") is False)
        stats["suitable_count"] = suitable_count
        stats["unsuitable_count"] = unsuitable_count
        stats["suitable_rate"] = (suitable_count / len(results) * 100) if results else 0.0
        
        # 统计唯一的(situation, style)对
        pairs: Set[Tuple[str, str]] = set()
        for r in results:
            if "situation" in r and "style" in r:
                pairs.add((r["situation"], r["style"]))
        stats["unique_pairs"] = len(pairs)
        
        # 统计评估者
        for r in results:
            evaluator = r.get("evaluator", "unknown")
            stats["evaluators"][evaluator] += 1
        
        # 统计评估时间
        evaluation_dates = []
        for r in results:
            evaluated_at = r.get("evaluated_at")
            if evaluated_at:
                dt = parse_datetime(evaluated_at)
                if dt:
                    evaluation_dates.append(dt)
        
        stats["evaluation_dates"] = evaluation_dates
        if evaluation_dates:
            min_date = min(evaluation_dates)
            max_date = max(evaluation_dates)
            stats["date_range"] = {
                "start": min_date.isoformat(),
                "end": max_date.isoformat(),
                "duration_days": (max_date - min_date).days + 1
            }
        
        # 检查字段存在性
        stats["has_expression_id"] = any("expression_id" in r for r in results)
        stats["has_reason"] = any(r.get("reason") for r in results)
        stats["reason_count"] = sum(1 for r in results if r.get("reason"))
        
    except Exception as e:
        stats["error"] = str(e)
        logger.error(f"分析文件 {file_name} 时出错: {e}")
    
    return stats


def print_file_stats(stats: Dict, index: int = None):
    """打印单个文件的统计信息"""
    prefix = f"[{index}] " if index is not None else ""
    print(f"\n{'=' * 80}")
    print(f"{prefix}文件: {stats['file_name']}")
    print(f"{'=' * 80}")
    
    if stats["error"]:
        print(f"✗ 错误: {stats['error']}")
        return
    
    print(f"文件路径: {stats['file_path']}")
    print(f"文件大小: {stats['file_size']:,} 字节 ({stats['file_size'] / 1024:.2f} KB)")
    
    if stats["last_updated"]:
        print(f"最后更新: {stats['last_updated']}")
    
    print(f"\n【记录统计】")
    print(f"  文件中的 total_count: {stats['total_count']}")
    print(f"  实际记录数: {stats['actual_count']}")
    
    if stats['total_count'] != stats['actual_count']:
        diff = stats['total_count'] - stats['actual_count']
        print(f"  ⚠️  数量不一致，差值: {diff:+d}")
    
    print(f"\n【评估结果统计】")
    print(f"  通过 (suitable=True): {stats['suitable_count']} 条 ({stats['suitable_rate']:.2f}%)")
    print(f"  不通过 (suitable=False): {stats['unsuitable_count']} 条 ({100 - stats['suitable_rate']:.2f}%)")
    
    print(f"\n【唯一性统计】")
    print(f"  唯一 (situation, style) 对: {stats['unique_pairs']} 条")
    if stats['actual_count'] > 0:
        duplicate_count = stats['actual_count'] - stats['unique_pairs']
        duplicate_rate = (duplicate_count / stats['actual_count'] * 100) if stats['actual_count'] > 0 else 0
        print(f"  重复记录: {duplicate_count} 条 ({duplicate_rate:.2f}%)")
    
    print(f"\n【评估者统计】")
    if stats['evaluators']:
        for evaluator, count in stats['evaluators'].most_common():
            rate = (count / stats['actual_count'] * 100) if stats['actual_count'] > 0 else 0
            print(f"  {evaluator}: {count} 条 ({rate:.2f}%)")
    else:
        print("  无评估者信息")
    
    print(f"\n【时间统计】")
    if stats['date_range']:
        print(f"  最早评估时间: {stats['date_range']['start']}")
        print(f"  最晚评估时间: {stats['date_range']['end']}")
        print(f"  评估时间跨度: {stats['date_range']['duration_days']} 天")
    else:
        print("  无时间信息")
    
    print(f"\n【字段统计】")
    print(f"  包含 expression_id: {'是' if stats['has_expression_id'] else '否'}")
    print(f"  包含 reason: {'是' if stats['has_reason'] else '否'}")
    if stats['has_reason']:
        rate = (stats['reason_count'] / stats['actual_count'] * 100) if stats['actual_count'] > 0 else 0
        print(f"  有理由的记录: {stats['reason_count']} 条 ({rate:.2f}%)")


def print_summary(all_stats: List[Dict]):
    """打印汇总统计信息"""
    print(f"\n{'=' * 80}")
    print("汇总统计")
    print(f"{'=' * 80}")
    
    total_files = len(all_stats)
    valid_files = [s for s in all_stats if not s.get("error")]
    error_files = [s for s in all_stats if s.get("error")]
    
    print(f"\n【文件统计】")
    print(f"  总文件数: {total_files}")
    print(f"  成功解析: {len(valid_files)}")
    print(f"  解析失败: {len(error_files)}")
    
    if error_files:
        print(f"\n  失败文件列表:")
        for stats in error_files:
            print(f"    - {stats['file_name']}: {stats['error']}")
    
    if not valid_files:
        print("\n没有成功解析的文件")
        return
    
    # 汇总记录统计
    total_records = sum(s['actual_count'] for s in valid_files)
    total_suitable = sum(s['suitable_count'] for s in valid_files)
    total_unsuitable = sum(s['unsuitable_count'] for s in valid_files)
    total_unique_pairs = set()
    
    # 收集所有唯一的(situation, style)对
    for stats in valid_files:
        try:
            with open(stats['file_path'], "r", encoding="utf-8") as f:
                data = json.load(f)
                results = data.get("manual_results", [])
                for r in results:
                    if "situation" in r and "style" in r:
                        total_unique_pairs.add((r["situation"], r["style"]))
        except Exception:
            pass
    
    print(f"\n【记录汇总】")
    print(f"  总记录数: {total_records:,} 条")
    print(f"  通过: {total_suitable:,} 条 ({total_suitable / total_records * 100:.2f}%)" if total_records > 0 else "  通过: 0 条")
    print(f"  不通过: {total_unsuitable:,} 条 ({total_unsuitable / total_records * 100:.2f}%)" if total_records > 0 else "  不通过: 0 条")
    print(f"  唯一 (situation, style) 对: {len(total_unique_pairs):,} 条")
    
    if total_records > 0:
        duplicate_count = total_records - len(total_unique_pairs)
        duplicate_rate = (duplicate_count / total_records * 100) if total_records > 0 else 0
        print(f"  重复记录: {duplicate_count:,} 条 ({duplicate_rate:.2f}%)")
    
    # 汇总评估者统计
    all_evaluators = Counter()
    for stats in valid_files:
        all_evaluators.update(stats['evaluators'])
    
    print(f"\n【评估者汇总】")
    if all_evaluators:
        for evaluator, count in all_evaluators.most_common():
            rate = (count / total_records * 100) if total_records > 0 else 0
            print(f"  {evaluator}: {count:,} 条 ({rate:.2f}%)")
    else:
        print("  无评估者信息")
    
    # 汇总时间范围
    all_dates = []
    for stats in valid_files:
        all_dates.extend(stats['evaluation_dates'])
    
    if all_dates:
        min_date = min(all_dates)
        max_date = max(all_dates)
        print(f"\n【时间汇总】")
        print(f"  最早评估时间: {min_date.isoformat()}")
        print(f"  最晚评估时间: {max_date.isoformat()}")
        print(f"  总时间跨度: {(max_date - min_date).days + 1} 天")
    
    # 文件大小汇总
    total_size = sum(s['file_size'] for s in valid_files)
    avg_size = total_size / len(valid_files) if valid_files else 0
    print(f"\n【文件大小汇总】")
    print(f"  总大小: {total_size:,} 字节 ({total_size / 1024 / 1024:.2f} MB)")
    print(f"  平均大小: {avg_size:,.0f} 字节 ({avg_size / 1024:.2f} KB)")


def main():
    """主函数"""
    logger.info("=" * 80)
    logger.info("开始分析评估结果统计信息")
    logger.info("=" * 80)
    
    if not os.path.exists(TEMP_DIR):
        print(f"\n✗ 错误：未找到temp目录: {TEMP_DIR}")
        logger.error(f"未找到temp目录: {TEMP_DIR}")
        return
    
    # 查找所有JSON文件
    json_files = glob.glob(os.path.join(TEMP_DIR, "*.json"))
    
    if not json_files:
        print(f"\n✗ 错误：temp目录下未找到JSON文件: {TEMP_DIR}")
        logger.error(f"temp目录下未找到JSON文件: {TEMP_DIR}")
        return
    
    json_files.sort()  # 按文件名排序
    
    print(f"\n找到 {len(json_files)} 个JSON文件")
    print("=" * 80)
    
    # 分析每个文件
    all_stats = []
    for i, json_file in enumerate(json_files, 1):
        stats = analyze_single_file(json_file)
        all_stats.append(stats)
        print_file_stats(stats, index=i)
    
    # 打印汇总统计
    print_summary(all_stats)
    
    print(f"\n{'=' * 80}")
    print("分析完成")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()


