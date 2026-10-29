from collections.abc import Awaitable, Callable
import asyncio

from playwright.async_api import Route

from .base import FaultEffect


def http_route(effect: FaultEffect) -> str | None:
    return effect.url if effect.type in {"http_error", "http_latency"} and effect.url else None


def response_handler(effect: FaultEffect) -> Callable[[Route], Awaitable[None]]:
    seen = 0  # matching requests so far, for per-request delays

    async def handler(route: Route) -> None:
        nonlocal seen
        if effect.type == "http_latency":
            if effect.delays_ms:
                delay = effect.delays_ms[min(seen, len(effect.delays_ms) - 1)]
            else:
                delay = effect.duration_ms
            seen += 1
            response = await route.fetch()
            try:
                await asyncio.sleep(delay / 1000)
                await route.fulfill(response=response)
            finally:
                await response.dispose()
            return
        await route.fulfill(status=effect.status, content_type="application/json", body='{"error":"fault injected"}')
    return handler
