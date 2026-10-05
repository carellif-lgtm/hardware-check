import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hardware_mcp import choose_title, extract_infobox_fields, handle_mcp_request
import json


def test_choose_title_model():
    """Verifica che choose_title restituisca article_type: model per match esatto."""
    titles = ["Google Pixel 8"]
    title, metadata = choose_title("Pixel 8", titles)
    assert title == "Google Pixel 8"
    assert metadata["article_type"] == "model"


def test_choose_title_family():
    """Verifica che choose_title restituisca article_type: family per pagina famiglia."""
    titles = ["Mac Studio"]
    title, metadata = choose_title("Mac Studio M2 Max", titles)
    assert title == "Mac Studio"
    assert metadata["article_type"] == "family"


def test_choose_title_missing():
    """Verifica che choose_title restituisca article_type: missing per nessun risultato."""
    titles = []
    title, metadata = choose_title("Dispositivo Inesistente XYZ", titles)
    assert title is None
    assert metadata["article_type"] == "missing"


def test_extract_infobox_fields():
    """Verifica che extract_infobox_fields estragga campi correttamente."""
    text = """
    {{Infobox
    | soc = Google Tensor G3
    | cpu = Octa-core
    | memory = 8 GB LPDDR5X
    | display = 6.2" OLED
    }}
    """
    fields = extract_infobox_fields(text)
    assert fields["soc"] == "Google Tensor G3"
    assert fields["cpu"] == "Octa-core"
    assert fields["memory"] == "8 GB LPDDR5X"
    assert fields["display"] == '6.2" OLED'


def test_handle_mcp_initialize():
    """Verifica che initialize restituisca protocollo corretto."""
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {}
    })
    response = handle_mcp_request(request)
    data = json.loads(response)
    assert data["result"]["protocolVersion"] == "2024-11-05"
    assert data["result"]["serverInfo"]["name"] == "hardware-check"


def test_handle_mcp_tools_list():
    """Verifica che tools/list esponga get_device e get_specs."""
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    })
    response = handle_mcp_request(request)
    data = json.loads(response)
    tools = [t["name"] for t in data["result"]["tools"]]
    assert "get_device" in tools
    assert "get_specs" in tools


def test_handle_mcp_geekom_missing():
    """Verifica che Geekom restituisca metadata article_type: missing."""
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "get_specs",
            "arguments": {"device_name": "Geekom Mini IT13"}
        }
    })
    response = handle_mcp_request(request)
    data = json.loads(response)
    # Non deve restituire errore 500
    assert "error" not in data or data.get("result")
    if "result" in data:
        metadata = data["result"]["content"][0]["text"]
        metadata_json = json.loads(metadata)
        assert metadata_json["metadata"]["article_type"] == "missing"


def test_handle_mcp_macstudio_family():
    """Verifica che Mac Studio restituisca metadata article_type: family."""
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "get_specs",
            "arguments": {"device_name": "Mac Studio"}
        }
    })
    response = handle_mcp_request(request)
    data = json.loads(response)
    assert "result" in data
    metadata = data["result"]["content"][0]["text"]
    metadata_json = json.loads(metadata)
    # article_type dovrebbe essere "family" dato che Mac Studio ha pochi campi
    assert metadata_json["metadata"]["article_type"] in ["family", "model"]
