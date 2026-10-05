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
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_specs","arguments":{"query":"Pixel 8"}}}'
```

## Tool

- `get_device`: nome e URL canonico da Wikipedia OpenSearch.
- `get_specs`: testo dell'infobox Wikipedia per `soc`, `cpu`, `memory`, `storage`, `display`, `battery`, solo se presente. Ogni campo ha `source_url`, `fetched_at` e `confidence`. I campi vuoti sono omessi.

Il display non è normalizzato. Per Pixel 8, verificato il 5 ottobre 2026, il valore è il testo grezzo delle due varianti:

```text
Pixel 8: | 157 mm FHD+ 1080p OLED at 428 ppi | 2400 x 1080 px (20:9) | 60-120 Hz refresh rate | Pixel 8 Pro: | 170 mm QHD+ 1440p LTPO OLED at 489 ppi | 2992 x 1344 px (20:9) | 1-120 Hz refresh rate | Both: HDR
```

`157 mm` è il primo valore del template `convert`, non una conversione in pollici. Non viene prodotto un unico `size_in` perché l'articolo copre Pixel 8 e Pixel 8 Pro.

## Benchmark

Nessun benchmark è supportato. `get_benchmarks` non è esposto. Geekbench Browser risponde 403. GSMArena risponde con una pagina di controllo Cloudflare, non con un risultato dispositivo. Un punteggio assente non viene stimato.

## Database

`db/schema.py` descrive tabelle previste. Neon non è tra i connettori di questa sessione, quindi le tabelle non sono state verificate e nessun dato è stato scritto. La persistenza richiede una connection string impostata come variabile d'ambiente, non nel repository.
