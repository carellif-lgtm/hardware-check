-- Tabella cache_specs per cache trasparente delle specifiche
CREATE TABLE IF NOT EXISTS cache_specs (
    device_name TEXT PRIMARY KEY,
    specs_json JSONB NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    metadata_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Indice per scadenza (ottimizza query TTL)
CREATE INDEX IF NOT EXISTS idx_cache_specs_expires ON cache_specs(expires_at);
