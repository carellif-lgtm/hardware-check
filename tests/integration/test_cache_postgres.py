import os
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from psycopg2.extras import Json, RealDictCursor


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
