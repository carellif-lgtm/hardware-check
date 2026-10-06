import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

import httpx
import psycopg2
from psycopg2.extensions import TRANSACTION_STATUS_IDLE
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


def _env_positive_float(name: str, default: float) -> float:
    """Legge un float strettamente positivo e finito da env; altrimenti default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("invalid %s=%r: not a number, using default %s", name, raw, default)
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning("invalid %s=%r: must be finite and > 0, using default %s", name, raw, default)
        return default
    return value


# Timeout configurabili da env (secondi). Budget peggiore ben sotto maxDuration Vercel (300s).
CACHE_LOCK_TIMEOUT_S = _env_positive_float("CACHE_LOCK_TIMEOUT_S", 15.0)
FETCH_CONNECT_TIMEOUT_S = _env_positive_float("FETCH_CONNECT_TIMEOUT_S", 3.0)
FETCH_READ_TIMEOUT_S = _env_positive_float("FETCH_READ_TIMEOUT_S", 5.0)
FETCH_WRITE_TIMEOUT_S = _env_positive_float("FETCH_WRITE_TIMEOUT_S", 5.0)
FETCH_POOL_TIMEOUT_S = _env_positive_float("FETCH_POOL_TIMEOUT_S", 3.0)

FETCH_TIMEOUT = httpx.Timeout(
    connect=FETCH_CONNECT_TIMEOUT_S,
    read=FETCH_READ_TIMEOUT_S,
    write=FETCH_WRITE_TIMEOUT_S,
    pool=FETCH_POOL_TIMEOUT_S,
)


def _lock_timeout_literal() -> str:
    """lock_timeout per SET LOCAL, in millisecondi (es. '15000ms').

    Minimo 1ms: in PostgreSQL '0ms' disabiliterebbe il timeout (attesa infinita).
    """
    return f"{max(1, int(CACHE_LOCK_TIMEOUT_S * 1000))}ms"


class CacheStatus(Enum):
    HIT = "hit"
    LIVE_FETCHED = "live_fetched"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CacheResolutionResult:
    status: CacheStatus
    payload: dict | None = None


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


def _select_cached_specs(conn, device_name: str) -> dict | None:
    """SELECT cache valida (TTL + parser_version) sulla connessione data. Nessun commit/close."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
                SELECT specs_json, source_url, fetched_at, metadata_json
                FROM cache_specs
                WHERE device_name = %s
                AND expires_at > %s
                AND parser_version = %s
                LIMIT 1
            """, (device_name, datetime.now(timezone.utc), PARSER_VERSION))
        row = cur.fetchone()

    if not row:
        return None
    return {
        "specs": row['specs_json'],
        "source_url": row['source_url'],
        "fetched_at": row['fetched_at'].isoformat(),
        "metadata": row['metadata_json'],
        "from_cache": True
    }


def _upsert_cached_specs(conn, device_name: str, specs: dict, source_url: str, fetched_at: str, metadata: dict) -> None:
    """UPSERT in cache sulla connessione data. Il COMMIT spetta al chiamante."""
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
            Json(specs),
            source_url,
            datetime.fromisoformat(fetched_at.replace("Z", "+00:00")),
            Json(metadata),
            datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS),
            PARSER_VERSION
        ))


def _advisory_lock_key(device_name: str) -> str:
    """Chiave testuale dell'advisory lock per device (namespace cache_specs)."""
    return f"cache_specs:{device_name}"


def _safe_rollback(conn) -> None:
    """ROLLBACK che non solleva: se la connessione è morta il server ha già rilasciato i lock xact."""
    try:
        conn.rollback()
    except Exception as e:
        logger.error("cache rollback failed: %s", type(e).__name__)


def _get_cached_specs(device_name: str) -> dict | None:
    """Cerca specifiche in cache con TTL 24h e parser_version (sola lettura, senza lock).

    Wrapper retrocompatibile su _select_cached_specs.
    """
    conn = _get_db_connection()
    if not conn:
        return None

    try:
        row = _select_cached_specs(conn, device_name)
        if row:
            logger.info("cache hit device=%s", device_name[:80])
            return row
        logger.info("cache miss device=%s", device_name[:80])
    except Exception as e:
        logger.error("cache read failed: %s device=%s", type(e).__name__, device_name[:80])
    finally:
        conn.close()

    return None


def _resolve_specs_with_lock(
    conn,
    device_name: str,
    fetch: Callable[[str], dict] | None = None,
) -> CacheResolutionResult:
    """Risolve le specifiche con cache + advisory lock per device (una sola fetch per miss concorrenti).

    Flusso (transazioni esplicite, READ COMMITTED):
      1. fast path: SELECT senza lock; HIT -> ritorno immediato. MISS -> ROLLBACK.
      2. SET LOCAL lock_timeout + pg_advisory_xact_lock(hashtextextended(key, 0)).
      3. seconda SELECT obbligatoria dopo il lock (vede il COMMIT di chi ha fetchato prima).
      4. ancora MISS -> una sola fetch live, UPSERT e COMMIT sulla stessa connessione
         (il COMMIT rilascia il lock).

    Ogni eccezione esegue ROLLBACK (che rilascia il lock xact). Errori DB prima della
    fetch -> UNAVAILABLE; errori dopo la fetch non causano mai una seconda fetch.
    """
    if fetch is None:
        fetch = _live_fetch_specs
    log_name = device_name[:80]
    try:
        try:
            conn.autocommit = False

            cached = _select_cached_specs(conn, device_name)
            conn.rollback()
            if cached:
                logger.info("cache hit device=%s", log_name)
                return CacheResolutionResult(CacheStatus.HIT, cached)
            logger.info("cache miss device=%s", log_name)

            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = %s", (_lock_timeout_literal(),))
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (_advisory_lock_key(device_name),),
                )

            cached = _select_cached_specs(conn, device_name)
            if cached:
                conn.rollback()
                logger.info("cache hit after lock device=%s", log_name)
                return CacheResolutionResult(CacheStatus.HIT, cached)
        except psycopg2.errors.LockNotAvailable:
            _safe_rollback(conn)
            logger.warning("cache lock timeout device=%s lock_timeout=%s", log_name, _lock_timeout_literal())
            return CacheResolutionResult(CacheStatus.UNAVAILABLE)
        except psycopg2.Error as e:
            _safe_rollback(conn)
            logger.error("cache unavailable: %s device=%s", type(e).__name__, log_name)
            return CacheResolutionResult(CacheStatus.UNAVAILABLE)
        except Exception:
            _safe_rollback(conn)
            raise

        # Da qui la fetch è tentata una sola volta: nessun percorso porta a una seconda fetch.
        try:
            payload = fetch(device_name)
        except Exception:
            _safe_rollback(conn)
            raise

        if not payload.get("source_url"):
            # source_url è NOT NULL nello schema: i risultati "missing" non si salvano in cache.
            _safe_rollback(conn)
            return CacheResolutionResult(CacheStatus.LIVE_FETCHED, payload)

        try:
            _upsert_cached_specs(
                conn,
                device_name,
                payload["specs"],
                payload["source_url"],
                payload["fetched_at"],
                payload["metadata"],
            )
            conn.commit()
            logger.info("cache write ok device=%s parser_version=%s", log_name, PARSER_VERSION)
        except Exception as e:
            _safe_rollback(conn)
            logger.error("cache write failed: %s device=%s", type(e).__name__, log_name)

        return CacheResolutionResult(CacheStatus.LIVE_FETCHED, payload)
    finally:
        # Garanzia su ogni percorso (anche BaseException): nessuna transazione aperta,
        # quindi nessun advisory lock xact trattenuto oltre questa funzione.
        if not conn.closed and conn.get_transaction_status() != TRANSACTION_STATUS_IDLE:
            _safe_rollback(conn)


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
        with httpx.Client(headers=HEADERS, timeout=FETCH_TIMEOUT) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if len(data) >= 3 and len(data[1]) >= 1:
                title = data[1][0]
                link = data[3][0] if len(data) >= 4 and len(data[3]) >= 1 else None
                return title, link
    except httpx.TimeoutException:
        return None, None  # Timeout: trattato come missing
    except httpx.ConnectError:
        return None, None  # Connect error: trattato come missing
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None, None  # 404: realmente missing
        return None, None  # Altri errori HTTP: trattati come missing
    except Exception:
        return None, None  # Errori imprevisti: trattati come missing
    return None, None


def _fetch_wikipedia_infobox(title: str) -> dict | None:
    """Fetch infobox Wikipedia.

    Restituisce dict (vuoto se la pagina non esiste o non ha revisioni) oppure
    None in caso di errore di rete/HTTP/risposta inattesa.
    """
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
        with httpx.Client(headers=HEADERS, timeout=FETCH_TIMEOUT) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                if int(page_id) < 0:
                    return {}  # Pagina inesistente: risultato legittimo, non errore
                revisions = page_data.get("revisions", [])
                if not revisions:
                    return {}  # Nessuna revisione: risultato legittimo, non errore
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


class SourceUnavailableError(httpx.HTTPError):
    """Sorgente esterna non raggiungibile: mappata a JSON-RPC -32603 "Source unavailable"."""


def _fetch_infobox_or_raise(title: str) -> dict:
    """Come _fetch_wikipedia_infobox, ma un errore (None) solleva SourceUnavailableError.

    Così un errore transitorio non viene mai salvato in cache come specifiche vuote.
    """
    infobox_fields = _fetch_wikipedia_infobox(title)
    if infobox_fields is None:
        raise SourceUnavailableError("wikipedia infobox unavailable")
    return infobox_fields


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
    
    infobox_fields = _fetch_infobox_or_raise(title) if wiki_url else {}
    
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


def _live_fetch_specs(cleaned: str) -> dict:
    """Fetch live da Wikipedia (nessun accesso al DB)."""
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
    
    infobox_fields = _fetch_infobox_or_raise(title)
    
    fields_found = list(infobox_fields.keys())
    fields_missing = [f for f in SPEC_FIELDS if f not in infobox_fields]
    
    if len(infobox_fields) < 3:
        article_type = "family"
    else:
        article_type = "model"
    
    metadata = _build_metadata(article_type, fields_found, fields_missing)
    fetched_at = datetime.now(timezone.utc).isoformat()

    return {
        "specs": infobox_fields,
        "source_url": wiki_url,
        "fetched_at": fetched_at,
        "metadata": metadata,
        "from_cache": False
    }


def get_specs(device_name: str) -> dict:
    """Ottiene specifiche dispositivo con cache trasparente (testo grezzo)."""
    cleaned = (device_name or "").strip()
    if len(cleaned) < 2:
        raise ValueError("device_name must be at least 2 characters")

    conn = _get_db_connection()
    if not conn:
        return _live_fetch_specs(cleaned)

    try:
        result = _resolve_specs_with_lock(conn, cleaned)
    finally:
        conn.close()

    if result.status is CacheStatus.UNAVAILABLE:
        # Degradazione graceful, by design: cache non disponibile (lock_timeout scaduto
        # o errore DB prima della fetch) -> fetch live NON cachata invece di fallire.
        # UNAVAILABLE è restituito solo se nessuna fetch è stata tentata: niente doppia fetch.
        logger.warning("cache unavailable, serving non-cached live fetch device=%s", cleaned[:80])
        return _live_fetch_specs(cleaned)

    return result.payload


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
