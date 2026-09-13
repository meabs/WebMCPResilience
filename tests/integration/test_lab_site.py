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
        api = (await adapter.probe())["api"]
        assert api["mode"] == "compatibility_host"
        assert api["native"] is False
        assert api["compatibilityHost"] is True
        tools = await adapter.get_tools()
        assert {tool["name"] for tool in tools} >= {"create_record", "get_observable_state"}
        await adapter.invoke_tool("create_record", {"text": "Created through a discovered generic tool."}, "test-invocation")
        assert (await adapter.get_state())["records"]["created"] == 1


async def test_preflight_probes_chromium_tools_permissions_policy(lab_server: str) -> None:
    async with BrowserClient() as client:
        assert client.page
        await client.page.goto(lab_server)
        await client.page.evaluate("""() => Object.defineProperty(document, 'permissionsPolicy', {
          configurable: true,
          value: {
            features: () => ['tools'],
            allowsFeature: (feature) => feature === 'tools',
          },
        })""")
        adapter = WebMCPAdapter(client.page)
        await adapter.install()
        policy = (await adapter.probe())["permissionsPolicy"]
        assert policy["feature"] == "tools"
        assert policy["modelContextAllowed"] is True


async def test_preflight_recognises_document_model_context_as_native(lab_server: str) -> None:
    async with BrowserClient() as client:
        assert client.page
        await client.page.goto(lab_server)
        await client.page.evaluate("""() => {
          document.modelContext = {
            getTools: async () => [],
            executeTool: async () => ({}),
          };
        }""")
        adapter = WebMCPAdapter(client.page)
        await adapter.install()
        api = (await adapter.probe())["api"]
        assert api["location"] == "document.modelContext"
        assert api["mode"] == "native"
        assert api["native"] is True
        assert api["compatibilityHost"] is False
