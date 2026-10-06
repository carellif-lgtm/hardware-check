import json
import math
import os
import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import pytest
from psycopg2.extensions import TRANSACTION_STATUS_IDLE
from psycopg2.extras import Json, RealDictCursor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import hardware_mcp
from hardware_mcp import CacheStatus


DATABASE_URL = os.environ.get("DATABASE_URL")
PARSER_VERSION = "1.2"
DEVICE_NAME = "Pixel 8"
SOURCE_URL = "https://en.wikipedia.org/wiki/Pixel_8"


pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


def utc_now():
    return datetime.now(timezone.utc)


def cache_row(
    *,
    device_name=DEVICE_NAME,
    specs=None,
    source_url=SOURCE_URL,
    fetched_at=None,
    metadata=None,
    expires_at=None,
    parser_version=PARSER_VERSION,
):
    now = utc_now()
    return {
        "device_name": device_name,
        "specs": specs or {"display": "raw"},
        "source_url": source_url,
        "fetched_at": fetched_at or now,
        "metadata": metadata or {"article_type": "model"},
        "expires_at": expires_at or now + timedelta(hours=24),
        "parser_version": parser_version,
    }


def insert_cache_row(conn, row):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cache_specs (
                device_name,
                specs_json,
                source_url,
                fetched_at,
                metadata_json,
                expires_at,
                parser_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["device_name"],
                Json(row["specs"]),
                row["source_url"],
                row["fetched_at"],
                Json(row["metadata"]),
                row["expires_at"],
                row["parser_version"],
            ),
        )


def upsert_cache_row(conn, row):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cache_specs (
                device_name,
                specs_json,
                source_url,
                fetched_at,
                metadata_json,
                expires_at,
                parser_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (device_name)
            DO UPDATE SET
                specs_json = EXCLUDED.specs_json,
                source_url = EXCLUDED.source_url,
                fetched_at = EXCLUDED.fetched_at,
                metadata_json = EXCLUDED.metadata_json,
                expires_at = EXCLUDED.expires_at,
                parser_version = EXCLUDED.parser_version
            """,
            (
                row["device_name"],
                Json(row["specs"]),
                row["source_url"],
                row["fetched_at"],
                Json(row["metadata"]),
                row["expires_at"],
                row["parser_version"],
            ),
        )


def select_valid_cache_row(conn, device_name, parser_version=PARSER_VERSION):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                device_name,
                specs_json,
                source_url,
                fetched_at,
                metadata_json,
                expires_at,
                parser_version
            FROM cache_specs
            WHERE device_name = %s
              AND expires_at > %s
              AND parser_version = %s
            LIMIT 1
            """,
            (device_name, utc_now(), parser_version),
        )
        return cur.fetchone()


@pytest.fixture()
def db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE cache_specs")

    conn.commit()

    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_jsonb_round_trip_with_psycopg2_json_adapter(db):
    specs = {
        "display": "raw infobox text",
        "variants": ["128 GB", "256 GB"],
        "nested": {"refresh_hz": 120},
    }
    metadata = {
        "article_type": "model",
        "fields_found": ["display", "storage"],
    }

    row = cache_row(specs=specs, metadata=metadata)
    insert_cache_row(db, row)
    db.commit()

    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT specs_json, metadata_json
            FROM cache_specs
            WHERE device_name = %s
            """,
            (DEVICE_NAME,),
        )
        result = cur.fetchone()

    assert result is not None
    assert result["specs_json"] == specs
    assert result["metadata_json"] == metadata


def test_upsert_updates_existing_device_without_duplicate(db):
    first = cache_row(
        specs={"display": "first"},
        metadata={"revision": 1},
    )
    second = cache_row(
        specs={"display": "second"},
        metadata={"revision": 2},
    )

    upsert_cache_row(db, first)
    db.commit()

    upsert_cache_row(db, second)
    db.commit()

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM cache_specs
            WHERE device_name = %s
            """,
            (DEVICE_NAME,),
        )
        count = cur.fetchone()[0]

    assert count == 1

    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT specs_json, metadata_json
            FROM cache_specs
            WHERE device_name = %s
            """,
            (DEVICE_NAME,),
        )
        result = cur.fetchone()

    assert result["specs_json"] == {"display": "second"}
    assert result["metadata_json"] == {"revision": 2}


@pytest.mark.parametrize(
    ("row_version", "requested_version", "expires_delta", "expected_hit"),
    [
        ("1.2", "1.2", timedelta(hours=1), True),
        ("1.1", "1.2", timedelta(hours=1), False),
        (None, "1.2", timedelta(hours=1), False),
        ("1.2", "1.2", timedelta(hours=-1), False),
    ],
)
def test_strict_version_and_expiry_filter(
    db,
    row_version,
    requested_version,
    expires_delta,
    expected_hit,
):
    row = cache_row(
        parser_version=row_version,
        expires_at=utc_now() + expires_delta,
    )
    insert_cache_row(db, row)
    db.commit()

    result = select_valid_cache_row(
        db,
        DEVICE_NAME,
        parser_version=requested_version,
    )

    assert (result is not None) is expected_hit


def test_transaction_rollback_removes_failed_insert(db):
    valid = cache_row()
    insert_cache_row(db, valid)
    db.commit()

    with pytest.raises(psycopg2.errors.NotNullViolation):
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cache_specs (
                    device_name,
                    specs_json,
                    source_url,
                    fetched_at,
                    metadata_json,
                    expires_at,
                    parser_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "Broken Device",
                    None,
                    SOURCE_URL,
                    utc_now(),
                    Json({"article_type": "model"}),
                    utc_now() + timedelta(hours=24),
                    PARSER_VERSION,
                ),
            )

    db.rollback()

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM cache_specs
            WHERE device_name = %s
            """,
            ("Broken Device",),
        )
        broken_count = cur.fetchone()[0]

    assert broken_count == 0

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM cache_specs
            WHERE device_name = %s
            """,
            (DEVICE_NAME,),
        )
        valid_count = cur.fetchone()[0]

    assert valid_count == 1


def test_failed_transaction_requires_rollback_before_reuse(db):
    with pytest.raises(psycopg2.errors.NotNullViolation):
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cache_specs (
                    device_name,
                    specs_json,
                    source_url,
                    fetched_at,
                    metadata_json,
                    expires_at,
                    parser_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "Broken Device",
                    None,
                    SOURCE_URL,
                    utc_now(),
                    Json({"article_type": "model"}),
                    utc_now() + timedelta(hours=24),
                    PARSER_VERSION,
                ),
            )

    db.rollback()

    with db.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1


# --- P0.2: advisory-lock cache resolution -----------------------------------


@pytest.fixture()
def connect():
    """Factory di connessioni aggiuntive, chiuse a fine test."""
    conns = []

    def _connect():
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        conns.append(conn)
        return conn

    try:
        yield _connect
    finally:
        for conn in conns:
            if not conn.closed:
                conn.rollback()
                conn.close()


class CountingFetch:
    """Fetch finta thread-safe che conta le chiamate."""

    def __init__(self, delay=0.0, error=None, source_url=SOURCE_URL):
        self.count = 0
        self._lock = threading.Lock()
        self._delay = delay
        self._error = error
        self._source_url = source_url

    def __call__(self, device_name):
        with self._lock:
            self.count += 1
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return {
            "specs": {"display": "live"},
            "source_url": self._source_url,
            "fetched_at": utc_now().isoformat(),
            "metadata": {"article_type": "model"},
            "from_cache": False,
        }


def hold_advisory_lock(conn, device_name=DEVICE_NAME):
    """Acquisisce il lock xact del device su ``conn`` lasciando la transazione aperta."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (hardware_mcp._advisory_lock_key(device_name),),
        )


def advisory_lock_is_free(conn, device_name=DEVICE_NAME):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
            (hardware_mcp._advisory_lock_key(device_name),),
        )
        acquired = cur.fetchone()[0]
    conn.rollback()
    return acquired


def count_rows(conn, device_name=DEVICE_NAME):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM cache_specs WHERE device_name = %s",
            (device_name,),
        )
        count = cur.fetchone()[0]
    conn.rollback()
    return count


def test_concurrent_miss_fetches_once(db, connect):
    conns = [connect(), connect()]
    fetch = CountingFetch(delay=0.5)
    barrier = threading.Barrier(2)
    results = [None, None]
    errors = []

    def worker(index):
        try:
            barrier.wait(timeout=5)
            results[index] = hardware_mcp._resolve_specs_with_lock(
                conns[index], DEVICE_NAME, fetch=fetch
            )
        except Exception as exc:  # pragma: no cover - surfaced by assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert fetch.count == 1
    assert sorted(r.payload["from_cache"] for r in results) == [False, True]
    assert {r.status for r in results} == {CacheStatus.LIVE_FETCHED, CacheStatus.HIT}

    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT parser_version FROM cache_specs WHERE device_name = %s",
            (DEVICE_NAME,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["parser_version"] == "1.2"


def test_failed_fetch_rolls_back_and_releases_lock(db, connect):
    conn = connect()
    fetch = CountingFetch(error=RuntimeError("wikipedia down"))

    with pytest.raises(RuntimeError, match="wikipedia down"):
        hardware_mcp._resolve_specs_with_lock(conn, DEVICE_NAME, fetch=fetch)

    assert fetch.count == 1
    assert conn.get_transaction_status() == TRANSACTION_STATUS_IDLE
    assert count_rows(db) == 0
    assert advisory_lock_is_free(connect()) is True

    retry = CountingFetch()
    result = hardware_mcp._resolve_specs_with_lock(conn, DEVICE_NAME, fetch=retry)
    assert result.status is CacheStatus.LIVE_FETCHED
    assert retry.count == 1
    assert count_rows(db) == 1


def test_hit_skips_lock_and_fetch(db, connect, monkeypatch):
    insert_cache_row(db, cache_row())
    db.commit()

    holder = connect()
    hold_advisory_lock(holder)
    # Se il fast path prendesse il lock, scadrebbe il timeout -> UNAVAILABLE.
    monkeypatch.setattr(hardware_mcp, "CACHE_LOCK_TIMEOUT_S", 0.2)

    conn = connect()
    fetch = CountingFetch()
    result = hardware_mcp._resolve_specs_with_lock(conn, DEVICE_NAME, fetch=fetch)

    assert result.status is CacheStatus.HIT
    assert result.payload["from_cache"] is True
    assert result.payload["source_url"] == SOURCE_URL
    assert fetch.count == 0
    assert conn.get_transaction_status() == TRANSACTION_STATUS_IDLE


def test_lock_timeout_is_unavailable_and_never_fetches(db, connect, monkeypatch):
    holder = connect()
    hold_advisory_lock(holder)
    monkeypatch.setattr(hardware_mcp, "CACHE_LOCK_TIMEOUT_S", 0.2)

    conn = connect()
    fetch = CountingFetch()
    started = time.monotonic()
    result = hardware_mcp._resolve_specs_with_lock(conn, DEVICE_NAME, fetch=fetch)
    elapsed = time.monotonic() - started

    assert result.status is CacheStatus.UNAVAILABLE
    assert result.payload is None
    assert fetch.count == 0
    assert elapsed < 5
    assert conn.get_transaction_status() == TRANSACTION_STATUS_IDLE
    assert count_rows(db) == 0

    # SET LOCAL non deve sopravvivere alla transazione.
    with conn.cursor() as cur:
        cur.execute("SHOW lock_timeout")
        assert cur.fetchone()[0] == "0"
    conn.rollback()


def test_persist_failure_after_fetch_never_refetches(db, connect, monkeypatch):
    conn = connect()
    fetch = CountingFetch()

    def failing_upsert(conn, *args, **kwargs):
        with conn.cursor() as cur:
            cur.execute("SELECT 1/0")

    monkeypatch.setattr(hardware_mcp, "_get_db_connection", lambda: conn)
    monkeypatch.setattr(hardware_mcp, "_live_fetch_specs", fetch)
    monkeypatch.setattr(hardware_mcp, "_upsert_cached_specs", failing_upsert)

    result = hardware_mcp.get_specs(DEVICE_NAME)

    assert fetch.count == 1
    assert result["from_cache"] is False
    assert result["specs"] == {"display": "live"}
    assert conn.closed
    assert count_rows(db) == 0
    assert advisory_lock_is_free(connect()) is True



def test_base_exception_during_fetch_releases_lock(db, connect):
    conn = connect()
    fetch = CountingFetch(error=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        hardware_mcp._resolve_specs_with_lock(conn, DEVICE_NAME, fetch=fetch)

    assert fetch.count == 1
    assert conn.get_transaction_status() == TRANSACTION_STATUS_IDLE
    assert count_rows(db) == 0
    assert advisory_lock_is_free(connect()) is True


def test_infobox_error_is_source_unavailable_and_not_cached(db, connect, monkeypatch):
    conn = connect()
    monkeypatch.setattr(hardware_mcp, "_get_db_connection", lambda: conn)
    monkeypatch.setattr(
        hardware_mcp,
        "_fetch_wikipedia_opensearch",
        lambda query: ("Pixel 8", SOURCE_URL),
    )
    monkeypatch.setattr(hardware_mcp, "_fetch_wikipedia_infobox", lambda title: None)

    response = json.loads(
        hardware_mcp.handle_mcp_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "get_specs",
                        "arguments": {"device_name": DEVICE_NAME},
                    },
                }
            )
        )
    )

    assert "result" not in response
    assert response["error"]["code"] == -32603
    assert response["error"]["message"].startswith("Source unavailable")
    assert conn.closed
    assert count_rows(db) == 0
    assert advisory_lock_is_free(connect()) is True


# --- Wikipedia fetch error contracts (no network: httpx.MockTransport) --------


def use_mock_transport(monkeypatch, handler):
    real_client = httpx.Client

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(hardware_mcp.httpx, "Client", client_factory)


def raise_timeout(request):
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    "pages",
    [
        {"-1": {"title": "Nope", "missing": ""}},
        {"123": {"title": "Pixel 8"}},
    ],
    ids=["missing-page", "no-revisions"],
)
def test_infobox_not_found_is_empty_dict(monkeypatch, pages):
    use_mock_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"query": {"pages": pages}}),
    )
    assert hardware_mcp._fetch_wikipedia_infobox("Pixel 8") == {}


@pytest.mark.parametrize(
    "handler",
    [lambda request: httpx.Response(500), raise_timeout],
    ids=["http-500", "timeout"],
)
def test_infobox_error_is_none(monkeypatch, handler):
    use_mock_transport(monkeypatch, handler)
    assert hardware_mcp._fetch_wikipedia_infobox("Pixel 8") is None
    with pytest.raises(hardware_mcp.SourceUnavailableError):
        hardware_mcp._fetch_infobox_or_raise("Pixel 8")


@pytest.mark.parametrize(
    "handler",
    [lambda request: httpx.Response(500), raise_timeout],
    ids=["http-500", "timeout"],
)
def test_opensearch_error_returns_none_pair(monkeypatch, handler):
    use_mock_transport(monkeypatch, handler)
    assert hardware_mcp._fetch_wikipedia_opensearch("Pixel 8") == (None, None)


# --- Env timeout validation ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 15.0),
        ("", 15.0),
        ("   ", 15.0),
        ("abc", 15.0),
        ("0", 15.0),
        ("-1", 15.0),
        ("nan", 15.0),
        ("inf", 15.0),
        ("-inf", 15.0),
        ("2.5", 2.5),
    ],
)
def test_env_positive_float_validation(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CACHE_LOCK_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("CACHE_LOCK_TIMEOUT_S", raw)

    value = hardware_mcp._env_positive_float("CACHE_LOCK_TIMEOUT_S", 15.0)

    assert value == expected
    assert math.isfinite(value) and value > 0


@pytest.mark.parametrize(
    ("seconds", "literal"),
    [(15.0, "15000ms"), (0.2, "200ms"), (0.0001, "1ms")],
)
def test_lock_timeout_literal_is_milliseconds(monkeypatch, seconds, literal):
    monkeypatch.setattr(hardware_mcp, "CACHE_LOCK_TIMEOUT_S", seconds)
    assert hardware_mcp._lock_timeout_literal() == literal


def test_fetch_timeout_uses_env_defaults():
    timeout = hardware_mcp.FETCH_TIMEOUT
    assert timeout.connect == hardware_mcp.FETCH_CONNECT_TIMEOUT_S
    assert timeout.read == hardware_mcp.FETCH_READ_TIMEOUT_S
    assert timeout.write == hardware_mcp.FETCH_WRITE_TIMEOUT_S
    assert timeout.pool == hardware_mcp.FETCH_POOL_TIMEOUT_S
