import json


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")

    if path in ("/", "/api/health"):
        body = json.dumps({"status": "ok", "service": "hardware-check"}).encode("utf-8")
        start_response(
            "200 OK",
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    body = json.dumps({"error": "Not found"}).encode("utf-8")
    start_response(
        "404 Not Found",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]
