"""回复效果完整记录的无损压缩编解码。"""

from typing import Any, Dict

import json
import zlib


_RECORD_BLOB_MAGIC = b"MRE2Z\x00"
_COMPRESSION_LEVEL = 9


def encode_record_payload(payload: Dict[str, Any]) -> bytes:
    """把完整记录编码为带格式标识的 zlib 数据，不裁剪任何字段。"""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _RECORD_BLOB_MAGIC + zlib.compress(serialized, level=_COMPRESSION_LEVEL)


def decode_record_payload(record_json: str, record_blob: bytes | None = None) -> Dict[str, Any]:
    """优先读取压缩记录，并兼容迁移前的明文 JSON。"""

    if record_blob:
        if not record_blob.startswith(_RECORD_BLOB_MAGIC):
            raise ValueError("回复效果压缩记录格式标识无效")
        try:
            serialized = zlib.decompress(record_blob[len(_RECORD_BLOB_MAGIC) :]).decode("utf-8")
        except (UnicodeDecodeError, zlib.error) as exc:
            raise ValueError("回复效果压缩记录损坏") from exc
    else:
        serialized = record_json
    payload = json.loads(serialized)
    if not isinstance(payload, dict):
        raise ValueError("回复效果记录必须是 JSON 对象")
    return payload
