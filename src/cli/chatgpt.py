"""Explicit local OAuth entry points; never start login as a side effect of inference."""

from pathlib import Path

import json

from src.common.chatgpt_auth import ChatGPTAccountStore, ChatGPTAuthError, USAGE_URL


def run_chatgpt_command(action: str, directory: Path, account: str) -> int:
    store = ChatGPTAccountStore(directory / "data" / "chatgpt")
    try:
        if action == "login":
            print("Continue with ChatGPT — 使用 ChatGPT 套餐为 MaiBot 提供模型能力。")
            print("将打开 OpenAI 官方授权页，并在此实例保存独立的 OAuth 凭据（macOS/Linux，0600）。")
            print("符合条件的账号及模型以官方页面为准；额度/credits 设置由你在 ChatGPT 中管理。")
            print(f"Manage usage: {USAGE_URL}")
            if input("继续登录并保存授权？[y/N] ").strip().lower() != "y":
                print("已取消；没有启动登录。")
                return 1
            store.login(account)
            print(f"账号标签 {account} 已连接。运行 maibot chatgpt models 查看此账号可用模型。")
        elif action == "accounts":
            print(json.dumps(store.accounts(), ensure_ascii=False, indent=2))
        elif action == "models":
            print(json.dumps(store.models(account), ensure_ascii=False, indent=2))
        elif action == "logout":
            confirmed = store.logout(account)
            print("已清除本地令牌并保留注册标签。")
            if not confirmed:
                print(f"远端撤销未确认；请在 ChatGPT 设置断开 MaiBot：{USAGE_URL}")
                return 1
        return 0
    except (ChatGPTAuthError, OSError) as error:
        # OSError filenames may identify a local path; never print arbitrary network errors.
        print(str(error) if isinstance(error, ChatGPTAuthError) else "ChatGPT 本地存储或回调端口不可用。")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("登录已取消。")
        return 1
