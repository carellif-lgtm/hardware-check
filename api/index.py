import json
import os
import sys
from typing import Any

# Aggiungi parent directory al path per importare hardware_mcp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hardware_mcp import handle_mcp_request


def handler(environ: dict, start_response: callable) -> list[bytes]:
    """WSGI handler per Vercel."""
    path = environ.get("PATH_INFO", "/").rstrip("/") or "/"
    method = environ.get("REQUEST_METHOD", "GET")
    
    # Health check
    if path == "/" or path == "/health" or path == "/api/health":
        if method == "GET":
            body = json.dumps({
                "status": "ok",
                "service": "hardware-check",
                "version": "1.1.3"
            })
            start_response("200 OK", [("Content-Type", "application/json")])
            return [body.encode("utf-8")]
        else:
            start_response("405 Method Not Allowed", [("Content-Type", "application/json")])
            return [b'{"error": "Method not allowed"}']
    
    # MCP endpoint
    if path == "/mcp" or path == "/api/mcp":
        if method == "POST":
            try:
                content_length = int(environ.get("CONTENT_LENGTH", 0))
                body_bytes = environ["wsgi.input"].read(content_length)
                request_body = body_bytes.decode("utf-8")
                
                # handle_mcp_request accetta stringa e restituisce stringa
                response_body = handle_mcp_request(request_body, environ)
                
                start_response("200 OK", [("Content-Type", "application/json")])
                return [response_body.encode("utf-8")]
            
            except Exception as e:
                error_response = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Internal error: {e}"}
                })
                start_response("200 OK", [("Content-Type", "application/json")])
                return [error_response.encode("utf-8")]
        else:
            start_response("405 Method Not Allowed", [("Content-Type", "application/json")])
            return [b'{"error": "Method not allowed"}']
    
    # 404 per altri path
    start_response("404 Not Found", [("Content-Type", "application/json")])
    return [b'{"error": "Not found"}']


# Per esecuzione locale con wsgiref
if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    server = make_server("localhost", 8000, handler)
    print("Serving on http://localhost:8000")
    server.serve_forever()
