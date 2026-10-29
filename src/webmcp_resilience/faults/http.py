from collections.abc import Awaitable, Callable
import asyncio

from playwright.async_api import Route

from .base import FaultEffect


def http_route(effect: FaultEffect) -> str | None:
    return effect.url if effect.type in {"http_error", "http_latency"} and effect.url else None


def response_handler(effect: FaultEffect) -> Callable[[Route], Awaitable[None]]:
    async def handler(route: Route) -> None:
        if effect.type == "http_latency":
            response = await route.fetch()
            try:
                await asyncio.sleep(effect.duration_ms / 1000)
                await route.fulfill(response=response)
            finally:
                await response.dispose()
            return
        await route.fulfill(status=effect.status, content_type="application/json", body='{"error":"fault injected"}')
    return handler
