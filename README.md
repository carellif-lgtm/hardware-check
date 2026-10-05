# hardware-check

Health check pubblico e server MCP minimo per identificare un dispositivo e citare la fonte.

Non inventa punteggi di benchmark. Se la fonte non restituisce un URL, il tool fallisce.

## Endpoint

- `GET /` e `GET /api`: health check.
- `POST /mcp`: JSON-RPC MCP (`initialize`, `tools/list`, `tools/call`).

Produzione: https://hardware-check-main.vercel.app

## Tool

`get_device` accetta `query` e interroga GSMArena. Restituisce nome, URL canonico, `fetched_at` e `confidence`. Non restituisce Geekbench, 3DMark o altre misure non lette dalla pagina.

## Database

`db/schema.py` descrive le tabelle previste. Non sono state create: il connettore Neon non è disponibile in questa sessione e non c'è ancora un dato reale da salvare.
