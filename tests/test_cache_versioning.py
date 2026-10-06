"""Strict cache versioning: _get_cached_specs must only hit rows written by the
current PARSER_VERSION.

No real Neon connection is used. The real SQL text issued by the code is run
against an in-memory SQLite table (``%s`` placeholders translated to ``?``), so
the WHERE clause is exercised for real, not mimicked.
"""
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import hardware_mcp
from hardware_mcp import _get_cached_specs

DEVICE = "Pixel 8"
TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _adapt(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime(TS_FORMAT)
    return value


class _FakeCursor:
    def __init__(self, db, calls):
        self._db = db
        self._calls = calls
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._calls.append((sql, params))
        self._result = self._db.execute(
            sql.replace("%s", "?"), tuple(_adapt(p) for p in params)
        )

    def fetchone(self):
        row = self._result.fetchone()
        if row is None:
            return None
        return {
            "specs_json": row[0],
            "source_url": row[1],
            "fetched_at": datetime.strptime(row[2], TS_FORMAT).replace(tzinfo=timezone.utc),
            "metadata_json": row[3],
        }


class _FakeConnection:
    def __init__(self, db, calls):
        self._db = db
        self._calls = calls

    def cursor(self, **_kwargs):
        return _FakeCursor(self._db, self._calls)

    def close(self):
        pass


def _run(row_version, expires_delta=timedelta(hours=1), parser_version=None):
    """Seed one cache row with ``row_version`` and call _get_cached_specs.

    Returns (result, executed_calls)."""
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE cache_specs (device_name TEXT, specs_json TEXT, source_url TEXT,"
        " fetched_at TEXT, metadata_json TEXT, expires_at TEXT, parser_version TEXT)"
    )
    now = datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO cache_specs VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            DEVICE,
            '{"display": "raw"}',
            "https://en.wikipedia.org/wiki/Pixel_8",
            now.strftime(TS_FORMAT),
            '{"article_type": "model"}',
            (now + expires_delta).strftime(TS_FORMAT),
            row_version,
        ),
    )
    calls = []
    with patch("hardware_mcp._get_db_connection", return_value=_FakeConnection(db, calls)):
        if parser_version is None:
            result = _get_cached_specs(DEVICE)
        else:
            with patch("hardware_mcp.PARSER_VERSION", parser_version):
                result = _get_cached_specs(DEVICE)
    return result, calls


def test_same_version_is_hit():
    assert hardware_mcp.PARSER_VERSION == "1.2"
    result, _ = _run("1.2")
    assert result is not None
    assert result["from_cache"] is True
    assert result["source_url"] == "https://en.wikipedia.org/wiki/Pixel_8"


def test_older_version_is_miss():
    result, _ = _run("1.1")
    assert result is None


def test_null_version_is_miss():
    result, _ = _run(None)
    assert result is None


def test_expired_row_with_current_version_is_miss():
    result, _ = _run("1.2", expires_delta=timedelta(hours=-1))
    assert result is None


def test_parser_version_is_passed_to_query():
    _, calls = _run("1.2")
    assert len(calls) == 1
    sql, params = calls[0]
    assert params[0] == DEVICE
    assert params[2] == hardware_mcp.PARSER_VERSION


def test_query_has_strict_equality_and_no_null_fallback():
    _, calls = _run("1.2")
    sql, _ = calls[0]
    assert "parser_version = %s" in sql
    assert "IS NULL" not in sql.upper()


def test_hit_follows_the_current_parser_version():
    # A row written by 1.2 stops being a hit as soon as the parser moves on.
    result, calls = _run("1.2", parser_version="1.3")
    assert result is None
    assert calls[0][1][2] == "1.3"
