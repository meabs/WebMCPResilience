import json
import re
from typing import Any

from ..browser.webmcp_adapter import WebMCPAdapter
from ..models.scenario import StateProvider


def resolve(value: Any, state: dict[str, Any]) -> Any:
    if isinstance(value, dict): return {key: resolve(item, state) for key, item in value.items()}
    if isinstance(value, list): return [resolve(item, state) for item in value]
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{state\.([\w.]+)\}", value)
        if match:
            current: Any = state
            for part in match.group(1).split('.'):
                current = current[part]
            return current
    return value


async def observe(adapter: WebMCPAdapter, provider: StateProvider, invocation_id: str) -> dict[str, Any]:
    value = await adapter.invoke_tool(provider.tool, {}, invocation_id)
    if isinstance(value, str): value = json.loads(value)
    if provider.path:
        for part in provider.path.split('.'):
            value = value[part]
    if not isinstance(value, dict): raise RuntimeError("state provider must resolve to an object")
    return value
