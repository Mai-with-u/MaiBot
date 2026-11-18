"""插件能力：权限声明之外还必须持有当前工具调用的聊天授权。"""

from typing import Any, Dict

from .service import disk_call, get_file_service


async def file_capability(plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
    service = get_file_service()
    if not (capability == "files.upload" and args.get("operation") == "cancel"):
        service.require_enabled()
    token = args.get("file_scope", "")
    scope = service.claims(token, "plugin")
    if scope["plugin_id"] != plugin_id:
        raise PermissionError("文件作用域不属于当前插件")
    if capability == "files.upload" and args.get("operation") == "cancel":
        await service.cancel_upload_ticket(args["upload_token"], token, plugin_id)
        return {"cancelled": True}
    sid = scope["session_id"]
    service.require_session(sid)
    if capability == "files.list":
        return await disk_call(service.store.list, sid, offset=int(args.get("offset", 0)))
    if capability == "files.read_text":
        await service.ensure(args["file_ref"], sid)
        return await disk_call(service.read_text, args["file_ref"], sid, offset=int(args.get("offset", 0)),
                               limit=int(args.get("limit", 12000)), encoding=args.get("encoding", "utf-8-sig"))
    if capability == "files.download":
        asset = await service.ensure(args["file_ref"], sid)
        await disk_call(service.store.lease, asset.file_ref, sid)
        ticket = service.ticket("download", file_ref=asset.file_ref, scope=token)
        return {**service.store.public(asset), "url": service.transfer_url(ticket)}
    if capability == "files.upload":
        asset = await disk_call(service.store.begin_upload, sid, args["name"], args.get("mime_type", "application/octet-stream"))
        ticket = service.ticket("upload", file_ref=asset.file_ref, scope=token, plugin_id=plugin_id)
        return {"file_ref": asset.file_ref, "max_bytes": asset.reserved_bytes, "upload_token": ticket,
                "url": service.transfer_url(ticket)}
    if capability == "files.send":
        return await service.send(args["file_ref"], sid)
    raise ValueError("未知文件能力")
