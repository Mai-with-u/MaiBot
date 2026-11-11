"""服务器待办命令，始终由工作线程执行文件读写。"""

from typing import Any, Dict

from .todos import TodoStore


def execute_todo(store: TodoStore, command: Dict[str, Any]) -> Dict[str, Any]:
    action = command["action"]
    if action == "list":
        return {"items": store.list(include_completed=command.get("include_completed", False))}
    if action == "image":
        return {"data_url": store.image(command["image_id"])}
    if action == "add":
        return {"item": store.add(command["text"], **command.get("options", {}))}
    if action == "update":
        return {"item": store.update(command["todo_id"], command.get("text"), **command.get("options", {}))}
    if action == "complete":
        store.done(command["todo_id"])
        return {"completed": command["todo_id"]}
    if action == "delete":
        store.delete(command["todo_id"])
        return {"deleted": command["todo_id"]}
    if action == "claim_due":
        return {"items": store.claim_due(kind=command.get("kind"))}
    if action == "claim_condition":
        return {"item": store.claim_condition(command["todo_id"], command["revision"], command["evidence"])}
    if action == "finish":
        store.finish(command["todo_id"], command["revision"], command.get("error", ""))
        return {}
    raise ValueError("未知待办操作")
