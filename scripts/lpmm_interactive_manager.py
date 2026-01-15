import asyncio
import os
import sys

# 尽量统一控制台编码为 utf-8，避免中文输出报错
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 确保项目根目录在 sys.path 中，以便导入 src.*
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

try:
    # 显式从 src.chat.knowledge.lpmm_ops 导入单例对象
    from src.chat.knowledge.lpmm_ops import lpmm_ops
    from src.common.logger import get_logger
    from src.memory_system.retrieval_tools.query_lpmm_knowledge import query_lpmm_knowledge
    from src.chat.knowledge import lpmm_start_up
    from src.config.config import global_config
except ImportError as e:
    print(f"导入失败，请确保在项目根目录下运行脚本: {e}")
    sys.exit(1)

logger = get_logger("lpmm_interactive_manager")

async def interactive_add():
    """交互式导入知识"""
    print("\n" + "=" * 40)
    print("      --- 📥 导入知识 (Add) ---")
    print("=" * 40)
    print("说明：请输入要导入的文本内容。")
    print("      - 支持多段落，段落间请保留空行。")
    print("      - 输入完成后，在新起的一行输入 'EOF' 并回车结束输入。")
    print("-" * 40)
    
    lines = []
    while True:
        try:
            line = input()
            if line.strip().upper() == "EOF":
                break
            lines.append(line)
        except EOFError:
            break
            
    text = "\n".join(lines).strip()
    if not text:
        print("\n[!] 内容为空，操作已取消。")
        return

    print("\n[进度] 正在调用 LPMM 接口进行信息抽取与向量化，请稍候...")
    try:
        # 使用 lpmm_ops.py 中的接口
        result = await lpmm_ops.add_content(text)
        
        if result["status"] == "success":
            print(f"\n[√] 成功：{result['message']}")
            print(f"    实际新增段落数: {result.get('count', 0)}")
        else:
            print(f"\n[×] 失败：{result['message']}")
    except Exception as e:
        print(f"\n[×] 发生异常: {e}")
        logger.error(f"add_content 异常: {e}", exc_info=True)

async def interactive_delete():
    """交互式删除知识"""
    print("\n" + "=" * 40)
    print("      --- 🗑️ 删除知识 (Delete) ---")
    print("=" * 40)
    print("删除模式：")
    print("  1. 关键词模糊匹配（删除包含关键词的所有段落）")
    print("  2. 完整文段匹配（删除完全匹配的段落）")
    print("-" * 40)
    
    mode = input("请选择删除模式 (1/2): ").strip()
    exact_match = False
    
    if mode == "2":
        exact_match = True
        print("\n[完整文段匹配模式]")
        print("说明：请输入要删除的完整文段内容（必须完全一致）。")
        print("      - 支持多行输入，输入完成后在新起的一行输入 'EOF' 并回车。")
        print("-" * 40)
        lines = []
        while True:
            try:
                line = input()
                if line.strip().upper() == "EOF":
                    break
                lines.append(line)
            except EOFError:
                break
        keyword = "\n".join(lines).strip()
    else:
        if mode != "1":
            print("\n[!] 无效选择，默认使用关键词模糊匹配模式。")
        print("\n[关键词模糊匹配模式]")
        keyword = input("请输入匹配关键词: ").strip()
    
    if not keyword:
        print("\n[!] 输入为空，操作已取消。")
        return
        
    print("-" * 40)
    confirm = input(f"危险确认：确定要删除所有匹配 '{keyword[:50]}{'...' if len(keyword) > 50 else ''}' 的知识吗？(y/N): ").strip().lower()
    if confirm != 'y':
        print("\n[!] 已取消删除操作。")
        return

    print("\n[进度] 正在执行删除并更新索引...")
    try:
        # 使用 lpmm_ops.py 中的接口
        result = await lpmm_ops.delete(keyword, exact_match=exact_match)
        
        if result["status"] == "success":
            print(f"\n[√] 成功：{result['message']}")
            print(f"    删除条数: {result.get('deleted_count', 0)}")
        elif result["status"] == "info":
            print(f"\n[i] 提示：{result['message']}")
        else:
            print(f"\n[×] 失败：{result['message']}")
    except Exception as e:
        print(f"\n[×] 发生异常: {e}")
        logger.error(f"delete 异常: {e}", exc_info=True)

async def interactive_clear():
    """交互式清空知识库"""
    print("\n" + "=" * 40)
    print("      --- ⚠️ 清空知识库 (Clear All) ---")
    print("=" * 40)
    print("警告：此操作将删除LPMM知识库中的所有内容！")
    print("      - 所有段落向量")
    print("      - 所有实体向量")
    print("      - 所有关系向量")
    print("      - 整个知识图谱")
    print("      - 此操作不可恢复！")
    print("-" * 40)
    
    # 双重确认
    confirm1 = input("⚠️  第一次确认：确定要清空整个知识库吗？(输入 'YES' 继续): ").strip()
    if confirm1 != "YES":
        print("\n[!] 已取消清空操作。")
        return
    
    print("\n" + "=" * 40)
    confirm2 = input("⚠️  第二次确认：此操作不可恢复，请再次输入 'CLEAR' 确认: ").strip()
    if confirm2 != "CLEAR":
        print("\n[!] 已取消清空操作。")
        return
    
    print("\n[进度] 正在清空知识库...")
    try:
        # 使用 lpmm_ops.py 中的接口
        result = await lpmm_ops.clear_all()
        
        if result["status"] == "success":
            print(f"\n[√] 成功：{result['message']}")
            stats = result.get("stats", {})
            before = stats.get("before", {})
            after = stats.get("after", {})
            print("\n[统计信息]")
            print(f"  清空前: 段落={before.get('paragraphs', 0)}, 实体={before.get('entities', 0)}, "
                  f"关系={before.get('relations', 0)}, KG节点={before.get('kg_nodes', 0)}, KG边={before.get('kg_edges', 0)}")
            print(f"  清空后: 段落={after.get('paragraphs', 0)}, 实体={after.get('entities', 0)}, "
                  f"关系={after.get('relations', 0)}, KG节点={after.get('kg_nodes', 0)}, KG边={after.get('kg_edges', 0)}")
        else:
            print(f"\n[×] 失败：{result['message']}")
    except Exception as e:
        print(f"\n[×] 发生异常: {e}")
        logger.error(f"clear_all 异常: {e}", exc_info=True)

async def interactive_search():
    """交互式查询知识"""
    print("\n" + "=" * 40)
    print("      --- 🔍 查询知识 (Search) ---")
    print("=" * 40)
    print("说明：输入查询问题或关键词，系统会返回相关的知识段落。")
    print("-" * 40)
    
    # 确保 LPMM 已初始化
    if not global_config.lpmm_knowledge.enable:
        print("\n[!] 警告：LPMM 知识库在配置中未启用。")
        return
    
    try:
        lpmm_start_up()
    except Exception as e:
        print(f"\n[!] LPMM 初始化失败: {e}")
        logger.error(f"LPMM 初始化失败: {e}", exc_info=True)
        return
    
    query = input("请输入查询问题或关键词: ").strip()
    
    if not query:
        print("\n[!] 查询内容为空，操作已取消。")
        return
    
    # 询问返回条数
    print("-" * 40)
    limit_str = input("希望返回的相关知识条数（默认3，直接回车使用默认值）: ").strip()
    try:
        limit = int(limit_str) if limit_str else 3
        limit = max(1, min(limit, 20))  # 限制在1-20之间
    except ValueError:
        limit = 3
        print("[!] 输入无效，使用默认值 3。")
    
    print("\n[进度] 正在查询知识库...")
    try:
        result = await query_lpmm_knowledge(query, limit=limit)
        
        print("\n" + "=" * 60)
        print("[查询结果]")
        print("=" * 60)
        print(result)
        print("=" * 60)
    except Exception as e:
        print(f"\n[×] 查询失败: {e}")
        logger.error(f"查询异常: {e}", exc_info=True)

async def main():
    """主循环"""
    while True:
        print("\n" + "╔" + "═" * 38 + "╗")
        print("║      LPMM 知识库交互管理工具        ║")
        print("╠" + "═" * 38 + "╣")
        print("║  1. 导入知识 (Add Content)          ║")
        print("║  2. 删除知识 (Delete Content)       ║")
        print("║  3. 查询知识 (Search Content)       ║")
        print("║  4. 清空知识库 (Clear All) ⚠️        ║")
        print("║  0. 退出 (Exit)                     ║")
        print("╚" + "═" * 38 + "╝")
        
        choice = input("请选择操作编号: ").strip()
        
        if choice == "1":
            await interactive_add()
        elif choice == "2":
            await interactive_delete()
        elif choice == "3":
            await interactive_search()
        elif choice == "4":
            await interactive_clear()
        elif choice in ("0", "q", "Q", "quit", "exit"):
            print("\n已退出工具。")
            break
        else:
            print("\n[!] 无效的选择，请输入 0, 1, 2, 3 或 4。")

if __name__ == "__main__":
    try:
        # 运行主循环
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n[!] 用户中断程序 (Ctrl+C)。")
    except Exception as e:
        print(f"\n[!] 程序运行出错: {e}")
        logger.error(f"Main loop 异常: {e}", exc_info=True)

