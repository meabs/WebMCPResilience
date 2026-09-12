from contextlib import AbstractAsyncContextManager

from playwright.async_api import Browser, Page, async_playwright


class BrowserClient(AbstractAsyncContextManager):
    def __init__(self, browser: str = "chromium", headless: bool = True, args: list[str] | None = None, channel: str | None = None) -> None:
        self.browser_name, self.headless, self.args, self.channel = browser, headless, args or [], channel
        self._playwright = None
        self.browser: Browser | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserClient":
        self._playwright = await async_playwright().start()
        launcher = getattr(self._playwright, self.browser_name)
        launch_options = {"headless": self.headless, "args": self.args}
        if self.channel:
            launch_options["channel"] = self.channel
        self.browser = await launcher.launch(**launch_options)
        self.page = await self.browser.new_page()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
