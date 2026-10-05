import json
import re
from datetime import datetime, timezone

import httpx

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "hardware-check", "version": "0.2.0"}
SEARCH_URL = "https://www.gsmarena.com/res.php3"


def get_device(query: str) -> dict:
    cleaned = (query or "").strip()
    if len(cleaned) < 2:
        raise ValueError("query must be at least 2 characters")

    headers = {
        "User-Agent": "HardwareCheckMCP/0.2 (+https://github.com/carellif-lgtm/hardware-check)",
        "Accept": "text/html",
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=12.0) as client:
        response = client.get(SEARCH_URL, params={"sSearch": cleaned})
        response.raise_for_status()
        html = response.text

    match = re.search(
        r'href="([a-z0-9_]+-\d+\.php)"[^>]*>([^<]+)',
        html,
        flags=re.IGNORECASE,
    )
    if not match:
        raise LookupError(f"No GSMArena device URL found for {cleaned!r}")

    source_url = "https://www.gsmarena.com/" + match.group(1)
    return {
        "query": cleaned,
        "name": re.sub(r"\s+", " ", match.group(2)).strip(),
        "category": "phone",
        "source_name": "GSMArena",
        "source_url": source_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "confidence": "medium",
        "notes": "Name and canonical URL only. Benchmark scores are not inferred.",
    }


TOOLS = {
    "get_device": {
        "description": "Find a phone on GSMArena and return its name plus canonical source URL. Does not invent benchmark scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Device name, for example Pixel 8",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": get_device,
    }
}


def handle_mcp_request(payload: dict) -> tuple[int, dict | None]:
    if not isinstance(payload, dict):
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}

    method = payload.get("method")
    request_id = payload.get("id")
    params = payload.get("params") or {}
    if method == "notifications/initialized":
        return 202, None

    if method == "initialize":
        return 200, {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "tools/list":
        return 200, {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": name,
                        "description": tool["description"],
                        "inputSchema": tool["inputSchema"],
                    }
                    for name, tool in TOOLS.items()
                ]
            },
        }

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if tool is None:
            return 200, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": f"Tool not found: {name}"},
            }
        try:
            result = tool["handler"](**arguments)
            return 200, {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return 200, {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }

    return 200, {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }
