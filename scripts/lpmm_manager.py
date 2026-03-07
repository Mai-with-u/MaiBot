import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List

# 尽量统一控制台编码为 utf-8，避免中文输出报错
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 确保能导入 src.* 以及同目录脚本
CURRENT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.common.logger import get_logger  # type: ignore  # noqa: E402
from src.config.config import global_config, model_config  # type: ignore  # noqa: E402

# 引入各功能脚本的入口函数
from import_openie import main as import_openie_main  # type: ignore  # noqa: E402
from info_extraction import main as info_extraction_main  # type: ignore  # noqa: E402
from delete_lpmm_items import main as delete_lpmm_items_main  # type: ignore  # noqa: E402
from inspect_lpmm_batch import main as inspect_lpmm_batch_main  # type: ignore  # noqa: E402
from inspect_lpmm_global import main as inspect_lpmm_global_main  # type: ignore  # noqa: E402
from refresh_lpmm_knowledge import main as refresh_lpmm_knowledge_main  # type: ignore  # noqa: E402
from test_lpmm_retrieval import main as test_lpmm_retrieval_main  # type: ignore  # noqa: E402
from raw_data_preprocessor import load_raw_data  # type: ignore  # noqa: E402


logger = get_logger("lpmm_manager")


ACTION_INFO = {
    "prepare_raw": "预处理 data/lpmm_raw_data/*.txt，按空行切分为段落并做去重统计",
    "info_extract": "原始 txt -> OpenIE 信息抽取（调用 info_extraction.py）",
    "import_openie": "导入 OpenIE 批次到向量库与知识图（调用 import_openie.py）",
    "delete": "删除/回滚知识（调用 delete_lpmm_items.py）",
    "batch_inspect": "检查指定 OpenIE 批次在当前库中的存在情况（调用 inspect_lpmm_batch.py）",
    "global_inspect": "查看当前整库向量与 KG 状态（调用 inspect_lpmm_global.py）",
    "refresh": "刷新 LPMM 磁盘数据到内存（调用 refresh_lpmm_knowledge.py）",
    "test": "运行 LPMM 检索效果回归测试（调用 test_lpmm_retrieval.py）",
    "embedding_helper": "嵌入模型迁移辅助：查看当前嵌入模型/维度并归档 embedding_model_test.json",
    "full_import": "一键执行：信息抽取 -> 导入 OpenIE -> 刷新",
}


def _with_overridden_argv(extra_args: List[str], target_main) -> None:
    """在不修改子脚本的前提下，临时覆盖 sys.argv 以透传参数。"""
    old_argv = list(sys.argv)
    try:
        # 第 0 个元素为“程序名”，后续元素为实际参数
        # 这里不再插入类似 delete_lpmm_items.py 的占位，避免被 argparse 误识别为位置参数
        sys.argv = [old_argv[0]] + extra_args
        target_main()
    finally:
        sys.argv = old_argv


def _check_before_info_extract(non_interactive: bool = False) -> bool:
    """信息抽取前的轻量级检查。"""
    raw_dir = Path(PROJECT_ROOT) / "data" / "lpmm_raw_data"
    txt_files = list(raw_dir.glob("*.txt"))
    if not txt_files:
        msg = f"[WARN] 未在 {raw_dir} 下找到任何 .txt 原始语料文件，info_extraction 可能立即退出或无数据可处理。"
        print(msg)
        if non_interactive:
            logger.error("非交互模式下要求原始语料目录中已存在可用的 .txt 文件，请先准备好数据再重试。")
            return False
        cont = input("仍然继续执行信息提取吗？(y/n): ").strip().lower()
        return cont == "y"
    return True


def _check_before_import_openie(non_interactive: bool = False) -> bool:
    """导入 OpenIE 前的轻量级检查。"""
    openie_dir = Path(PROJECT_ROOT) / "data" / "openie"
    json_files = list(openie_dir.glob("*.json"))
    if not json_files:
        msg = f"[WARN] 未在 {openie_dir} 下找到任何 OpenIE JSON 文件，import_openie 可能会因为找不到批次而失败。"
        print(msg)
        if non_interactive:
            logger.error("非交互模式下要求 data/openie 目录中已存在可用的 OpenIE JSON 文件，请先执行信息提取脚本。")
            return False
        cont = input("仍然继续执行导入吗？(y/n): ").strip().lower()
        return cont == "y"
    return True


def _warn_if_lpmm_disabled() -> None:
    """在部分操作前提醒 lpmm_knowledge.enable 状态。"""
    try:
        if not getattr(global_config.lpmm_knowledge, "enable", False):
            print("[WARN] 当前配置 lpmm_knowledge.enable = false，刷新或检索测试可能无法在聊天侧真正启用 LPMM。")
    except Exception:
        # 配置异常时不阻断主流程，仅忽略提示
        pass


def run_action(action: str, extra_args: Optional[List[str]] = None) -> None:
    """根据动作名称调度到对应脚本。

    这里不重复解析子参数，而是直接调用各脚本的 main()，
    让子脚本保留原有的交互/参数行为。
    """
    logger.info("开始执行操作: %s", action)

    extra_args = extra_args or []

    try:
        if action == "prepare_raw":
            logger.info("开始预处理原始语料 (data/lpmm_raw_data/*.txt)...")
            sha_list, raw_data = load_raw_data()
            print(f"\n[PREPARE_RAW] 完成原始语料预处理：共 {len(raw_data)} 条段落，去重后哈希数 {len(sha_list)}。")
        elif action == "info_extract":
            if not _check_before_info_extract("--non-interactive" in extra_args):
                print("已根据用户选择，取消执行信息提取。")
                return
            _with_overridden_argv(extra_args, info_extraction_main)
        elif action == "import_openie":
            if not _check_before_import_openie("--non-interactive" in extra_args):
                print("已根据用户选择，取消执行导入。")
                return
            _with_overridden_argv(extra_args, import_openie_main)
        elif action == "delete":
            _with_overridden_argv(extra_args, delete_lpmm_items_main)
        elif action == "batch_inspect":
            _with_overridden_argv(extra_args, inspect_lpmm_batch_main)
        elif action == "global_inspect":
            _with_overridden_argv(extra_args, inspect_lpmm_global_main)
        elif action == "refresh":
            _warn_if_lpmm_disabled()
            _with_overridden_argv(extra_args, refresh_lpmm_knowledge_main)
        elif action == "test":
            _warn_if_lpmm_disabled()
            _with_overridden_argv(extra_args, test_lpmm_retrieval_main)
        elif action == "embedding_helper":
            # 嵌入模型迁移辅助：查看当前嵌入模型/维度并归档 embedding_model_test.json
            _run_embedding_helper()
        elif action == "full_import":
            # 一键流水线：预处理原始语料 -> 信息抽取 -> 导入 -> 刷新
            logger.info("开始 full_import：预处理原始语料 -> 信息抽取 -> 导入 -> 刷新")
            sha_list, raw_data = load_raw_data()
            print(f"\n[FULL_IMPORT] 原始语料预处理完成：共 {len(raw_data)} 条段落，去重后哈希数 {len(sha_list)}。")
            non_interactive = "--non-interactive" in extra_args
            if not _check_before_info_extract(non_interactive):
                print("已根据用户选择，取消 full_import（信息提取阶段被取消）。")
                return
            # 使用与单步 info_extract 相同的参数透传机制，确保 --non-interactive 等生效
            _with_overridden_argv(extra_args, info_extraction_main)
            if not _check_before_import_openie(non_interactive):
                print("已根据用户选择，取消 full_import（导入阶段被取消）。")
                return
            _with_overridden_argv(extra_args, import_openie_main)
            _warn_if_lpmm_disabled()
            _with_overridden_argv(extra_args, refresh_lpmm_knowledge_main)
        else:
            logger.error("未知操作: %s", action)
    except KeyboardInterrupt:
        logger.info("用户中断当前操作（Ctrl+C）")
    except SystemExit:
        # 子脚本里大量使用 sys.exit，直接透传即可
        raise
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.error("执行操作 %s 时发生未捕获异常: %s", action, exc)
        raise


def print_menu() -> None:
    print("\n===== LPMM 管理菜单 =====")
    for idx, key in enumerate(
        [
            "prepare_raw",
            "info_extract",
            "import_openie",
            "delete",
            "batch_inspect",
            "global_inspect",
            "refresh",
            "test",
            "embedding_helper",
            "full_import",
        ],
        start=1,
    ):
        desc = ACTION_INFO.get(key, "")
        print(f"{idx}. {key:14s} - {desc}")
    print("0. 退出")
    print("=========================")


def interactive_loop() -> None:
    """交互式选择模式。"""
    key_order = [
        "prepare_raw",
        "info_extract",
        "import_openie",
        "delete",
        "batch_inspect",
        "global_inspect",
        "refresh",
        "test",
        "embedding_helper",
        "full_import",
    ]

    while True:
        print_menu()
        choice = input("请输入选项编号（0-10）：").strip()

        if choice in ("0", "q", "Q", "quit", "exit"):
            print("已退出 LPMM 管理器。")
            return

        try:
            idx = int(choice)
        except ValueError:
            print("输入无效，请输入 0-10 之间的数字。")
            continue

        if not (1 <= idx <= len(key_order)):
            print("输入编号超出范围，请重新输入。")
            continue

        action = key_order[idx - 1]
        print(f"\n你选择了: {action} - {ACTION_INFO.get(action, '')}")
        confirm = input("确认执行该操作？(y/n): ").strip().lower()
        if confirm != "y":
            print("已取消当前操作。\n")
            continue

        # 通过交互式问题，尽量帮用户补全对应脚本的常用参数
        extra_args: List[str] = []
        if action == "delete":
            extra_args = _interactive_build_delete_args()
        elif action == "batch_inspect":
            extra_args = _interactive_build_batch_inspect_args()
        elif action == "test":
            extra_args = _interactive_build_test_args()
        else:
            extra_args = []

        run_action(action, extra_args=extra_args)
        print("\n当前操作已结束，回到主菜单。\n")


def _interactive_choose_openie_file(prompt: str) -> Optional[str]:
    """在 data/openie 下列出可选 JSON 文件，并返回用户选择的路径。"""
    openie_dir = Path(PROJECT_ROOT) / "data" / "openie"
    files = sorted(openie_dir.glob("*.json"))
    if not files:
        print(f"[WARN] 在 {openie_dir} 下没有找到任何 OpenIE JSON 文件。")
        return input(prompt).strip() or None

    print("\n可选的 OpenIE 批次文件：")
    for i, f in enumerate(files, start=1):
        print(f"{i}. {f.name}")
    print("0. 手动输入完整路径")

    while True:
        choice = input("请选择文件编号：").strip()
        if choice == "0":
            manual = input(prompt).strip()
            return manual or None
        try:
            idx = int(choice)
        except ValueError:
            print("请输入合法的编号。")
            continue
        if 1 <= idx <= len(files):
            return str(files[idx - 1])
        print("编号超出范围，请重试。")


def _interactive_build_delete_args() -> List[str]:
    """为 delete_lpmm_items 构造常见参数，减少二次交互。"""
    print(
        "\n[DELETE] 请选择删除方式：\n"
        "1. 按哈希文件删除 (--hash-file)\n"
        "2. 按 OpenIE 批次删除 (--openie-file)\n"
        "3. 按原始语料文件 + 段落索引删除 (--raw-file + --raw-index)\n"
        "4. 按关键字搜索现有段落 (--search-text)\n"
        "回车跳过，由子脚本自行交互。"
    )
    mode = input("输入选项编号（1-4，或回车跳过）：").strip()
    args: List[str] = []

    if mode == "1":
        path = input("请输入哈希文件路径（每行一个 hash）：").strip()
        if path:
            args += ["--hash-file", path]
    elif mode == "2":
        path = _interactive_choose_openie_file("请输入 OpenIE JSON 文件路径：")
        if path:
            args += ["--openie-file", path]
    elif mode == "3":
        raw_file = input("请输入原始语料 txt 文件路径：").strip()
        raw_index = input("请输入要删除的段落索引（如 1,3）：").strip()
        if raw_file and raw_index:
            args += ["--raw-file", raw_file, "--raw-index", raw_index]
    elif mode == "4":
        text = input("请输入用于搜索的关键字（出现在段落原文中）：").strip()
        if text:
            args += ["--search-text", text]
    else:
        # 留空则完全交给子脚本交互
        return []

    # 进一步询问与安全相关的布尔选项
    print(
        "\n[DELETE] 接下来是一些安全相关选项的说明：\n"
        "- 删除实体向量/节点：会一并清理与这些段落关联的实体节点及其向量；\n"
        "- 删除关系向量：在上面的基础上，额外清理关系向量（一般与删除实体一同使用）；\n"
        "- 删除孤立实体节点：删除后若实体不再连接任何段落，将其从图中移除，避免残留孤点；\n"
        "- dry-run：只预览将要删除的内容，不真正修改任何数据；\n"
        "- 跳过交互确认(--yes)：直接执行删除操作，适合脚本化或已充分确认的场景；\n"
        "- 单次最大删除节点数上限：防止一次性删除规模过大，起到误操作保护作用；\n"
        "- 一般情况下建议同时删除实体向量/节点/关系向量/节点，以确保知识图谱的完整性。"
    )

    # 快速选项：按推荐方式清理所有相关实体/关系
    quick_all = (
        input("是否使用推荐策略：同时删除关联的实体向量/节点、关系向量，并清理孤立实体？(Y/n): ").strip().lower()
    )
    if quick_all in ("", "y", "yes"):
        args.extend(["--delete-entities", "--delete-relations", "--remove-orphan-entities"])
    else:
        # 仅当未使用快速方案时，再逐项询问
        if input("是否同时删除实体向量/节点？(y/N): ").strip().lower() == "y":
            args.append("--delete-entities")
            if input("是否同时删除关系向量？(y/N): ").strip().lower() == "y":
                args.append("--delete-relations")

        if input("是否删除孤立实体节点？(y/N): ").strip().lower() == "y":
            args.append("--remove-orphan-entities")

    if input("是否以 dry-run 预览而不真正删除？(y/N): ").strip().lower() == "y":
        args.append("--dry-run")
    else:
        if input("是否跳过交互确认直接删除？(默认否，请谨慎) (y/N): ").strip().lower() == "y":
            args.append("--yes")

    max_nodes = input("单次最大删除节点数上限（回车使用默认 2000）：").strip()
    if max_nodes:
        args += ["--max-delete-nodes", max_nodes]

    return args


def _interactive_build_batch_inspect_args() -> List[str]:
    """为 inspect_lpmm_batch 构造 --openie-file 参数。"""
    path = _interactive_choose_openie_file("请输入要检查的 OpenIE JSON 文件路径（回车跳过，由子脚本自行交互）：")
    if not path:
        return []
    return ["--openie-file", path]


def _interactive_build_test_args() -> List[str]:
    """为 test_lpmm_retrieval 构造自定义测试用例参数。"""
    print("\n[TEST] 你可以：\n- 直接回车使用内置的默认测试用例；\n- 或者输入一条自定义问题，并指定期望命中的关键字。")
    query = input("请输入自定义测试问题（回车则使用默认用例）：").strip()
    if not query:
        return []

    expect = input("请输入期望命中的关键字（可选，多项用逗号分隔）：").strip()
    args: List[str] = ["--query", query]
    if expect:
        for kw in expect.split(","):
            kw = kw.strip()
            if kw:
                args.extend(["--expect-keyword", kw])
    return args


def _run_embedding_helper() -> None:
    """嵌入模型迁移辅助：展示当前配置，并安全归档 embedding_model_test.json。"""
    from src.chat.knowledge.embedding_store import EMBEDDING_TEST_FILE  # type: ignore

    # 1. 读取当前配置中的嵌入维度与模型信息
    current_dim = getattr(getattr(global_config, "lpmm_knowledge", None), "embedding_dimension", None)
    embed_task = getattr(model_config.model_task_config, "embedding", None)
    model_ids: List[str] = []
    if embed_task is not None:
        model_ids = getattr(embed_task, "model_list", []) or []
    primary_model = model_ids[0] if model_ids else "unknown"
    safe_model_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", primary_model) or "unknown"

    print("\n===== 嵌入模型迁移辅助 (embedding_helper) =====")
    print(f"- 当前嵌入模型标识（model_task_config.embedding.model_list[0]）: {primary_model}")
    print(f"- 当前配置中的嵌入维度 (lpmm_knowledge.embedding_dimension): {current_dim}")
    print(f"- 测试文件路径: {EMBEDDING_TEST_FILE}")

    new_dim = input("\n如果你计划更换嵌入模型，请在此输入“新的嵌入维度”（仅用于记录与提示，回车则跳过）：").strip()
    if new_dim and not new_dim.isdigit():
        print("输入的维度不是纯数字，已取消操作。")
        return

    print(
        "\n[重要提示]\n"
        "- 修改嵌入模型或维度会导致当前磁盘中的旧知识库（data/embedding 下的向量）与新模型不兼容；\n"
        "- 这通常意味着你需要清空旧的向量/图数据，并重新执行 LPMM 导入流水线；\n"
        "- 请仅在你**确定要切换嵌入模型/维度**时再继续。\n"
    )
    confirm = input("是否已充分评估风险，并准备切换嵌入模型/维度？(y/N): ").strip().lower()
    if confirm != "y":
        print("已根据你的选择取消嵌入模型迁移辅助操作。")
        return

    print(
        "\n接下来请手动完成以下操作（脚本不会自动修改配置或删除知识库）：\n"
        f"1. 在配置文件中，将 lpmm_knowledge.embedding_dimension 从 {current_dim} 修改为你计划使用的新维度"
        + (f"（例如 {new_dim}）" if new_dim else "")  # 仅作为示例
        + "；\n"
        "2. 根据需要，清空 data/embedding 与相关 KG 数据（data/rag 等），然后重新执行导入流水线；\n"
        "3. 本脚本将帮助你归档当前的 embedding_model_test.json，避免旧测试文件干扰新模型的校验。\n"
    )

    # 2. 归档 embedding_model_test.json
    test_path = Path(EMBEDDING_TEST_FILE)
    if not test_path.exists():
        print(f"\n[INFO] 未在 {test_path} 发现 embedding_model_test.json，无需归档。")
        return

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_name = f"embedding_model_test-{safe_model_name}-{ts}.json"
    archive_path = test_path.with_name(archive_name)

    # 若不巧重名，简单追加后缀避免覆盖
    suffix_id = 1
    while archive_path.exists():
        archive_name = f"embedding_model_test-{safe_model_name}-{ts}-{suffix_id}.json"
        archive_path = test_path.with_name(archive_name)
        suffix_id += 1

    try:
        test_path.rename(archive_path)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.error("归档 embedding_model_test.json 失败: %s", exc)
        print("[ERROR] 归档 embedding_model_test.json 失败，请检查文件权限与路径。错误详情已写入日志。")
        return

    print(
        f"\n[OK] 已将 {test_path.name} 重命名为 {archive_path.name}。\n"
        f"- 归档位置: {archive_path}\n"
        "- 之后再次运行涉及嵌入模型的一致性校验时，将会基于当前配置与新模型生成新的测试文件。\n"
        "- 在完成配置修改与知识库重导入前，请不要手动再创建名为 embedding_model_test.json 的文件。"
    )


def parse_args(argv: Optional[list[str]] = None) -> tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "LPMM 管理脚本：集中入口管理 LPMM 的导入 / 删除 / 自检 / 刷新 / 测试等功能。\n"
            "可以通过 --interactive 进入菜单模式，也可以使用 --action 直接执行单个操作。"
        )
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="进入交互式菜单模式（推荐给手动运维使用）",
    )
    parser.add_argument(
        "-a",
        "--action",
        choices=list(ACTION_INFO.keys()),
        help="直接执行指定操作（非交互模式）",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "启用非交互模式：lpmm_manager 自身不会再通过 input() 询问是否继续前置检查；"
            "并会将 --non-interactive 透传给子脚本，以避免子脚本中的交互式确认。"
        ),
    )
    # 允许在管理脚本之后继续跟随子脚本参数，例如:
    # python lpmm_manager.py -a delete -- --hash-file xxx --yes
    args, unknown = parser.parse_known_args(argv)
    return args, unknown


def main(argv: Optional[list[str]] = None) -> None:
    args, extra_args = parse_args(argv)

    # 如果指定了 non-interactive，则不能进入交互式菜单
    if args.non_interactive and args.interactive:
        logger.error("不能同时指定 --interactive 与 --non-interactive，请二选一。")
        sys.exit(1)

    # 没有指定 action 或显式要求交互 -> 进入菜单
    if args.interactive or not args.action:
        interactive_loop()
        return

    # 在非交互模式下，将 --non-interactive 透传给子脚本，避免其内部出现 input() 交互
    if args.non_interactive:
        extra_args = ["--non-interactive"] + extra_args

    # 非交互模式：直接执行指定操作
    run_action(args.action, extra_args=extra_args)


if __name__ == "__main__":
    main()
