import sys
import os
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from typing import List, Tuple
import numpy as np
from src.common.database.database_model import Expression, ChatStreams

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


# 设置中文字体
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def get_chat_name(chat_id: str) -> str:
    """Get chat name from chat_id by querying ChatStreams table directly"""
    try:
        chat_stream = ChatStreams.get_or_none(ChatStreams.stream_id == chat_id)
        if chat_stream is None:
            return f"未知聊天 ({chat_id})"

        if chat_stream.group_name:
            return f"{chat_stream.group_name} ({chat_id})"
        elif chat_stream.user_nickname:
            return f"{chat_stream.user_nickname}的私聊 ({chat_id})"
        else:
            return f"未知聊天 ({chat_id})"
    except Exception:
        return f"查询失败 ({chat_id})"


def get_expression_data() -> List[Tuple[float, float, str, str]]:
    """获取Expression表中的数据，返回(create_date, count, chat_id, expression_type)的列表"""
    expressions = Expression.select()
    data = []

    for expr in expressions:
        # 如果create_date为空，跳过该记录
        if expr.create_date is None:
            continue

        data.append((expr.create_date, expr.count, expr.chat_id, expr.type))

    return data


def create_scatter_plot(data: List[Tuple[float, float, str, str]], save_path: str = None):
    """创建散点图"""
    if not data:
        print("没有找到有效的表达式数据")
        return

    # 分离数据
    create_dates = [item[0] for item in data]
    counts = [item[1] for item in data]
    _chat_ids = [item[2] for item in data]
    _expression_types = [item[3] for item in data]

    # 转换时间戳为datetime对象
    dates = [datetime.fromtimestamp(ts) for ts in create_dates]

    # 计算时间跨度，自动调整显示格式
    time_span = max(dates) - min(dates)
    if time_span.days > 30:  # 超过30天，按月显示
        date_format = "%Y-%m-%d"
        major_locator = mdates.MonthLocator()
        minor_locator = mdates.DayLocator(interval=7)
    elif time_span.days > 7:  # 超过7天，按天显示
        date_format = "%Y-%m-%d"
        major_locator = mdates.DayLocator(interval=1)
        minor_locator = mdates.HourLocator(interval=12)
    else:  # 7天内，按小时显示
        date_format = "%Y-%m-%d %H:%M"
        major_locator = mdates.HourLocator(interval=6)
        minor_locator = mdates.HourLocator(interval=1)

    # 创建图形
    fig, ax = plt.subplots(figsize=(12, 8))

    # 创建散点图
    scatter = ax.scatter(dates, counts, alpha=0.6, s=30, c=range(len(dates)), cmap="viridis")

    # 设置标签和标题
    ax.set_xlabel("创建日期 (Create Date)", fontsize=12)
    ax.set_ylabel("使用次数 (Count)", fontsize=12)
    ax.set_title("表达式使用次数随时间分布散点图", fontsize=14, fontweight="bold")

    # 设置x轴日期格式 - 根据时间跨度自动调整
    ax.xaxis.set_major_formatter(mdates.DateFormatter(date_format))
    ax.xaxis.set_major_locator(major_locator)
    ax.xaxis.set_minor_locator(minor_locator)
    plt.xticks(rotation=45)

    # 添加网格
    ax.grid(True, alpha=0.3)

    # 添加颜色条
    cbar = plt.colorbar(scatter)
    cbar.set_label("数据点顺序", fontsize=10)

    # 调整布局
    plt.tight_layout()

    # 显示统计信息
    print("\n=== 数据统计 ===")
    print(f"总数据点数量: {len(data)}")
    print(f"时间范围: {min(dates).strftime('%Y-%m-%d %H:%M:%S')} 到 {max(dates).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"使用次数范围: {min(counts):.1f} 到 {max(counts):.1f}")
    print(f"平均使用次数: {np.mean(counts):.2f}")
    print(f"中位数使用次数: {np.median(counts):.2f}")

    # 保存图片
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"\n散点图已保存到: {save_path}")

    # 显示图片
    plt.show()


def create_grouped_scatter_plot(data: List[Tuple[float, float, str, str]], save_path: str = None):
    """创建按聊天分组的散点图"""
    if not data:
        print("没有找到有效的表达式数据")
        return

    # 按chat_id分组
    chat_groups = {}
    for item in data:
        chat_id = item[2]
        if chat_id not in chat_groups:
            chat_groups[chat_id] = []
        chat_groups[chat_id].append(item)

    # 计算时间跨度，自动调整显示格式
    all_dates = [datetime.fromtimestamp(item[0]) for item in data]
    time_span = max(all_dates) - min(all_dates)
    if time_span.days > 30:  # 超过30天，按月显示
        date_format = "%Y-%m-%d"
        major_locator = mdates.MonthLocator()
        minor_locator = mdates.DayLocator(interval=7)
    elif time_span.days > 7:  # 超过7天，按天显示
        date_format = "%Y-%m-%d"
        major_locator = mdates.DayLocator(interval=1)
        minor_locator = mdates.HourLocator(interval=12)
    else:  # 7天内，按小时显示
        date_format = "%Y-%m-%d %H:%M"
        major_locator = mdates.HourLocator(interval=6)
        minor_locator = mdates.HourLocator(interval=1)

    # 创建图形
    fig, ax = plt.subplots(figsize=(14, 10))

    # 为每个聊天分配不同颜色
    colors = plt.cm.Set3(np.linspace(0, 1, len(chat_groups)))

    for i, (chat_id, chat_data) in enumerate(chat_groups.items()):
        create_dates = [item[0] for item in chat_data]
        counts = [item[1] for item in chat_data]
        dates = [datetime.fromtimestamp(ts) for ts in create_dates]

        chat_name = get_chat_name(chat_id)
        # 截断过长的聊天名称
        display_name = chat_name[:20] + "..." if len(chat_name) > 20 else chat_name

        ax.scatter(
            dates,
            counts,
            alpha=0.7,
            s=40,
            c=[colors[i]],
            label=f"{display_name} ({len(chat_data)}个)",
            edgecolors="black",
            linewidth=0.5,
        )

    # 设置标签和标题
    ax.set_xlabel("创建日期 (Create Date)", fontsize=12)
    ax.set_ylabel("使用次数 (Count)", fontsize=12)
    ax.set_title("按聊天分组的表达式使用次数散点图", fontsize=14, fontweight="bold")

    # 设置x轴日期格式 - 根据时间跨度自动调整
    ax.xaxis.set_major_formatter(mdates.DateFormatter(date_format))
    ax.xaxis.set_major_locator(major_locator)
    ax.xaxis.set_minor_locator(minor_locator)
    plt.xticks(rotation=45)

    # 添加图例
    ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=8)

    # 添加网格
    ax.grid(True, alpha=0.3)

    # 调整布局
    plt.tight_layout()

    # 显示统计信息
    print("\n=== 分组统计 ===")
    print(f"总聊天数量: {len(chat_groups)}")
    for chat_id, chat_data in chat_groups.items():
        chat_name = get_chat_name(chat_id)
        counts = [item[1] for item in chat_data]
        print(f"{chat_name}: {len(chat_data)}个表达式, 平均使用次数: {np.mean(counts):.2f}")

    # 保存图片
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"\n分组散点图已保存到: {save_path}")

    # 显示图片
    plt.show()


def create_type_scatter_plot(data: List[Tuple[float, float, str, str]], save_path: str = None):
    """创建按表达式类型分组的散点图"""
    if not data:
        print("没有找到有效的表达式数据")
        return

    # 按type分组
    type_groups = {}
    for item in data:
        expr_type = item[3]
        if expr_type not in type_groups:
            type_groups[expr_type] = []
        type_groups[expr_type].append(item)

    # 计算时间跨度，自动调整显示格式
    all_dates = [datetime.fromtimestamp(item[0]) for item in data]
    time_span = max(all_dates) - min(all_dates)
    if time_span.days > 30:  # 超过30天，按月显示
        date_format = "%Y-%m-%d"
        major_locator = mdates.MonthLocator()
        minor_locator = mdates.DayLocator(interval=7)
    elif time_span.days > 7:  # 超过7天，按天显示
        date_format = "%Y-%m-%d"
        major_locator = mdates.DayLocator(interval=1)
        minor_locator = mdates.HourLocator(interval=12)
    else:  # 7天内，按小时显示
        date_format = "%Y-%m-%d %H:%M"
        major_locator = mdates.HourLocator(interval=6)
        minor_locator = mdates.HourLocator(interval=1)

    # 创建图形
    fig, ax = plt.subplots(figsize=(12, 8))

    # 为每个类型分配不同颜色
    colors = plt.cm.tab10(np.linspace(0, 1, len(type_groups)))

    for i, (expr_type, type_data) in enumerate(type_groups.items()):
        create_dates = [item[0] for item in type_data]
        counts = [item[1] for item in type_data]
        dates = [datetime.fromtimestamp(ts) for ts in create_dates]

        ax.scatter(
            dates,
            counts,
            alpha=0.7,
            s=40,
            c=[colors[i]],
            label=f"{expr_type} ({len(type_data)}个)",
            edgecolors="black",
            linewidth=0.5,
        )

    # 设置标签和标题
    ax.set_xlabel("创建日期 (Create Date)", fontsize=12)
    ax.set_ylabel("使用次数 (Count)", fontsize=12)
    ax.set_title("按表达式类型分组的散点图", fontsize=14, fontweight="bold")

    # 设置x轴日期格式 - 根据时间跨度自动调整
    ax.xaxis.set_major_formatter(mdates.DateFormatter(date_format))
    ax.xaxis.set_major_locator(major_locator)
    ax.xaxis.set_minor_locator(minor_locator)
    plt.xticks(rotation=45)

    # 添加图例
    ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left")

    # 添加网格
    ax.grid(True, alpha=0.3)

    # 调整布局
    plt.tight_layout()

    # 显示统计信息
    print("\n=== 类型统计 ===")
    for expr_type, type_data in type_groups.items():
        counts = [item[1] for item in type_data]
        print(f"{expr_type}: {len(type_data)}个表达式, 平均使用次数: {np.mean(counts):.2f}")

    # 保存图片
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"\n类型散点图已保存到: {save_path}")

    # 显示图片
    plt.show()


def main():
    """主函数"""
    print("开始分析表达式数据...")

    # 获取数据
    data = get_expression_data()

    if not data:
        print("没有找到有效的表达式数据（create_date不为空的数据）")
        return

    print(f"找到 {len(data)} 条有效数据")

    # 创建输出目录
    output_dir = os.path.join(project_root, "data", "temp")
    os.makedirs(output_dir, exist_ok=True)

    # 生成时间戳用于文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1. 创建基础散点图
    print("\n1. 创建基础散点图...")
    create_scatter_plot(data, os.path.join(output_dir, f"expression_scatter_{timestamp}.png"))

    # 2. 创建按聊天分组的散点图
    print("\n2. 创建按聊天分组的散点图...")
    create_grouped_scatter_plot(data, os.path.join(output_dir, f"expression_scatter_by_chat_{timestamp}.png"))

    # 3. 创建按类型分组的散点图
    print("\n3. 创建按类型分组的散点图...")
    create_type_scatter_plot(data, os.path.join(output_dir, f"expression_scatter_by_type_{timestamp}.png"))

    print("\n分析完成！")


if __name__ == "__main__":
    main()
