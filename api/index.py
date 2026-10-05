import json

OK_PATHS = {
    "/",
    "/api",
    "/api/index",
    "/api/health",
    "/health",
    "/index",
}


def _path(environ):
    raw = environ.get("PATH_INFO") or "/"
    path = raw.rstrip("/") or "/"
    original = environ.get("HTTP_X_VERCEL_ORIGINAL_PATH") or environ.get("HTTP_X_INVOKE_PATH")
    if original:
        original = original.split("?", 1)[0].rstrip("/") or "/"
    return path, original


def app(environ, start_response):
    path, original = _path(environ)
    if path in OK_PATHS or original in OK_PATHS:
        body = json.dumps(
            {
                "status": "ok",
                "service": "hardware-check",
                "phase": "healthcheck",
            }
        ).encode("utf-8")
        status = "200 OK"
    else:
        body = json.dumps({"error": "Not found"}).encode("utf-8")
        status = "404 Not Found"
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]
