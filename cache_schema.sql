-- Tabella cache_specs per cache trasparente delle specifiche
-- Versione: 1.2 (con parser_version per invalidazione)

CREATE TABLE IF NOT EXISTS cache_specs (
    device_name TEXT PRIMARY KEY,
    specs_json JSONB NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    metadata_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    parser_version TEXT DEFAULT '1.2'
);

-- Indice per scadenza (ottimizza query TTL)
CREATE INDEX IF NOT EXISTS idx_cache_specs_expires ON cache_specs(expires_at);

-- Indice per parser_version (ottimizza invalidazione)
CREATE INDEX IF NOT EXISTS idx_cache_specs_parser_version ON cache_specs(parser_version);

-- Nota: dopo ALTER TABLE in produzione, eseguire:
-- UPDATE cache_specs SET parser_version = '1.0-legacy' WHERE parser_version IS NULL;
