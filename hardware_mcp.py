import json
import re
from datetime import datetime, timezone

import httpx

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "hardware-check", "version": "0.3.4"}
OPENSEARCH_URL = "https://en.wikipedia.org/w/api.php"
SPEC_FIELDS = (
    "soc",
    "cpu",
    "processor",
    "system_on_chip",
    "graphics",
    "memory",
    "storage",
    "display",
    "battery",
)
HEADERS = {
    "User-Agent": "HardwareCheckMCP/0.3 (+https://github.com/carellif-lgtm/hardware-check)",
    "Accept": "application/json",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _tokens(value: str) -> set:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def choose_title(query: str, titles: list) -> str:
    query_tokens = _tokens(query)
    best = None
    best_score = 0
    for title in titles:
        title_tokens = _tokens(title)
        if not query_tokens or not title_tokens:
            continue
        score = len(query_tokens & title_tokens) / len(query_tokens)
        if score > best_score or (score == best_score and best and len(title) < len(best)):
            best = title
            best_score = score
    if best is None or best_score < 0.5:
        raise LookupError("No Wikipedia article found for %r" % query)
    return best


def _strip_links(value: str) -> str:
    while "[[" in value and "]]" in value:
        start = value.find("[[")
        end = value.find("]]", start)
        if end < 0:
            break
        label = value[start + 2:end].split("|")[-1]
        value = value[:start] + label + value[end + 2:]
    return value


def _expand_templates(value: str) -> str:
    while "{{" in value and "}}" in value:
        end = value.find("}}")
        start = value.rfind("{{", 0, end)
        if start < 0:
            break
        inner = value[start + 2:end]
        name, _, rest = inner.partition("|")
        parts = [part.strip() for part in rest.split("|") if part.strip()]
        key = name.strip().lower()
        if key == "convert" and len(parts) >= 2:
            replacement = parts[0] + " " + parts[1]
        elif key == "resx" and len(parts) >= 2:
            replacement = parts[0] + " x " + parts[1]
        elif key in ("ubl", "plainlist", "flatlist"):
            replacement = " | ".join(parts)
        else:
            replacement = ""
        value = value[:start] + replacement + value[end + 2:]
    return value


def _clean_wiki(value: str) -> str:
    value = value.replace("&nbsp;", " ").replace("\xa0", " ")
    value = value.replace("'''", "").replace("''", "")
    value = value.replace("<br />", "; ").replace("<br/>", "; ").replace("<br>", "; ")
    value = _strip_links(value)
    value = _expand_templates(value)
    return " ".join(value.split()).strip(" ;|")


def _field_start(line):
    stripped = line.lstrip()
    if not stripped.startswith("|"):
        return None
    body = stripped[1:].lstrip()
    if "=" not in body:
        return None
    name, _, rest = body.partition("=")
    name = name.strip().lower()
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        return None
    return name, rest


def extract_infobox_fields(wikitext: str) -> dict:
    start = wikitext.find("{{Infobox")
    if start < 0:
        return {}
    current = None
    buf = []
    fields = {}
    for line in wikitext[start:].splitlines()[1:]:
        started = _field_start(line)
        if started:
            if current in SPEC_FIELDS:
                cleaned = _clean_wiki(" ".join(buf))
                if cleaned:
                    fields[current] = cleaned
            current, rest = started
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
    params = {"action": "opensearch", "search": query, "limit": 5, "namespace": 0, "format": "json"}
    search_params = {"action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json"}
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=12.0) as client:
        response = client.get(OPENSEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        searched = client.get(OPENSEARCH_URL, params=search_params)
        searched.raise_for_status()
        hits = searched.json().get("query", {}).get("search", [])
    titles = list(payload[1] if len(payload) > 1 else [])
    urls = list(payload[3] if len(payload) > 3 else [])
    by_title = {title: url for title, url in zip(titles, urls)}
    for hit in hits:
        title = hit.get("title")
        if title and title not in by_title:
            by_title[title] = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    title = choose_title(query, list(by_title))
    return title, by_title[title]


def get_device(query: str) -> dict:
    cleaned = (query or "").strip()
    if len(cleaned) < 2:
        raise ValueError("query must be at least 2 characters")
    title, source_url = _wikipedia_lookup(cleaned)
    notes = "Identity and canonical URL only. Benchmark scores are not inferred."
    if _tokens(cleaned) != _tokens(title):
        notes = "Matched a related Wikipedia article, not a separate page for the exact query. " + notes
    return {
        "query": cleaned,
        "name": title,
        "category": "device",
        "source_name": "Wikipedia",
        "source_url": source_url,
        "fetched_at": _now(),
        "confidence": "medium",
        "notes": notes,
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
    notes = "Raw infobox text. Missing model-specific values are omitted, not estimated."
    if _tokens(cleaned) != _tokens(title):
        notes = "Article title differs from the query. " + notes
    return {
        "query": cleaned,
        "name": title,
        "source_name": "Wikipedia",
        "source_url": source_url,
        "fetched_at": fetched_at,
        "fields": fields,
        "notes": notes,
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
        "description": "Return sourced Wikipedia infobox text. Does not invent normalized numbers or missing model specs.",
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
