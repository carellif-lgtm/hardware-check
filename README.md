# hardware-check

Health check pubblico e server MCP minimo per identificare un dispositivo e citare la fonte.

Non inventa punteggi di benchmark. Se la fonte non restituisce un URL, il tool fallisce.

## Endpoint

- `GET /` e `GET /api`: health check.
- `POST /mcp`: JSON-RPC MCP (`initialize`, `tools/list`, `tools/call`).

Produzione: https://hardware-check-main.vercel.app

## Tool

`get_device` accetta `query` e interroga l'API OpenSearch di Wikipedia. Restituisce nome, URL canonico, `fetched_at` e `confidence`.

GSMArena risponde con un controllo Cloudflare dalle richieste di Vercel, quindi non è usabile come fonte server-side. Geekbench Browser risponde 403. I punteggi non vengono stimati.

## Database

`db/schema.py` descrive le tabelle previste. Non sono state create: Neon non è tra i connettori disponibili e non c'è ancora un dato di benchmark reale da salvare.
