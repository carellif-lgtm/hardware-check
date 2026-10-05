import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hardware_mcp import handle_mcp_request


def test_initialize_and_list_do_not_require_network():
    status, initialized = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert status == 200
    assert initialized["result"]["serverInfo"]["name"] == "hardware-check"

    status, listed = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert status == 200
    assert listed["result"]["tools"][0]["name"] == "get_device"


def test_missing_query_is_an_error_result():
    status, called = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_device", "arguments": {"query": ""}},
        }
    )
    assert status == 200
    assert called["result"]["isError"] is True
    assert "2 characters" in called["result"]["content"][0]["text"]
