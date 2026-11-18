"""外接 Agent 的完整执行记录；图片单独保存。"""

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

import asyncio
import base64
import hashlib
import json

from src.common.async_work import run_sync_work
from src.llm_models.payload_content.context_item import ContextItem
from src.llm_models.payload_content.context_snapshot import deserialize_context_item_snapshot, serialize_context_item_snapshot


class AgentHistory:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.records: List[Dict[str, Any]] = []
        self._record_ids: Set[str] = set()
        self._write_lock = asyncio.Lock()

    def load(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "rollout.jsonl"
        self.records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        self._record_ids = {record["id"] for record in self.records}

    def pack(self, items: Sequence[ContextItem]) -> List[Dict[str, Any]]:
        snapshots = [serialize_context_item_snapshot(item) for item in items]
        for raw in snapshots:
            for part in raw.get("parts", []):
                if part["type"] == "image":
                    data = base64.b64decode(part.pop("image_base64"), validate=True)
                    filename = hashlib.sha256(data).hexdigest() + "." + part["image_format"]
                    folder = self.root / "images"
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / filename).write_bytes(data)
                    part["image_file"] = filename
        return snapshots

    def unpack(self, snapshots: Sequence[Dict[str, Any]]) -> List[ContextItem]:
        restored = deepcopy(snapshots)
        for raw in restored:
            for part in raw.get("parts", []):
                if part["type"] == "image":
                    filename = part.pop("image_file")
                    part["image_base64"] = base64.b64encode((self.root / "images" / filename).read_bytes()).decode()
        return [deserialize_context_item_snapshot(raw) for raw in restored]

    async def append(self, record: Dict[str, Any]) -> None:
        # ACP 通知与输入消费可能同时提交；取消必须等落盘完成后才释放提交锁。
        async with self._write_lock:
            await run_sync_work(self._append, record)

    def _append(self, record: Dict[str, Any]) -> None:
        # pending 写入与 rollout 提交之间即使进程中断，也不会重复提交同一回合。
        if record["id"] in self._record_ids:
            return
        with (self.root / "rollout.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
        self.records.append(record)
        self._record_ids.add(record["id"])

    def events(self, after: int = 0) -> List[Dict[str, Any]]:
        return [{"index": index, "id": record["id"], "time": record["time"], "kind": record["kind"],
                 "items": [{**{key: value for key, value in item.items() if key != "meta"},
                            "logical_turn_id": item["meta"]["logical_turn_id"]} for item in record["items"]]}
                for index, record in enumerate(self.records[after:], start=after)]
