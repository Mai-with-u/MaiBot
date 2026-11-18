"""窗口线程通过主程序操作事项，不持有第二份可写存储。"""

from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

import threading


class TodoProxy:
    def __init__(self, emit: Callable[[Dict[str, Any]], None]) -> None:
        self._emit = emit
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}

    def _request(self, action: str, **payload: Any) -> Dict[str, Any]:
        request_id = uuid4().hex
        event = threading.Event()
        response: Dict[str, Any] = {"event": event}
        with self._lock:
            self._pending[request_id] = response
        try:
            self._emit({"type": "todo_request", "request_id": request_id,
                        "command": {"action": action, **payload}})
            if not event.wait(30):
                raise TimeoutError("主程序事项请求超时，请查询列表确认结果")
            if response.get("error"):
                raise RuntimeError(response["error"])
            return response["result"]
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def receive(self, command: Dict[str, Any]) -> None:
        with self._lock:
            response = self._pending.get(command["request_id"])
            if response is not None:
                response.update(result=command.get("result"), error=command.get("error"))
                response["event"].set()

    def list(self, include_completed: bool = False) -> List[Dict[str, Any]]:
        return self._request("list", include_completed=include_completed)["items"]

    def image(self, image_id: str) -> str:
        return self._request("image", image_id=image_id)["data_url"]

    def add(self, text: str, **options: Any) -> Dict[str, Any]:
        return self._request("add", text=text, options=options)["item"]

    def update(self, todo_id: str, text: Optional[str] = None, **options: Any) -> Dict[str, Any]:
        return self._request("update", todo_id=todo_id, text=text, options=options)["item"]

    def done(self, todo_id: str) -> None:
        self._request("complete", todo_id=todo_id)

    def delete(self, todo_id: str) -> None:
        self._request("delete", todo_id=todo_id)
