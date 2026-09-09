"""Synthetic ACP v1 peer for infrastructure tests. Never calls a model."""

import json
import sys
import time


def send(value):
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}
    elif method == "session/new":
        result = {"sessionId": "synthetic-session"}
    elif method == "session/prompt":
        time.sleep(0.3)
        send(
            {
                "method": "session/update",
                "params": {
                    "sessionId": "synthetic-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "LYRA_SANDBOX_OK"},
                    },
                },
            }
        )
        result = {"stopReason": "end_turn"}
    else:
        send({"id": message.get("id"), "error": {"code": -32601, "message": "Unsupported method"}})
        continue
    send({"id": message["id"], "result": result})
