from typing import Any


def handler(request: Any) -> dict:
    path = getattr(request, "path", "/").split("?", 1)[0]
    if path in ("/", "/api/health"):
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json; charset=utf-8"},
            "body": '{"status":"ok","service":"hardware-check"}',
        }
    return {
        "statusCode": 404,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": '{"error":"Not found"}',
    }
