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
| display = {{ubl
|'''Pixel 8:'''
|{{convert|157|mm|in|1|abbr=on|order=flip}} [[FHD+]] [[1080p]] [[OLED]] at 428&nbsp;[[Pixels per inch|ppi]]
|{{resx|2400|1080}}&nbsp;px (20:9)
|60-120&nbsp;[[Hertz|Hz]] [[refresh rate]]
|'''Pixel 8 Pro:'''
|{{resx|2992|1344}}&nbsp;px
}}
| rear_camera = ignored
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


def test_infobox_parser_keeps_display_until_next_key():
    fields = extract_infobox_fields(PIXEL_INFOBOX)
    assert "cpu" not in fields
    assert "Pixel 8: 8 GB LPDDR5X" in fields["memory"]
    assert "157 mm" in fields["display"]
    assert "2400 x 1080" in fields["display"]
    assert "2992 x 1344" in fields["display"]
    assert "60-120 Hz refresh rate" in fields["display"]
    assert "rear_camera" not in fields
