import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hardware_mcp import extract_infobox_fields, handle_mcp_request

PIXEL_INFOBOX = """
{{Infobox mobile phone
| name = Pixel 8
| soc = [[Google Tensor G3]]
| cpu =
| memory = {{ubl
| '''Pixel 8:''' 8&nbsp;GB [[LPDDR5X]]
| '''Pixel 8 Pro:''' 12&nbsp;GB LPDDR5X
}}
| storage = 128 or 256 GB
| battery = {{ubl
| '''Pixel 8:''' 4575&nbsp;mAh
}}
| display = {{convert|157|mm|in|1}}
| next = ignored
}}
"""


def test_initialize_and_list_do_not_require_network():
    status, initialized = handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert status == 200
    assert initialized["result"]["serverInfo"]["name"] == "hardware-check"
    status, listed = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == ["get_device", "get_specs"]


def test_missing_query_is_an_error_result():
    status, called = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_device", "arguments": {"query": ""}}}
    )
    assert status == 200
    assert called["result"]["isError"] is True


def test_infobox_parser_keeps_variants_and_skips_empty_fields():
    fields = extract_infobox_fields(PIXEL_INFOBOX)
    assert "cpu" not in fields
    assert "Pixel 8: 8 GB LPDDR5X" in fields["memory"]
    assert "Pixel 8 Pro: 12 GB LPDDR5X" in fields["memory"]
    assert fields["soc"] == "Google Tensor G3"
    assert "4575 mAh" in fields["battery"]
