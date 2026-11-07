from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import base64
import hashlib
import logging

import pytest

from src.plugins.built_in.qq_official_adapter import codec
from src.plugins.built_in.qq_official_adapter.plugin import QQOfficialAdapterPlugin


def message(segments, group=True):
    return {
        "message_info": {
            "group_info": {"group_id": "group-openid"} if group else None,
            "additional_config": {} if group else {"platform_io_target_user_id": "user-openid"},
        },
        "raw_message": segments,
        "reply_to": "incoming-id",
    }


def file_segment(binary=b"hello", name="测试.txt", kind="file"):
    return {"type": kind, "data": {"name": name, "base64": base64.b64encode(binary).decode()}}


def adapter():
    plugin = QQOfficialAdapterPlugin()
    plugin._set_context(SimpleNamespace(logger=logging.getLogger("qq-file-test")))
    plugin._ready = True
    return plugin


@pytest.mark.parametrize("group", [True, False])
def test_file_preserves_name_and_bytes_in_both_scenes(group):
    scene, target, parts, _ = codec.encode_message(message([file_segment()], group))
    assert scene == ("group" if group else "private")
    assert target == ("group-openid" if group else "user-openid")
    assert parts == [{"kind": "media", "file_type": 4, "file_name": "测试.txt", "binary": b"hello"}]


def test_media_order_and_video_file_are_distinct():
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12
    segments = [
        {"type": "text", "data": "before"},
        file_segment(mp4, "clip.mp4"),
        {"type": "dict", "data": file_segment(mp4, "clip.mp4", "video")},
        {"type": "text", "data": "after"},
    ]
    _, _, parts, _ = codec.encode_message(message(segments))
    assert [part.get("file_type", part.get("body", {}).get("content")) for part in parts] == ["before", 4, 2, "after"]


@pytest.mark.parametrize("data", [
    {"name": "x.txt", "base64": "???"},
    {"name": "x.txt", "base64": ""},
    {"name": "x.txt", "base64": "===="},
    {"name": "x.txt", "url": "http://localhost/secret"},
    {"name": "../secret.txt", "base64": "YQ=="},
    {"name": "C:\\secret.txt", "base64": "YQ=="},
    {"name": "a\nb.txt", "base64": "YQ=="},
    {"base64": "YQ=="},
    {"name": "a.txt", "file_ref": "ref", "size": 1},
    {"name": "a.txt", "file_ref": "ref", "size": 0, "url": "http://localhost/ticket"},
])
def test_invalid_file_payload_fails_before_upload(data):
    with pytest.raises(ValueError):
        codec.encode_message(message([{"type": "file", "data": data}]))


def test_file_limit_checked_before_and_after_base64_decode(monkeypatch):
    monkeypatch.setattr(codec, "MAX_OUTBOUND_FILE_BYTES", 4)
    for size in (5, 7):
        with pytest.raises(ValueError, match="100 MiB"):
            codec.encode_message(message([file_segment(b"x" * size)]))


def test_invalid_video_is_not_downgraded_to_file():
    with pytest.raises(ValueError, match="MP4"):
        codec.encode_message(message([file_segment(b"not a video", "fake.mp4", "video")]))


def test_host_file_reference_uses_ticket_instead_of_inline_data():
    segment = {"type": "file", "data": {
        "name": "来自宿主.txt", "file_ref": "ref-1", "url": "http://127.0.0.1/ticket",
        "size": "5", "base64": "invalid-and-must-not-be-used",
    }}
    _, _, parts, _ = codec.encode_message(message([segment]))
    assert parts == [{"kind": "media", "file_type": 4, "file_name": "来自宿主.txt",
                      "file_url": "http://127.0.0.1/ticket", "file_size": 5}]


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [True, False])
async def test_gateway_uploads_file_then_sends_with_passive_context(group):
    plugin = adapter()
    plugin._group_user_map = Mock(return_value={})
    plugin._upload_media = AsyncMock(return_value="file-info")
    plugin._claim_passive_fields = AsyncMock(return_value={"msg_id": "incoming-id", "msg_seq": 1})
    plugin._api_post = AsyncMock(return_value={"id": "sent-id"})
    result = await plugin.handle_qq_official_gateway(message([file_segment()], group))
    path = "/v2/groups/group-openid" if group else "/v2/users/user-openid"
    assert result == {"success": True, "external_message_id": "sent-id"}
    plugin._upload_media.assert_awaited_once_with(path, b"hello", "测试.txt", file_type=4)
    plugin._api_post.assert_awaited_once_with(
        path + "/messages", {"msg_type": 7, "media": {"file_info": "file-info"},
                             "msg_id": "incoming-id", "msg_seq": 1},
    )


class Response:
    def __init__(self, chunks=(), status=200, length=None):
        self.status = status
        self.content_length = length
        self.content = self
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize("response,error", [
    (Response([b"ab", b"c"], length=3), None),
    (Response([b"ab", b"c"]), None),
    (Response([b"ab"]), "不完整"),
    (Response([b"abcd"]), "超过"),
    (Response([], length=4), "不一致"),
    (Response([], status=302), "HTTP 302"),
    (Response([], status=403), "HTTP 403"),
])
async def test_host_download_exact_size_and_failures(response, error):
    plugin = adapter()
    session = SimpleNamespace(get=Mock(return_value=response))
    plugin._require_session = Mock(return_value=session)
    if error:
        with pytest.raises((ValueError, RuntimeError), match=error):
            await plugin._download_outbound_file("http://127.0.0.1/ticket", 3)
    else:
        assert await plugin._download_outbound_file("http://127.0.0.1/ticket", 3) == b"abc"
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert "headers" not in session.get.call_args.kwargs


@pytest.mark.asyncio
async def test_gateway_does_not_upload_or_send_after_download_failure():
    plugin = adapter()
    plugin._download_outbound_file = AsyncMock(side_effect=ValueError("下载不完整"))
    plugin._upload_media = AsyncMock()
    plugin._api_post = AsyncMock()
    payload = {"name": "sample.txt", "file_ref": "ref", "url": "http://localhost/ticket", "size": 5}
    result = await plugin.handle_qq_official_gateway(message([{"type": "file", "data": payload}], False))
    assert result == {"success": False, "error": "下载不完整"}
    plugin._upload_media.assert_not_awaited()
    plugin._api_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_uses_existing_chunk_upload_protocol():
    plugin = adapter()
    plugin._api_post = AsyncMock(side_effect=[
        {"upload_id": "upload", "block_size": 3, "parts": [
            {"index": 1, "presigned_url": "https://upload.example/1"},
            {"index": 2, "presigned_url": "https://upload.example/2"},
        ]}, {}, {}, {"file_info": "file-info"},
    ])
    session = SimpleNamespace(put=Mock(side_effect=[Response(), Response()]))
    plugin._require_session = Mock(return_value=session)
    result = await plugin._upload_media("/v2/groups/group", b"hello", "测试.txt", file_type=4)
    assert result == "file-info"
    calls = plugin._api_post.await_args_list
    assert calls[0].args[1] == {
        "file_type": 4, "file_size": "5", "file_name": "测试.txt",
        "md5": hashlib.md5(b"hello").hexdigest(), "sha1": hashlib.sha1(b"hello").hexdigest(),
        "md5_10m": hashlib.md5(b"hello").hexdigest(),
    }
    assert [call.kwargs["data"] for call in session.put.call_args_list] == [b"hel", b"lo"]
    assert calls[-1].args[1] == {
        "file_type": 4, "srv_send_msg": False, "file_name": "测试.txt", "upload_id": "upload",
    }
