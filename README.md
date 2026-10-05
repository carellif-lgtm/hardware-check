# hardware-check

Server MCP minimo che identifica un dispositivo e cita la fonte. Non inventa punteggi di benchmark e non riduce un articolo multi-variante a un solo numero.

Produzione: https://hardware-check-main.vercel.app

## Endpoint verificati

- `GET /` e `GET /api`: health check.
- `POST /mcp`: una richiesta JSON-RPC, risposta JSON. Metodi: `initialize`, `tools/list`, `tools/call`.

Questo non è il trasporto MCP streamable HTTP con sessione SSE. Un client che richiede quel trasporto non si collega senza un adattatore.

Esempio:

```bash
curl -s https://hardware-check-main.vercel.app/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_device","arguments":{"query":"Pixel 8"}}}'
```

## Tool

- `get_device`: nome e URL canonico da Wikipedia OpenSearch.
- `get_specs`: testo dell'infobox Wikipedia per `soc`, `cpu`, `memory`, `storage`, `display`, `battery`, solo se presente. Ogni campo ha `source_url`, `fetched_at` e `confidence`. I campi vuoti sono omessi.

`get_benchmarks` non è esposto. Geekbench Browser risponde 403 e GSMArena risponde con un controllo Cloudflare dalle richieste di Vercel.

## Database

`db/schema.py` descrive tabelle previste. In questa sessione Neon non è un connettore disponibile, quindi non è stato verificato se le tabelle esistono e non è stato scritto alcun dato.
