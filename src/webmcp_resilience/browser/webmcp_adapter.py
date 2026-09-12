from pathlib import Path
import asyncio
from typing import Any

from playwright.async_api import Page


class WebMCPUnavailable(RuntimeError):
    pass


class WebMCPAdapter:
    """The sole browser-facing WebMCP boundary used by the Python engine."""

    def __init__(self, page: Page, state_script: str | None = None, from_origins: list[str] | None = None) -> None:
        self.page, self.state_script, self.from_origins = page, state_script, from_origins or []

    async def install(self) -> None:
        source = Path(__file__).with_name("adapter.js").read_text()
        await self.page.evaluate(source)

    async def get_tools(self) -> list[dict[str, Any]]:
        try:
            return await self.page.evaluate("origins => window.__webmcp_resilience.getTools(origins)", self.from_origins)
        except Exception as error:
            raise WebMCPUnavailable(str(error)) from error

    async def wait_for_tools(self, names: set[str], timeout_ms: int = 3000) -> list[dict[str, Any]]:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            tools = await self.get_tools()
            if names.issubset({tool["name"] for tool in tools}):
                return tools
            if asyncio.get_running_loop().time() >= deadline:
                missing = ", ".join(sorted(names - {tool["name"] for tool in tools}))
                raise WebMCPUnavailable(f"WebMCP tools were not registered before timeout: {missing}")
            await asyncio.sleep(0.05)

    async def invoke_tool(self, name: str, args: dict[str, Any], invocation_id: str) -> Any:
        try:
            return await self.page.evaluate("([name, args, id]) => window.__webmcp_resilience.invokeTool(name, args, id)", [name, args, invocation_id])
        except Exception as error:
            raise RuntimeError(f"WebMCP tool {name!r} failed: {error}") from error

    async def start_tool(self, name: str, args: dict[str, Any], invocation_id: str) -> None:
        """Start a tool in the page without holding the Playwright command open.

        This is the browser-side handoff used by same-offset actor groups. The
        page retains the promise so a UI action can begin before the tool
        result is awaited.
        """
        try:
            await self.page.evaluate(
                "([name, args, id]) => window.__webmcp_resilience.startTool(name, args, id)",
                [name, args, invocation_id],
            )
        except Exception as error:
            raise RuntimeError(f"WebMCP tool {name!r} failed to start: {error}") from error

    async def await_started_tool(self, invocation_id: str) -> Any:
        """Collect a promise previously started with :meth:`start_tool`."""
        try:
            return await self.page.evaluate(
                "id => window.__webmcp_resilience.awaitTool(id)", invocation_id,
            )
        except Exception as error:
            raise RuntimeError(f"WebMCP tool invocation {invocation_id!r} failed: {error}") from error

    async def cancel(self, invocation_id: str) -> bool:
        return await self.page.evaluate("id => window.__webmcp_resilience.cancel(id)", invocation_id)

    async def get_state(self) -> dict[str, Any]:
        state = await self.page.evaluate("script => window.__webmcp_resilience.getState(script)", self.state_script)
        if not isinstance(state, dict):
            raise RuntimeError("state_script must return an object")
        return state

    async def drain_lifecycle_events(self) -> list[dict[str, Any]]:
        return await self.page.evaluate("() => window.__webmcp_resilience.drainLifecycle()")

    async def probe(self) -> dict[str, Any]:
        """Collect readiness evidence without invoking a page tool or mutating state."""
        return await self.page.evaluate("() => window.__webmcp_resilience.probe()")
