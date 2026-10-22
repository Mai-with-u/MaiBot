"""真实 stdio/HTTP MCP 联调用 ACP 对端，不访问任何模型或桌面。"""

from pathlib import Path

import json
import os
import sys

import httpx

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
session_id = "isolated-session"
mount = None
waiting = None


def send(message):
    print(json.dumps(message), flush=True)


def result(identifier, value):
    send({"jsonrpc": "2.0", "id": identifier, "result": value})


for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    log = {"method": method}
    if method == "session/prompt":
        log["has_image"] = any(block["type"] == "image" for block in request["params"]["prompt"])
    with Path(os.environ["ACP_TEST_LOG"]).open("a", encoding="utf-8") as output:
        output.write(json.dumps(log) + "\n")
    if method == "initialize":
        result(request["id"], {"protocolVersion": 1, "agentCapabilities": {
            "mcpCapabilities": {"http": True}, "promptCapabilities": {"image": True},
            "sessionCapabilities": {"resume": {}, "close": {}},
        }})
    elif method in {"session/new", "session/resume"}:
        if method == "session/resume" and os.environ.get("ACP_TEST_REJECT_RESUME"):
            send({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32602, "message": "original resume failure"}})
            continue
        mount = request["params"]["mcpServers"][0]
        result(request["id"], {"sessionId": session_id, "configOptions": []})
    elif method == "session/prompt":
        text = "\n".join(block.get("text", "") for block in request["params"]["prompt"]).strip()
        if text.endswith("WAIT"):
            waiting = request["id"]
            continue
        if text.endswith("TOOLS"):
            headers = {item["name"]: item["value"] for item in mount["headers"]}
            headers.update({"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"})
            with httpx.Client(trust_env=False, headers=headers) as client:
                def mcp_call(method, params, url=mount["url"]):
                    response = client.post(url, json={"jsonrpc": "2.0", "id": 1,
                                                               "method": method, "params": params})
                    response.raise_for_status()
                    return response.json()["result"]
                mcp_call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}})
                tools = mcp_call("tools/list", {})["tools"]
                assert {tool["name"] for tool in tools} == {"agent_talk", "agent_status", "agent_chats", "agent_recent"}
                response = mcp_call("tools/call", {"name": "agent_talk", "arguments": {
                    "text": "result", "target": "chat", "images": ["result.png"],
                }})
                assert response.get("isError") is not True
        send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk",
                                                  "content": {"type": "text", "text": "external reply"}},
        }})
        result(request["id"], {"stopReason": "end_turn"})
    elif method == "session/cancel":
        result(waiting, {"stopReason": "cancelled"})
        waiting = None
    else:
        result(request["id"], {})
