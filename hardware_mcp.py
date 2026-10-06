import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor, Json

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_VERSION = "1.2.0"
SERVER_NAME = "hardware-check"
PARSER_VERSION = "1.2"  # Per invalidazione cache

# User-Agent per Wikipedia
HEADERS = {
    "User-Agent": "hardware-check-mcp/1.2 (github.com/carellif-lgtm/hardware-check; contact: carellif-lgtm)",
    "Accept": "application/json",
}

# Campi specifiche da estrarre (testo grezzo, nessuna normalizzazione)
SPEC_FIELDS = (
    "soc",
    "cpu",
    "memory",
    "storage",
    "display",
    "battery",
)

# Database URL per cache
DATABASE_URL = os.getenv("DATABASE_URL")
CACHE_TTL_HOURS = 24


def _get_db_connection():
    """Ottiene connessione DB con fallback graceful."""
    if not DATABASE_URL:
        logger.warning("cache disabled: DATABASE_URL not set")
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.OperationalError as e:
        logger.error("cache db connection failed: %s", type(e).__name__)
        return None
    except Exception as e:
        logger.error("cache db connection failed: %s", type(e).__name__)
        return None


def _get_cached_specs(device_name: str) -> dict | None:
    """Cerca specifiche in cache con TTL 24h e parser_version."""
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
                AND (parser_version = %s OR parser_version IS NULL)
                LIMIT 1
            """, (device_name, datetime.now(timezone.utc), PARSER_VERSION))
            row = cur.fetchone()

            if row:
                logger.info("cache hit device=%s", device_name[:80])
                return {
                    "specs": row['specs_json'],
                    "source_url": row['source_url'],
                    "fetched_at": row['fetched_at'].isoformat(),
                    "metadata": row['metadata_json'],
                    "from_cache": True
                }
            logger.info("cache miss device=%s", device_name[:80])
    except psycopg2.OperationalError as e:
        logger.error("cache read failed: %s device=%s", type(e).__name__, device_name[:80])
    except Exception as e:
        logger.error("cache read failed: %s device=%s", type(e).__name__, device_name[:80])
    finally:
        conn.close()
    
    return None


def _cache_specs(device_name: str, specs: dict, source_url: str, fetched_at: str, metadata: dict) -> None:
    """Salva specifiche in cache con parser_version (silente se DB non disponibile)."""
    conn = _get_db_connection()
    if not conn:
        logger.warning("cache write skipped: no connection device=%s", device_name[:80])
        return

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cache_specs (device_name, specs_json, source_url, fetched_at, metadata_json, expires_at, parser_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_name) DO UPDATE
                SET 
                    specs_json = EXCLUDED.specs_json,
                    source_url = EXCLUDED.source_url,
                    fetched_at = EXCLUDED.fetched_at,
                    metadata_json = EXCLUDED.metadata_json,
                    cached_at = NOW(),
                    expires_at = EXCLUDED.expires_at,
                    parser_version = EXCLUDED.parser_version
            """, (
                device_name,
                specs,
                source_url,
                datetime.fromisoformat(fetched_at.replace("Z", "+00:00")),
                Json(metadata),
                datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS),
                PARSER_VERSION
            ))
            conn.commit()
            logger.info("cache write ok device=%s parser_version=%s", device_name[:80], PARSER_VERSION)
    except psycopg2.OperationalError as e:
        logger.error("cache write failed: %s device=%s", type(e).__name__, device_name[:80])
        conn.rollback()
    except Exception as e:
        logger.error("cache write failed: %s device=%s", type(e).__name__, device_name[:80])
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
    except httpx.TimeoutException:
        return None  # Timeout: trattato come missing
    except httpx.ConnectError:
        return None  # Connect error: trattato come missing
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None  # 404: realmente missing
        return None  # Altri errori HTTP: trattati come missing
    except Exception:
        return None  # Errori imprevisti: trattati come missing
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
                return _parse_infobox_robust(text)
    except httpx.TimeoutException:
        return None
    except httpx.ConnectError:
        return None
    except httpx.HTTPStatusError:
        return None
    except Exception:
        return None
    return None


def _extract_infobox_block(text: str, start: int) -> str | None:
    """Estrae blocco infobox contando parentesi graffe annidate (parser a stati)."""
    if not text[start:].startswith("{{"):
        return None
    
    depth = 0
    i = start
    
    while i < len(text):
        if text[i:i+2] == "{{":
            depth += 1
            i += 2
        elif text[i:i+2] == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return text[start:i]
        else:
            i += 1
    
    return None  # Infobox non chiuso


def _extract_field_value_at_depth_0(infobox_text: str, field: str) -> str | None:
    """Estrae valore di un campo dall'infobox, spezzando solo ai | di livello 0."""
    # Pattern per trovare | field = valore
    pattern = rf"\|\s*{field}\s*="
    match = re.search(pattern, infobox_text, re.IGNORECASE)
    if not match:
        return None
    
    # Inizia dopo il =
    start = match.end()
    value_chars = []
    depth_braces = 0  # {{ }}
    depth_brackets = 0  # [[ ]]
    
    i = start
    while i < len(infobox_text):
        char = infobox_text[i]
        
        # Gestisci {{ e }}
        if infobox_text[i:i+2] == "{{":
            depth_braces += 1
            value_chars.append("{{")
            i += 2
            continue
        elif infobox_text[i:i+2] == "}}":
            depth_braces -= 1
            value_chars.append("}}")
            i += 2
            continue
        
        # Gestisci [[ e ]]
        if infobox_text[i:i+2] == "[[":
            depth_brackets += 1
            value_chars.append("[[")
            i += 2
            continue
        elif infobox_text[i:i+2] == "]]":
            depth_brackets -= 1
            value_chars.append("]]")
            i += 2
            continue
        
        # Se siamo a profondità 0 e troviamo |, abbiamo finito
        if char == "|" and depth_braces == 0 and depth_brackets == 0:
            break
        
        # Se siamo a profondità 0 e troviamo }} (fine infobox), abbiamo finito
        if char == "}" and infobox_text[i:i+2] == "}}" and depth_braces == 0:
            break
        
        value_chars.append(char)
        i += 1
    
    value = "".join(value_chars).strip()
    return value if value else None


def _parse_infobox_robust(text: str) -> dict:
    """Parsa infobox Wikipedia con supporto per template annidati (robusto)."""
    # Trova inizio infobox
    match = re.search(r"\{\{Infobox", text, re.IGNORECASE)
    if not match:
        return {}
    
    # Estrae blocco completo con parser a stati
    infobox_text = _extract_infobox_block(text, match.start())
    if not infobox_text:
        return {}
    
    fields = {}
    
    # Estrae ogni campo con estrattore a profondità 0
    for field in SPEC_FIELDS:
        value = _extract_field_value_at_depth_0(infobox_text, field)
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
    """Estrae campi infobox da testo Wikipedia (testo grezzo, parser robusto)."""
    return _parse_infobox_robust(text)


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
    """Ottiene specifiche dispositivo con cache trasparente (testo grezzo)."""
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
    """Gestisce richiesta MCP JSON-RPC con error handling robusto."""
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
                "description": "Ottiene specifiche hardware da infobox Wikipedia (testo grezzo, parser robusto)",
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
