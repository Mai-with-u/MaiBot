"""独立相通组管理；同步端点由 WebUI 线程池执行。"""

from fastapi import APIRouter, HTTPException, Query

from src.services import sharing_group_service as service

router = APIRouter(prefix="/sharing-groups", tags=["Sharing groups"])


@router.get("")
def list_groups():
    groups = service.list_groups()
    for group in groups:
        group["resolved_ids"] = sorted(service.resolved_members(group))
    return {"data": groups, "capabilities": service.CAPABILITIES}


@router.post("")
def create_group(data: service.GroupInput):
    try:
        return service.save_group(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/{group_id}")
def update_group(group_id: str, data: service.GroupInput):
    if data.revision is None:
        raise HTTPException(422, "更新相通组必须提供 revision")
    try:
        return service.save_group(data, group_id)
    except KeyError as exc:
        raise HTTPException(404, "相通组不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/{group_id}")
def delete_group(group_id: str, revision: int = Query(ge=1)):
    try:
        service.delete_group(group_id, revision)
        return {"success": True}
    except KeyError as exc:
        raise HTTPException(404, "相通组不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
