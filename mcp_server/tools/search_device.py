from hardware_mcp import get_device

async def search_device(query: str) -> dict:
    return get_device(query)
