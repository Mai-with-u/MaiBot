from pathlib import Path
from typing import Any, Dict

import asyncio
import json
import os


def empty_state() -> Dict[str, Any]:
    return {
        "remark": "",
        "user_id": "",
        "token": "",
        "account_id": "",
        "base_url": "https://ilinkai.weixin.qq.com",
        "cursor": "",
        "contexts": {},
        "seen": [],
    }


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = empty_state()
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        def read() -> Dict[str, Any]:
            if not self.path.exists():
                return empty_state()
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("contexts"), dict):
                raise ValueError("微信登录态文件格式无效")
            return {**empty_state(), **value}

        self.state = await asyncio.to_thread(read)

    async def save(self) -> None:
        async with self.lock:
            payload = json.dumps(self.state, ensure_ascii=False)

            def write() -> None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(".tmp")
                with open(temp, "w", encoding="utf-8") as stream:
                    os.chmod(temp, 0o600)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)

            # Finish the atomic write even if the polling task is being stopped.
            task = asyncio.create_task(asyncio.to_thread(write))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def clear(self) -> None:
        self.state = empty_state()
        await self.save()

    async def invalidate(self) -> None:
        """凭证过期保留账号归属和备注，重新扫码时禁止替换成另一个账号。"""
        previous = self.state
        self.state = empty_state()
        for key in ("account_id", "remark", "user_id"):
            self.state[key] = previous[key]
        await self.save()
