from collections.abc import Awaitable, Callable

from playwright.async_api import Route

from .base import FaultEffect


def http_route(effect: FaultEffect) -> str | None:
    return effect.url if effect.type == "http_error" and effect.url else None


def response_handler(effect: FaultEffect) -> Callable[[Route], Awaitable[None]]:
    async def handler(route: Route) -> None:
        await route.fulfill(status=effect.status, content_type="application/json", body='{"error":"fault injected"}')
    return handler
