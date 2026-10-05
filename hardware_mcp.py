import json
from datetime import datetime, timezone

import httpx

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "hardware-check", "version": "0.3.1"}
OPENSEARCH_URL = "https://en.wikipedia.org/w/api.php"
SPEC_FIELDS = ("soc", "cpu", "memory", "storage", "display", "battery")
HEADERS = {
    "User-Agent": "HardwareCheckMCP/0.3 (+https://github.com/carellif-lgtm/hardware-check)",
    "Accept": "application/json",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _strip_links(value: str) -> str:
    while "[[" in value and "]]" in value:
        start = value.find("[[")
        end = value.find("]]", start)
        if end < 0:
            break
        label = value[start + 2:end].split("|")[-1]
        value = value[:start] + label + value[end + 2:]
    return value


def _clean_wiki(value: str) -> str:
    value = value.replace("&nbsp;", " ").replace("\xa0", " ")
    value = value.replace("'''", "").replace("''", "")
    value = value.replace("<br />", "; ").replace("<br/>", "; ").replace("<br>", "; ")
    value = _strip_links(value)
    value = value.replace("{{ubl", "").replace("{{plainlist", "").replace("{{flatlist", "")
    value = value.replace("}}", "").replace("{{", "")
    return " ".join(value.split()).strip(" ;|")


def extract_infobox_fields(wikitext: str) -> dict:
    start = wikitext.find("{{Infobox")
    if start < 0:
        return {}
    current = None
    buf = []
    fields = {}
    for line in wikitext[start:].splitlines()[1:]:
        stripped = line.lstrip()
        if stripped.startswith("|") and "=" in stripped:
            if current in SPEC_FIELDS:
                cleaned = _clean_wiki(" ".join(buf))
                if cleaned:
                    fields[current] = cleaned
            name, _, rest = stripped[1:].partition("=")
            current = name.strip()
            buf = [rest]
            continue
        if current:
            buf.append(line)
    if current in SPEC_FIELDS:
        cleaned = _clean_wiki(" ".join(buf))
        if cleaned:
            fields[current] = cleaned
    return fields


def _wikipedia_lookup(query: str):
    params = {"action": "opensearch", "search": query, "limit": 1, "namespace": 0, "format": "json"}
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=12.0) as client:
        response = client.get(OPENSEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    titles = payload[1] if len(payload) > 1 else []
    urls = payload[3] if len(payload) > 3 else []
    if not titles or not urls:
        raise LookupError("No Wikipedia article found for %r" % query)
    return titles[0], urls[0]


def get_device(query: str) -> dict:
    cleaned = (query or "").strip()
    if len(cleaned) < 2:
        raise ValueError("query must be at least 2 characters")
    title, source_url = _wikipedia_lookup(cleaned)
    return {
        "query": cleaned,
        "name": title,
        "category": "device",
        "source_name": "Wikipedia",
        "source_url": source_url,
        "fetched_at": _now(),
        "confidence": "medium",
        "notes": "Identity and canonical URL only. GSMArena is blocked by Cloudflare from Vercel; benchmark scores are not inferred.",
    }


def get_specs(query: str) -> dict:
    cleaned = (query or "").strip()
    if len(cleaned) < 2:
        raise ValueError("query must be at least 2 characters")
    title, source_url = _wikipedia_lookup(cleaned)
    fetched_at = _now()
    params = {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "redirects": 1}
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=12.0) as client:
        response = client.get(OPENSEARCH_URL, params=params)
        response.raise_for_status()
        wikitext = response.json()["parse"]["wikitext"]["*"]
    raw_fields = extract_infobox_fields(wikitext)
    if not raw_fields:
        raise LookupError("No infobox spec fields found for %s" % title)
    fields = {
        name: {"value": value, "source_url": source_url, "fetched_at": fetched_at, "confidence": "medium"}
        for name, value in raw_fields.items()
    }
    return {
        "query": cleaned,
        "name": title,
        "source_name": "Wikipedia",
        "source_url": source_url,
        "fetched_at": fetched_at,
        "fields": fields,
        "notes": "Raw infobox text. Multi-variant articles are not collapsed into one ram_gb, storage_gb, or battery_wh.",
    }


TOOLS = {
    "get_device": {
        "description": "Find a device article and return its name plus canonical source URL. Does not invent benchmark scores.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Device name, for example Pixel 8"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": get_device,
    },
    "get_specs": {
        "description": "Return sourced Wikipedia infobox text for soc, memory, storage, display, and battery. Does not invent normalized numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Device name, for example Pixel 8"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": get_specs,
    },
}


def handle_mcp_request(payload: dict):
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
                    {"name": name, "description": tool["description"], "inputSchema": tool["inputSchema"]}
                    for name, tool in TOOLS.items()
                ]
            },
        }
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if tool is None:
            return 200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Tool not found: %s" % name}}
        try:
            result = tool["handler"](**arguments)
            return 200, {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False},
            }
        except Exception as exc:
            return 200, {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
    return 200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found: %s" % method}}
