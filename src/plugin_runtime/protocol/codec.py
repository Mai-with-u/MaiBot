"""MsgPack 编解码器"""

from typing import Any

import msgpack

from .envelope import Envelope


class Codec:
    """消息编解码器基类"""

    def encode_envelope(self, envelope: Envelope) -> bytes:
        raise NotImplementedError

    def decode_envelope(self, data: bytes) -> Envelope:
        raise NotImplementedError

    def encode(self, obj: dict[str, Any]) -> bytes:
        raise NotImplementedError

    def decode(self, data: bytes) -> dict[str, Any]:
        raise NotImplementedError


class MsgPackCodec(Codec):
    """MsgPack 编解码器"""

    def encode(self, obj: dict[str, Any]) -> bytes:
        return msgpack.packb(obj, use_bin_type=True)

    def decode(self, data: bytes) -> dict[str, Any]:
        result = msgpack.unpackb(data, raw=False)
        if not isinstance(result, dict):
            raise ValueError(f"期望解码为 dict，实际为 {type(result)}")
        return result

    def encode_envelope(self, envelope: Envelope) -> bytes:
        return self.encode(envelope.model_dump())

    def decode_envelope(self, data: bytes) -> Envelope:
        raw = self.decode(data)
        return Envelope.model_validate(raw)
