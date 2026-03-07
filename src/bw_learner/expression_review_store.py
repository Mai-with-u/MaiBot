from typing import Any, Dict, Optional

from src.manager.local_store_manager import local_storage


def _review_key(expression_id: int) -> str:
    return f"expression_review:{expression_id}"


def get_review_state(expression_id: Optional[int]) -> Dict[str, Any]:
    if expression_id is None:
        return {"checked": False, "rejected": False, "modified_by": None}
    value = local_storage[_review_key(expression_id)]
    if isinstance(value, dict):
        return {
            "checked": bool(value.get("checked", False)),
            "rejected": bool(value.get("rejected", False)),
            "modified_by": value.get("modified_by"),
        }
    return {"checked": False, "rejected": False, "modified_by": None}


def set_review_state(
    expression_id: Optional[int],
    checked: bool,
    rejected: bool,
    modified_by: Optional[str],
) -> None:
    if expression_id is None:
        return
    local_storage[_review_key(expression_id)] = {
        "checked": checked,
        "rejected": rejected,
        "modified_by": modified_by,
    }
