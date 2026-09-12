from threading import Thread

import pytest

from webmcp_resilience.browser import BrowserClient, WebMCPAdapter
from webmcp_resilience.demo import create_server


pytestmark = pytest.mark.e2e


@pytest.fixture
def lab_server():
    server = create_server(port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


async def test_generic_adapter_discovers_and_invokes_the_lab_contract(lab_server: str) -> None:
    async with BrowserClient() as client:
        assert client.page
        await client.page.goto(lab_server)
        adapter = WebMCPAdapter(client.page, "window.__resilienceLab.getState()")
        await adapter.install()
        tools = await adapter.get_tools()
        assert {tool["name"] for tool in tools} >= {"create_record", "get_observable_state"}
        await adapter.invoke_tool("create_record", {"text": "Created through a discovered generic tool."}, "test-invocation")
        assert (await adapter.get_state())["records"]["created"] == 1
