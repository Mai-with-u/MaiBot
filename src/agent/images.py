"""Agent 双向附图：聊天图片、各宿主工作目录及显式提供的截图。"""

from io import BytesIO
from typing import TYPE_CHECKING, Dict, List, Sequence, Tuple

from PIL import Image

import asyncio
import base64
import re

from src.common.data_models.message_component_data_model import ImageComponent

if TYPE_CHECKING:
    from .service import AgentService


async def resolve_images(
    service: "AgentService", caller: str, references: Sequence[str],
) -> Tuple[List[ImageComponent], List[Dict[str, str]]]:
    if isinstance(references, str) or not all(isinstance(ref, str) and ref for ref in references):
        raise ValueError("images 必须是非空图片引用组成的列表")
    images: List[ImageComponent] = []
    attachments: List[Dict[str, str]] = []
    if not references:
        return images, attachments

    from .messaging import source_images

    available = await source_images(caller) if caller != service.agent_id else {}
    workspace = service.workspace

    def save(data: bytes) -> Tuple[ImageComponent, Dict[str, str]]:
        # 校验真实图片格式，原始字节保真保存；阻塞解码和文件操作在线程池进行。
        with Image.open(BytesIO(data)) as decoded:
            suffix = decoded.format.lower()
            decoded.verify()
        image = ImageComponent(binary_hash="", binary_data=data)
        folder = workspace / "relay_images"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{image.binary_hash}.{suffix}"
        path.write_bytes(data)
        image.content = f"[传话图片 hash={image.binary_hash}，附件文件={path}]"
        return image, {"hash": image.binary_hash, "path": str(path)}

    for reference in references:
        if reference in available:
            for original in available[reference]:
                image = original.clone()
                await image.load_image_binary()
                image, attachment = await asyncio.to_thread(save, image.binary_data)
                images.append(image)
                attachments.append(attachment)
            continue
        elif caller == service.agent_id and reference == "screenshot":
            frame = await service.observe()
            data = await asyncio.to_thread(base64.b64decode, frame["image"], validate=True)
        elif caller == service.agent_id and re.fullmatch(r"[a-f0-9]{64}", reference):
            matches = await asyncio.to_thread(lambda reference=reference: list((workspace / "relay_images").glob(reference + ".*")))
            if len(matches) != 1:
                raise ValueError(f"没有唯一的工作区图片：{reference}")
            data = await asyncio.to_thread(matches[0].read_bytes)
        elif caller == service.agent_id:
            path = await asyncio.to_thread(service.image_path, reference)
            data = await asyncio.to_thread(path.read_bytes)
        else:
            raise ValueError(f"当前聊天上下文中没有图片：{reference}；请使用带图消息 ID 或图片 hash")
        image, attachment = await asyncio.to_thread(save, data)
        images.append(image)
        attachments.append(attachment)
    return images, attachments
