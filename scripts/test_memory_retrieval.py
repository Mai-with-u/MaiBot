import argparse
import asyncio
import os
import sys
import time
import json
import importlib
from typing import Optional, Dict, Any
from datetime import datetime

# 强制使用 utf-8，避免控制台编码报错
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 确保能导入 src.*
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.common.logger import initialize_logging, get_logger
from src.common.database.database import db
from src.common.database.database_model import LLMUsage
from src.chat.message_receive.chat_manager import BotChatSession
from maim_message import UserInfo, GroupInfo

logger = get_logger("test_memory_retrieval")


# 使用 importlib 动态导入，避免循环导入问题
def _import_memory_retrieval():
    """使用 importlib 动态导入 memory_retrieval 模块，避免循环导入"""
    try:
        # 先导入 prompt_builder，检查 prompt 是否已经初始化
        from src.chat.utils.prompt_builder import global_prompt_manager

        # 检查 memory_retrieval 相关的 prompt 是否已经注册
        # 如果已经注册，说明模块可能已经通过其他路径初始化过了
        prompt_already_init = "memory_retrieval_question_prompt" in global_prompt_manager._prompts

        module_name = "src.memory_system.memory_retrieval"

        # 如果 prompt 已经初始化，尝试直接使用已加载的模块
        if prompt_already_init and module_name in sys.modules:
            existing_module = sys.modules[module_name]
            if hasattr(existing_module, "init_memory_retrieval_prompt"):
                return (
                    existing_module.init_memory_retrieval_prompt,
                    existing_module._react_agent_solve_question,
                    existing_module._process_single_question,
                )

        # 如果模块已经在 sys.modules 中但部分初始化，先移除它
        if module_name in sys.modules:
            existing_module = sys.modules[module_name]
            if not hasattr(existing_module, "init_memory_retrieval_prompt"):
                # 模块部分初始化，移除它
                logger.warning(f"检测到部分初始化的模块 {module_name}，尝试重新导入")
                del sys.modules[module_name]
                # 清理可能相关的部分初始化模块
                keys_to_remove = []
                for key in sys.modules.keys():
                    if key.startswith("src.memory_system.") and key != "src.memory_system":
                        keys_to_remove.append(key)
                for key in keys_to_remove:
                    try:
                        del sys.modules[key]
                    except KeyError:
                        pass

        # 在导入 memory_retrieval 之前，先确保所有可能触发循环导入的模块都已完全加载
        # 这些模块在导入时可能会触发 memory_retrieval 的导入，所以我们需要先加载它们
        try:
            # 先导入可能触发循环导入的模块，让它们完成初始化
            import src.config.config
            import src.chat.utils.prompt_builder

            # 尝试导入可能触发循环导入的模块（这些模块可能在模块级别导入了 memory_retrieval）
            # 如果它们已经导入，就确保它们完全初始化
            # 尝试导入可能触发循环导入的模块（这些模块可能在模块级别导入了 memory_retrieval）
            # 如果它们已经导入，就确保它们完全初始化
            try:
                import src.chat.replyer.group_generator  # noqa: F401
            except (ImportError, AttributeError):
                pass  # 如果导入失败，继续
            try:
                import src.chat.replyer.private_generator  # noqa: F401
            except (ImportError, AttributeError):
                pass  # 如果导入失败，继续
        except Exception as e:
            logger.warning(f"预加载依赖模块时出现警告: {e}")

        # 现在尝试导入 memory_retrieval
        # 如果此时仍然触发循环导入，说明有其他模块在模块级别导入了 memory_retrieval
        memory_retrieval_module = importlib.import_module(module_name)

        return (
            memory_retrieval_module.init_memory_retrieval_prompt,
            memory_retrieval_module._react_agent_solve_question,
            memory_retrieval_module._process_single_question,
        )
    except (ImportError, AttributeError) as e:
        logger.error(f"导入 memory_retrieval 模块失败: {e}", exc_info=True)
        raise


def create_test_chat_stream(chat_id: str = "test_memory_retrieval") -> ChatStream:
    """创建一个测试用的 ChatStream 对象"""
    user_info = UserInfo(
        platform="test",
        user_id="test_user",
        user_nickname="测试用户",
    )
    group_info = GroupInfo(
        platform="test",
        group_id="test_group",
        group_name="测试群组",
    )
    return ChatStream(
        stream_id=chat_id,
        platform="test",
        user_info=user_info,
        group_info=group_info,
    )


def get_token_usage_since(start_time: float) -> Dict[str, Any]:
    """获取从指定时间开始的token使用情况

    Args:
        start_time: 开始时间戳

    Returns:
        包含token使用统计的字典
    """
    try:
        start_datetime = datetime.fromtimestamp(start_time)

        # 查询从开始时间到现在的所有memory相关的token使用记录
        records = (
            LLMUsage.select()
            .where(
                (LLMUsage.timestamp >= start_datetime)
                & (
                    (LLMUsage.request_type.like("%memory%"))
                    | (LLMUsage.request_type == "memory.question")
                    | (LLMUsage.request_type == "memory.react")
                    | (LLMUsage.request_type == "memory.react.final")
                )
            )
            .order_by(LLMUsage.timestamp.asc())
        )

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        total_cost = 0.0
        request_count = 0
        model_usage = {}  # 按模型统计

        for record in records:
            total_prompt_tokens += record.prompt_tokens or 0
            total_completion_tokens += record.completion_tokens or 0
            total_tokens += record.total_tokens or 0
            total_cost += record.cost or 0.0
            request_count += 1

            # 按模型统计
            model_name = record.model_name or "unknown"
            if model_name not in model_usage:
                model_usage[model_name] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost": 0.0,
                    "request_count": 0,
                }
            model_usage[model_name]["prompt_tokens"] += record.prompt_tokens or 0
            model_usage[model_name]["completion_tokens"] += record.completion_tokens or 0
            model_usage[model_name]["total_tokens"] += record.total_tokens or 0
            model_usage[model_name]["cost"] += record.cost or 0.0
            model_usage[model_name]["request_count"] += 1

        return {
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "request_count": request_count,
            "model_usage": model_usage,
        }
    except Exception as e:
        logger.error(f"获取token使用情况失败: {e}")
        return {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "request_count": 0,
            "model_usage": {},
        }


def format_thinking_steps(thinking_steps: list) -> str:
    """格式化思考步骤为可读字符串"""
    if not thinking_steps:
        return "无思考步骤"

    lines = []
    for step in thinking_steps:
        iteration = step.get("iteration", "?")
        thought = step.get("thought", "")
        actions = step.get("actions", [])
        observations = step.get("observations", [])

        lines.append(f"\n--- 迭代 {iteration} ---")
        if thought:
            lines.append(f"思考: {thought[:200]}...")

        if actions:
            lines.append("行动:")
            for action in actions:
                action_type = action.get("action_type", "unknown")
                action_params = action.get("action_params", {})
                lines.append(f"  - {action_type}: {json.dumps(action_params, ensure_ascii=False)}")

        if observations:
            lines.append("观察:")
            for obs in observations:
                obs_str = str(obs)[:200]
                if len(str(obs)) > 200:
                    obs_str += "..."
                lines.append(f"  - {obs_str}")

    return "\n".join(lines)


async def test_memory_retrieval(
    question: str,
    chat_id: str = "test_memory_retrieval",
    context: str = "",
    max_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    """测试记忆检索功能

    Args:
        question: 要查询的问题
        chat_id: 聊天ID
        context: 上下文信息
        max_iterations: 最大迭代次数

    Returns:
        包含测试结果的字典
    """
    print("\n" + "=" * 80)
    print("[测试] 记忆检索测试")
    print(f"[问题] {question}")
    print("=" * 80)

    # 记录开始时间
    start_time = time.time()

    # 延迟导入并初始化记忆检索prompt（这会自动加载 global_config）
    # 注意：必须在函数内部调用，避免在模块级别触发循环导入
    try:
        init_memory_retrieval_prompt, _react_agent_solve_question, _ = _import_memory_retrieval()

        # 检查 prompt 是否已经初始化，避免重复初始化
        from src.chat.utils.prompt_builder import global_prompt_manager

        if "memory_retrieval_question_prompt" not in global_prompt_manager._prompts:
            init_memory_retrieval_prompt()
        else:
            logger.debug("记忆检索 prompt 已经初始化，跳过重复初始化")
    except Exception as e:
        logger.error(f"初始化记忆检索模块失败: {e}", exc_info=True)
        raise

    # 获取 global_config（此时应该已经加载）
    from src.config.config import global_config

    # 直接调用 _react_agent_solve_question 来获取详细的迭代信息
    if max_iterations is None:
        max_iterations = global_config.memory.max_agent_iterations

    timeout = global_config.memory.agent_timeout_seconds

    print("\n[配置]")
    print(f"  最大迭代次数: {max_iterations}")
    print(f"  超时时间: {timeout}秒")
    print(f"  聊天ID: {chat_id}")

    # 执行检索
    print(f"\n[开始检索] {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")

    found_answer, answer, thinking_steps, is_timeout = await _react_agent_solve_question(
        question=question,
        chat_id=chat_id,
        max_iterations=max_iterations,
        timeout=timeout,
        initial_info="",
    )

    # 记录结束时间
    end_time = time.time()
    elapsed_time = end_time - start_time

    # 获取token使用情况
    token_usage = get_token_usage_since(start_time)

    # 构建结果
    result = {
        "question": question,
        "found_answer": found_answer,
        "answer": answer,
        "is_timeout": is_timeout,
        "elapsed_time": elapsed_time,
        "thinking_steps": thinking_steps,
        "iteration_count": len(thinking_steps),
        "token_usage": token_usage,
    }

    # 输出结果
    print(f"\n[检索完成] {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    print("\n[结果]")
    print(f"  是否找到答案: {'是' if found_answer else '否'}")
    if found_answer and answer:
        print(f"  答案: {answer}")
    else:
        print("  答案: (未找到答案)")
    print(f"  是否超时: {'是' if is_timeout else '否'}")
    print(f"  迭代次数: {len(thinking_steps)}")
    print(f"  总耗时: {elapsed_time:.2f}秒")

    print("\n[Token使用情况]")
    print(f"  总请求数: {token_usage['request_count']}")
    print(f"  总Prompt Tokens: {token_usage['total_prompt_tokens']:,}")
    print(f"  总Completion Tokens: {token_usage['total_completion_tokens']:,}")
    print(f"  总Tokens: {token_usage['total_tokens']:,}")
    print(f"  总成本: ${token_usage['total_cost']:.6f}")

    if token_usage["model_usage"]:
        print("\n[按模型统计]")
        for model_name, usage in token_usage["model_usage"].items():
            print(f"  {model_name}:")
            print(f"    请求数: {usage['request_count']}")
            print(f"    Prompt Tokens: {usage['prompt_tokens']:,}")
            print(f"    Completion Tokens: {usage['completion_tokens']:,}")
            print(f"    总Tokens: {usage['total_tokens']:,}")
            print(f"    成本: ${usage['cost']:.6f}")

    print("\n[迭代详情]")
    print(format_thinking_steps(thinking_steps))

    print("\n" + "=" * 80)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="测试记忆检索功能。可以输入一个问题，脚本会使用记忆检索的逻辑进行检索，并记录迭代信息、时间和token总消耗。"
    )
    parser.add_argument(
        "--chat-id",
        default="test_memory_retrieval",
        help="测试用的聊天ID（默认: test_memory_retrieval）",
    )
    parser.add_argument(
        "--context",
        default="",
        help="上下文信息（可选）",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="将结果保存到JSON文件（可选）",
    )

    args = parser.parse_args()

    # 初始化日志（使用较低的详细程度，避免输出过多日志）
    initialize_logging(verbose=False)

    # 交互式输入问题
    print("\n" + "=" * 80)
    print("记忆检索测试工具")
    print("=" * 80)
    question = input("\n请输入要查询的问题: ").strip()
    if not question:
        print("错误: 问题不能为空")
        return

    # 交互式输入最大迭代次数
    max_iterations_input = input("\n请输入最大迭代次数（直接回车使用配置默认值）: ").strip()
    max_iterations = None
    if max_iterations_input:
        try:
            max_iterations = int(max_iterations_input)
            if max_iterations <= 0:
                print("警告: 迭代次数必须大于0，将使用配置默认值")
                max_iterations = None
        except ValueError:
            print("警告: 无效的迭代次数，将使用配置默认值")
            max_iterations = None

    # 连接数据库
    try:
        db.connect(reuse_if_open=True)
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        print(f"错误: 数据库连接失败: {e}")
        return

    # 运行测试
    try:
        result = asyncio.run(
            test_memory_retrieval(
                question=question,
                chat_id=args.chat_id,
                context=args.context,
                max_iterations=max_iterations,
            )
        )

        # 如果指定了输出文件，保存结果
        if args.output:
            # 将thinking_steps转换为可序列化的格式
            output_result = result.copy()
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output_result, f, ensure_ascii=False, indent=2)
            print(f"\n[结果已保存] {args.output}")

    except KeyboardInterrupt:
        print("\n\n[中断] 用户中断测试")
    except Exception as e:
        logger.error(f"测试失败: {e}", exc_info=True)
        print(f"\n[错误] 测试失败: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
