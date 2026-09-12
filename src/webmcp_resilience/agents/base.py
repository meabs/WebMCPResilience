"""Optional agent boundary. Core scenarios remain deterministic and LLM-free."""
from abc import ABC, abstractmethod
from typing import Any


class AgentAdapter(ABC):
    @abstractmethod
    async def act(self, tools: list[dict[str, Any]], instruction: str) -> list[dict[str, Any]]:
        """Return requested tool calls as {name, arguments}; provider adapters implement this."""


class DeterministicAgent(AgentAdapter):
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def act(self, tools: list[dict[str, Any]], instruction: str) -> list[dict[str, Any]]:
        available = {tool["name"] for tool in tools}
        return [call for call in self.calls if call.get("name") in available]
