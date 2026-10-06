# hardware-check

MCP server per identificare dispositivi hardware e ottenere specifiche da Wikipedia, senza inventare dati.

## Principi

- **Meglio nessun dato che dati inventati**: se Wikipedia non ha la pagina, restituisci errore strutturato, non inventare specifiche.
- **Trasparenza totale**: ogni risposta include `source_url`, `fetched_at` e `confidence`.
- **Testo grezzo**: il campo `display` è il testo esatto dall'infobox Wikipedia, senza normalizzazioni (es. "157 mm" non diventa "6.2 pollici").
- **Nessun benchmark**: i benchmark (Geekbench, ecc.) non sono inclusi perché le fonti sono bloccate (403/Cloudflare).

## Tool

- `get_device`: nome e URL canonico da Wikipedia OpenSearch.
- `get_specs`: testo dell'infobox Wikipedia per `soc`, `cpu`, `memory`, `storage`, `display`, `battery`, solo se presente. Ogni campo ha `source_url`, `fetched_at` e `confidence`. I campi vuoti sono omessi.

Il display non è normalizzato. Per Pixel 8, verificato il 5 ottobre 2026, il valore è il testo grezzo delle due varianti:
- `"157 mm FHD+ 1080p OLED at 428 ppi"` o simile (dipende dall'infobox)
- NON convertito in "6.2 pollici"

## Versioni

| Variabile | Valore | Descrizione |
|---|---|---|
| `SERVER_VERSION` | `1.2.0` | Versione server MCP/API |
| `PARSER_VERSION` | `1.2` | Versione parser/cache |
| `PROTOCOL_VERSION` | `2024-11-05` | Versione protocollo MCP |

### Verifica live

```bash
curl -s https://hardware-check-main.vercel.app/ | jq .
```

Il campo `version` dell'health check coincide con `SERVER_VERSION`.

## Esempi

### Health check

```bash
curl -s https://hardware-check-main.vercel.app/ | jq
```

### Initialize

```bash
curl -s -X POST https://hardware-check-main.vercel.app/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | jq
```

### get_device

```bash
curl -s -X POST https://hardware-check-main.vercel.app/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_device","arguments":{"query":"Pixel 8"}}}' | jq
```

### get_specs

```bash
curl -s -X POST https://hardware-check-main.vercel.app/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_specs","arguments":{"device_name":"Pixel 8"}}}' | jq
```

### get_specs con errore (pagina mancante)

```bash
curl -s -X POST https://hardware-check-main.vercel.app/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_specs","arguments":{"device_name":"Geekom Mini IT13"}}}' | jq
```

Output atteso: `metadata.article_type = "missing"` (nessuna pagina Wikipedia dedicata).

## Struttura del database (Neon)

- `devices`: lista dispositivi identificati (vuota all'inizio).
- `device_specs`: specifiche estratte (vuota all'inizio).
- `device_benchmarks`: non usata (i benchmark non sono supportati).
- `cache_specs`: cache trasparente delle risposte `get_specs` con TTL 24h.

## Limiti noti

- **Geekom Mini IT13**: nessuna pagina Wikipedia dedicata → errore strutturato, non un bug.
- **Mac Studio M2 Max**: pagina Wikipedia della famiglia "Mac Studio" → solo `system_on_chip: Apple M series`, altri campi assenti.
- **Wikipedia down**: se Wikipedia non risponde, errore `-32603` con messaggio "Network error: Wikipedia unreachable", NON `article_type: missing`.

## Sviluppo

```bash
# Installa dipendenze
pip install -r requirements.txt

# Esegui test (alcuni richiedono rete)
python3 -m pytest tests -v
```

## License

MIT
