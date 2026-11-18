from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from aiohttp import web

import base64

import pytest

import weixin_clawbot_adapter.client as protocol
from weixin_clawbot_adapter.client import APIError, Client, decode_key


@pytest.fixture
async def service():
    app = web.Application()
    requests = []
    uploads = []
    key = b"0123456789abcdef"

    async def api(request):
        body = await request.json() if request.method == "POST" else None
        requests.append((request.match_info["endpoint"], dict(request.headers), body))
        endpoint = request.match_info["endpoint"]
        if endpoint == "getuploadurl":
            return web.json_response({"ret": 0, "upload_param": "a+b/c="})
        if endpoint == "error":
            return web.json_response({"ret": -14, "message": "secret-token"})
        if endpoint == "get_bot_qrcode":
            return web.json_response({"qrcode": "123", "qrcode_img_content": "content"})
        return web.json_response({"ret": 0})

    async def upload(request):
        assert "Authorization" not in request.headers
        assert request.query["encrypted_query_param"] == "a+b/c="
        uploads.append(await request.read())
        return web.Response(headers={"x-encrypted-param": "download-query"})

    async def download(request):
        assert "Authorization" not in request.headers
        return web.Response(body=AES.new(key, AES.MODE_ECB).encrypt(pad(b"test-image", 16)))

    app.router.add_route("*", "/ilink/bot/{endpoint}", api)
    app.router.add_post("/upload", upload)
    app.router.add_get("/download", download)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}", requests, uploads, key
    finally:
        await runner.cleanup()


async def test_http_auth_body_and_errors(service):
    url, requests, _, _ = service
    client = Client(token="token")
    client.base_url = url  # Only the local protocol fixture bypasses production URL validation.
    try:
        await client.call("get_bot_qrcode", params={"bot_type": "3"}, login=True)
        assert "Authorization" not in requests[-1][1]
        await client.send("user", "context", [{"type": 1, "text_item": {"text": "hello"}}], "cid")
        _, headers, body = requests[-1]
        assert headers["Authorization"] == "Bearer token"
        assert headers["AuthorizationType"] == "ilink_bot_token"
        assert body["base_info"]["channel_version"] == "maibot"
        assert body["msg"]["context_token"] == "context"
        assert body["msg"]["client_id"] == "cid"
        with pytest.raises(APIError) as error:
            await client.call("error", {})
        assert error.value.code == -14
        assert "secret-token" not in str(error.value)
    finally:
        await client.close()


async def test_encrypted_media_round_trip(service, monkeypatch):
    url, requests, uploads, key = service
    monkeypatch.setattr(protocol, "CDN_URL", url)
    client = Client(token="token")
    client.base_url = url
    try:
        result = await client.upload_image("user", b"outgoing-image")
        _, _, body = requests[-1]
        assert body["media_type"] == 1
        upload_key = bytes.fromhex(body["aeskey"])
        assert unpad(AES.new(upload_key, AES.MODE_ECB).decrypt(uploads[0]), 16) == b"outgoing-image"
        assert decode_key(result["image_item"]["media"]["aes_key"]) == upload_key
        media = {"media": {"encrypt_query_param": "query", "aes_key": base64.b64encode(key).decode()}}
        assert await client.download_image(media) == b"test-image"
        assert await client.download_image({**media, "aeskey": key.hex()}) == b"test-image"
        client.max_bytes = 2
        with pytest.raises(ValueError, match="大小限制"):
            await client.download_image(media)
    finally:
        await client.close()
