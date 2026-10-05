import pathlib
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hardware_mcp import choose_title, extract_infobox_fields, handle_mcp_request
import json
import httpx


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


def test_extract_infobox_fields_with_nested_templates():
    """Verifica che extract_infobox_fields gestisca template annidati (caso reale)."""
    # Infobox REALE con template annidati {{convert}}, {{ubl}}, ecc.
    text = """
{{Infobox mobile phone
| name = Google Pixel 8
| brand = Google
| soc = [[Google Tensor G3|Google Tensor G3]]
| cpu = {{ubl|2×2.85 GHz Cortex-X3|4×2.35 GHz Cortex-A715|4×1.80 GHz Cortex-A510}}
| memory = 8 GB LPDDR5X
| storage = {{ubl|128 GB|256 GB|512 GB}}
| display = {{convert|157|mm|in|1|abbr=on}} FHD+ 1080p OLED at 428 ppi
| battery = {{ubl|Li-Po|4575 mAh}}
}}
"""
    fields = extract_infobox_fields(text)
    
    # Verifica che i campi siano estratti correttamente
    assert "soc" in fields
    assert "memory" in fields
    assert "display" in fields
    assert "battery" in fields
    
    # Verifica che i valori contengano i template annidati (testo grezzo)
    assert "Google Tensor G3" in fields["soc"]
    assert "8 GB LPDDR5X" in fields["memory"]
    assert "{{convert|157|mm|in|1|abbr=on}}" in fields["display"]
    assert "4575 mAh" in fields["battery"]


def test_extract_infobox_fields_real_pixel8():
    """Verifica con infobox ancora più realistico (simile a Wikipedia reale)."""
    text = """
{{Infobox mobile phone
| name = Pixel 8
| image = Google Pixel 8 front.svg
| caption = Pixel 8
| brand = Google
| manufacturer = [[Foxconn]]
| type = [[Smartphone]]
| generation = 8th-generation Pixel
| soc = [[Google Tensor G3]]
| cpu = {{ubl|2×2.85 GHz Cortex-X3|4×2.35 GHz Cortex-A715|4×1.80 GHz Cortex-A510}}
| gpu = [[ARM Mali-G715|Immortalis-G715s]]
| memory = 8 GB LPDDR5X
| storage = {{ubl|128 GB|256 GB|512 GB}}
| battery = {{ubl|Li-Po|4575 mAh}}
| display = {{convert|157|mm|in|1|abbr=on}} FHD+ 1080p OLED at 428 ppi
}}
"""
    fields = extract_infobox_fields(text)
    
    # Tutti i campi principali devono essere presenti
    assert fields.get("soc") is not None
    assert fields.get("memory") is not None
    assert fields.get("display") is not None
    assert fields.get("battery") is not None
    assert fields.get("storage") is not None
    
    # I valori devono essere testo grezzo (con template)
    assert "{{ubl" in fields["memory"] or "8 GB" in fields["memory"]
    assert "{{convert" in fields["display"] or "157" in fields["display"]


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


@patch('hardware_mcp._fetch_wikipedia_opensearch')
@patch('hardware_mcp._fetch_wikipedia_infobox')
def test_handle_mcp_geekom_missing(mock_infobox, mock_opensearch):
    """Verifica che Geekom restituisca metadata article_type: missing (mock, no rete)."""
    # Mock: Wikipedia risponde con pagina non trovata (404)
    mock_opensearch.return_value = (None, None)
    mock_infobox.return_value = {}
    
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
    assert "result" in data
    metadata = data["result"]["content"][0]["text"]
    metadata_json = json.loads(metadata)
    assert metadata_json["metadata"]["article_type"] == "missing"


@patch('hardware_mcp._fetch_wikipedia_opensearch')
@patch('hardware_mcp._fetch_wikipedia_infobox')
def test_handle_mcp_macstudio_family(mock_infobox, mock_opensearch):
    """Verifica che Mac Studio restituisca metadata article_type: family (mock, no rete)."""
    # Mock: Wikipedia risponde con pagina famiglia (pochi campi)
    mock_opensearch.return_value = ("Mac Studio", "https://en.wikipedia.org/wiki/Mac_Studio")
    mock_infobox.return_value = {"soc": "Apple M series"}  # Solo soc, pochi campi
    
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
    # article_type dovrebbe essere "family" dato che ha solo 1 campo
    assert metadata_json["metadata"]["article_type"] == "family"


@patch('hardware_mcp._fetch_wikipedia_opensearch')
def test_network_timeout_returns_missing(mock_opensearch):
    """Verifica che timeout di rete restituisca article_type: missing (non errore)."""
    # Mock: timeout di rete
    mock_opensearch.side_effect = httpx.TimeoutException("Request timed out")
    
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "get_specs",
            "arguments": {"device_name": "Pixel 8"}
        }
    })
    response = handle_mcp_request(request)
    data = json.loads(response)
    
    # Timeout: trattato come missing (articolo non trovato)
    assert "result" in data
    metadata = data["result"]["content"][0]["text"]
    metadata_json = json.loads(metadata)
    assert metadata_json["metadata"]["article_type"] == "missing"


@patch('hardware_mcp._fetch_wikipedia_opensearch')
def test_network_connect_error_returns_missing(mock_opensearch):
    """Verifica che connect error restituisca article_type: missing (non errore)."""
    # Mock: connect error
    mock_opensearch.side_effect = httpx.ConnectError("Connection refused")
    
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "get_specs",
            "arguments": {"device_name": "Pixel 8"}
        }
    })
    response = handle_mcp_request(request)
    data = json.loads(response)
    
    # Connect error: trattato come missing
    assert "result" in data
    metadata = data["result"]["content"][0]["text"]
    metadata_json = json.loads(metadata)
    assert metadata_json["metadata"]["article_type"] == "missing"
