"""
MaiSaka - 程序入口
使用方法:
    python saka.py

环境变量 (可通过 .env 文件设置):
    OPENAI_API_KEY   - API 密钥
    OPENAI_BASE_URL  - API 基地址 (可选, 默认 https://api.openai.com/v1)
    OPENAI_MODEL     - 模型名称 (可选, 默认 gpt-4o)
    ENABLE_THINKING  - 是否启用思考模式 (可选, true/false, 不设置则不发送该参数)
"""

from pathlib import Path

import asyncio
import sys

# 添加项目根目录到 Python 路径
_root = Path(__file__).parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.cli.console import console  # noqa: E402
from src.cli.maisaka_cli import BufferCLI  # noqa: E402
from src.prompt.prompt_manager import prompt_manager  # noqa: E402


def main():
    # 加载所有提示词文件
    prompt_manager.load_prompts()

    try:
        asyncio.run(BufferCLI().run())
    except KeyboardInterrupt:
        console.print("\n[muted]程序已终止[/muted]")


if __name__ == "__main__":
    main()
