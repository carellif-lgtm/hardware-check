import json
import traceback

OK_PATHS = {"/", "/api", "/api/index", "/api/health", "/health", "/index"}
MCP_PATHS = {"/mcp", "/api/mcp"}


def _paths(environ):
    values = []
    for key in ("PATH_INFO", "HTTP_X_VERCEL_ORIGINAL_PATH", "HTTP_X_INVOKE_PATH", "HTTP_X_MATCHED_PATH"):
        raw = environ.get(key)
        if not raw:
            continue
        path = raw.split("?", 1)[0].rstrip("/") or "/"
        values.append(path)
    return values or ["/"]


def _response(start_response, status, body):
    encoded = body.encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(encoded))),
        ],
    )
    return [encoded]


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    paths = _paths(environ)

    if method == "POST" or any(path in MCP_PATHS for path in paths):
        if method != "POST":
            return _response(
                start_response,
                "405 Method Not Allowed",
                json.dumps({"error": "POST a JSON-RPC body to /mcp"}),
            )
        length = int(environ.get("CONTENT_LENGTH") or 0)
        raw = environ["wsgi.input"].read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return _response(
                start_response,
                "400 Bad Request",
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}),
            )
        try:
            from hardware_mcp import handle_mcp_request

            status, result = handle_mcp_request(payload)
        except Exception as exc:
            return _response(
                start_response,
                "200 OK",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id") if isinstance(payload, dict) else None,
                        "error": {
                            "code": -32603,
                            "message": f"{type(exc).__name__}: {exc}",
                            "data": traceback.format_exc()[-1200:],
                        },
                    }
                ),
            )
        if result is None:
            start_response("202 Accepted", [("Content-Length", "0")])
            return [b""]
        return _response(start_response, "200 OK", json.dumps(result))

    if any(path in OK_PATHS for path in paths):
        return _response(
            start_response,
            "200 OK",
            json.dumps({"status": "ok", "service": "hardware-check", "phase": "mcp-min", "mcp": "/mcp"}),
        )
    return _response(start_response, "404 Not Found", json.dumps({"error": "Not found"}))
