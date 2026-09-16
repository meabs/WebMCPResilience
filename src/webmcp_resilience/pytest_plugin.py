"""pytest integration for real configured WebMCP applications."""
import pytest

from .browser import BrowserClient, WebMCPAdapter
from .config import load_config


@pytest.fixture
async def webmcp():
    """Yield an adapter for the live app configured in `.webmcp/config.yaml`."""
    config = load_config()
    async with BrowserClient(config.browser, args=config.browser_args, channel=config.browser_channel) as client:
        assert client.page
        await client.page.goto(config.base_url)
        adapter = WebMCPAdapter(client.page, config.state_script, config.from_origins, config.webmcp_profile)
        await adapter.install()
        yield adapter
