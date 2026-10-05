import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor, Json

PROTOCOL_VERSION = "2024-11-05"
SERVER_VERSION = "1.1.0"
SERVER_NAME = "hardware-check"

# User-Agent per Wikipedia (correzione A)
HEADERS = {
    "User-Agent": "hardware-check-mcp/1.1 (github.com/carellif-lgtm/hardware-check; contact: carellif-lgtm)",
    "Accept": "application/json",
}

# Campi specifiche da estrarre
SPEC_FIELDS = (
    "soc",
    "cpu",
    "memory",
    "storage",
    "display",
    "battery",
)

# Database URL per cache (correzione D)
DATABASE_URL = os.getenv("DATABASE_URL")
CACHE_TTL_HOURS = 24


def _get_db_connection():
    """Ottiene connessione DB con fallback graceful (correzione D)."""
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.OperationalError:
        return None
    except Exception:
        return None


def _get_cached_specs(device_name: str) -> dict | None:
    """Cerca specifiche in cache con TTL 24h."""
    conn = _get_db_connection()
    if not conn:
        return None
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT specs_json, source_url, fetched_at, metadata_json
                FROM cache_specs
                WHERE device_name = %s
                AND expires_at > %s
                LIMIT 1
            """, (device_name, datetime.now(timezone.utc)))
            row = cur.fetchone()
            
            if row:
                return {
                    "specs": row['specs_json'],
                    "source_url": row['source_url'],
                    "fetched_at": row['fetched_at'].isoformat(),
                    "metadata": row['metadata_json'],
                    "from_cache": True
                }
    except psycopg2.OperationalError:
        pass
    except Exception:
        pass
    finally:
        conn.close()
    
    return None


def _cache_specs(device_name: str, specs: dict, source_url: str, fetched_at: str, metadata: dict) -> None:
    """Salva specifiche in cache (silente se DB non disponibile)."""
    conn = _get_db_connection()
    if not conn:
        return
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cache_specs (device_name, specs_json, source_url, fetched_at, metadata_json, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_name) DO UPDATE
                SET 
                    specs_json = EXCLUDED.specs_json,
                    source_url = EXCLUDED.source_url,
                    fetched_at = EXCLUDED.fetched_at,
                    metadata_json = EXCLUDED.metadata_json,
                    cached_at = NOW(),
                    expires_at = EXCLUDED.expires_at
            """, (
                device_name,
                specs,
                source_url,
                datetime.fromisoformat(fetched_at.replace("Z", "+00:00")),
                Json(metadata),
                datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)
            ))
            conn.commit()
    except psycopg2.OperationalError:
        conn.rollback()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _build_metadata(article_type: str, fields_found: list, fields_missing: list) -> dict:
    """Costruisce metadata per la risposta."""
    return {
        "article_type": article_type,
        "fields_found": fields_found,
        "fields_missing": fields_missing
    }


def _fetch_wikipedia_opensearch(query: str) -> tuple[str | None, str | None]:
    """Fetch Wikipedia OpenSearch. Restituisce (title, url) o (None, None)."""
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "format": "json",
        "origin": "*",
        "search": query,
        "limit": "1",
    }
    try:
        with httpx.Client(headers=HEADERS, timeout=10.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if len(data) >= 3 and len(data[1]) >= 1:
                title = data[1][0]
                link = data[3][0] if len(data) >= 4 and len(data[3]) >= 1 else None
                return title, link
    except Exception:
        pass
    return None, None


def _fetch_wikipedia_infobox(title: str) -> dict | None:
    """Fetch infobox Wikipedia. Restituisce dict o None in caso di errore."""
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "origin": "*",
        "titles": title,
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
    }
    try:
        with httpx.Client(headers=HEADERS, timeout=10.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                if int(page_id) < 0:
                    return None
                revisions = page_data.get("revisions", [])
                if not revisions:
                    return None
                text = revisions[0].get("slots", {}).get("main", {}).get("*", "")
                return _parse_infobox(text)
    except Exception:
        pass
    return None


def _parse_infobox(text: str) -> dict:
    """Parsa infobox Wikipedia estraendo campi specifici."""
    infobox_match = re.search(r"\{\{Infobox[^}]*\}\}", text, re.DOTALL | re.IGNORECASE)
    if not infobox_match:
        return {}
    
    infobox_text = infobox_match.group(0)
    fields = {}
    
    for field in SPEC_FIELDS:
        pattern = rf"\|\s*{field}\s*=\s*([^|\n]+)"
        match = re.search(pattern, infobox_text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value:
                fields[field] = value
    
    return fields


def choose_title(query: str, titles: list[str]) -> tuple[str | None, dict | None]:
    """Sceglie titolo migliore e restituisce metadata."""
    if not titles:
        return None, _build_metadata("missing", [], list(SPEC_FIELDS))
    
    query_lower = query.lower()
    
    for title in titles:
        if query_lower in title.lower():
            fields_found = []
            fields_missing = [f for f in SPEC_FIELDS if f not in fields_found]
            return title, _build_metadata("model", fields_found, fields_missing)
    
    first_title = titles[0]
    fields_found = []
    fields_missing = [f for f in SPEC_FIELDS if f not in fields_found]
    return first_title, _build_metadata("family", fields_found, fields_missing)


def extract_infobox_fields(text: str) -> dict:
    """Estrae campi infobox da testo Wikipedia."""
    return _parse_infobox(text)


def get_device(query: str) -> dict:
    """Ottiene info dispositivo da Wikipedia OpenSearch."""
    cleaned = (query or "").strip()
    if len(cleaned) < 2:
        raise ValueError("query must be at least 2 characters")
    
    title, wiki_url = _fetch_wikipedia_opensearch(cleaned)
    
    if not title:
        fields_missing = list(SPEC_FIELDS)
        return {
            "name": cleaned,
            "source_url": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "confidence": "low",
            "metadata": _build_metadata("missing", [], fields_missing)
        }
    
    infobox_fields = _fetch_wikipedia_infobox(title) if wiki_url else {}
    
    fields_found = list(infobox_fields.keys())
    fields_missing = [f for f in SPEC_FIELDS if f not in infobox_fields]
    
    if len(infobox_fields) < 3:
        article_type = "family"
        confidence = "medium"
    else:
        article_type = "model"
        confidence = "high"
    
    return {
        "name": title,
        "source_url": wiki_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "confidence": confidence,
        "metadata": _build_metadata(article_type, fields_found, fields_missing)
    }


def get_specs(device_name: str) -> dict:
    """Ottiene specifiche dispositivo con cache trasparente."""
    cleaned = (device_name or "").strip()
    if len(cleaned) < 2:
        raise ValueError("device_name must be at least 2 characters")
    
    # Cerca in cache prima
    cached = _get_cached_specs(cleaned)
    if cached:
        return cached
    
    # Fetch da Wikipedia
    title, wiki_url = _fetch_wikipedia_opensearch(cleaned)
    
    if not title or not wiki_url:
        fields_missing = list(SPEC_FIELDS)
        return {
            "specs": {},
            "source_url": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "metadata": _build_metadata("missing", [], fields_missing),
            "from_cache": False
        }
    
    infobox_fields = _fetch_wikipedia_infobox(title)
    
    fields_found = list(infobox_fields.keys())
    fields_missing = [f for f in SPEC_FIELDS if f not in infobox_fields]
    
    if len(infobox_fields) < 3:
        article_type = "family"
    else:
        article_type = "model"
    
    metadata = _build_metadata(article_type, fields_found, fields_missing)
    fetched_at = datetime.now(timezone.utc).isoformat()
    
    # Salva in cache
    _cache_specs(cleaned, infobox_fields, wiki_url, fetched_at, metadata)
    
    return {
        "specs": infobox_fields,
        "source_url": wiki_url,
        "fetched_at": fetched_at,
        "metadata": metadata,
        "from_cache": False
    }


def handle_mcp_request(request_body: str, environ: dict | None = None) -> str:
    """Gestisce richiesta MCP JSON-RPC con error handling robusto (correzione C)."""
    try:
        request = json.loads(request_body)
    except json.JSONDecodeError as e:
        return json.dumps({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": f"Parse error: {e}"}
        })
    
    jsonrpc = request.get("jsonrpc", "2.0")
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})
    
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return json.dumps({"jsonrpc": jsonrpc, "id": req_id, "result": result})
    
    if method == "tools/list":
        tools = [
            {
                "name": "get_device",
                "description": "Identifica dispositivo hardware da query (nome, URL Wikipedia, metadati)",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]
                }
            },
            {
                "name": "get_specs",
                "description": "Ottiene specifiche hardware da infobox Wikipedia con cache trasparente",
                "inputSchema": {
                    "type": "object",
                    "properties": {"device_name": {"type": "string"}},
                    "required": ["device_name"]
                }
            }
        ]
        return json.dumps({"jsonrpc": jsonrpc, "id": req_id, "result": {"tools": tools}})
    
    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        
        try:
            if tool_name == "get_device":
                query = args.get("query", "")
                result = get_device(query)
                content = [{"type": "text", "text": json.dumps(result, indent=2)}]
                return json.dumps({"jsonrpc": jsonrpc, "id": req_id, "result": {"content": content}})
            
            if tool_name == "get_specs":
                device_name = args.get("device_name", "")
                result = get_specs(device_name)
                content = [{"type": "text", "text": json.dumps(result, indent=2)}]
                return json.dumps({"jsonrpc": jsonrpc, "id": req_id, "result": {"content": content}})
            
            return json.dumps({
                "jsonrpc": jsonrpc,
                "id": req_id,
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"}
            })
        
        except ValueError as e:
            return json.dumps({
                "jsonrpc": jsonrpc,
                "id": req_id,
                "error": {"code": -32602, "message": f"Invalid params: {e}"}
            })
        
        except httpx.HTTPError as e:
            return json.dumps({
                "jsonrpc": jsonrpc,
                "id": req_id,
                "error": {"code": -32603, "message": f"Source unavailable: {type(e).__name__}"}
            })
        
        except Exception as e:
            return json.dumps({
                "jsonrpc": jsonrpc,
                "id": req_id,
                "error": {"code": -32603, "message": f"Internal error: {type(e).__name__}"}
            })
    
    return json.dumps({
        "jsonrpc": jsonrpc,
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    })
