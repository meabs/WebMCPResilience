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


async def test_probe_classifies_a_native_shaped_contract_fixture(lab_server: str) -> None:
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


async def test_explicit_native_object_profile_passes_object_arguments(lab_server: str) -> None:
    async with BrowserClient() as client:
        assert client.page
        await client.page.goto(lab_server)
        await client.page.evaluate("""() => {
          const tool = {name: 'object_tool', inputSchema: {type: 'object', properties: {value: {type: 'string'}}}};
          document.modelContext = {
            async getTools() { return [tool]; },
            async executeTool(_tool, args) { return {code: typeof args === 'object' && args.value === 'ok' ? 'OBJECT' : 'WRONG'}; },
          };
        }""")
        adapter = WebMCPAdapter(client.page, profile="native-object")
        await adapter.install()
        tools = await adapter.get_tools()
        assert tools[0]["argumentMode"] == "object"
        result = await adapter.invoke_tool("object_tool", {"value": "ok"}, "object-invocation")
        assert result["code"] == "OBJECT"


async def test_auto_profile_passes_string_arguments_to_a_native_shaped_host(lab_server: str) -> None:
    async with BrowserClient() as client:
        assert client.page
        await client.page.goto(lab_server)
        await client.page.evaluate("""() => {
          const tool = {name: 'string_tool', inputSchema: {type: 'object'}};
          document.modelContext = {
            async getTools() { return [tool]; },
            async executeTool(_tool, args) { return {code: typeof args === 'string' && args === '{\\"value\\":\\"ok\\"}' ? 'STRING' : 'WRONG'}; },
          };
        }""")
        adapter = WebMCPAdapter(client.page)
        await adapter.install()
        tools = await adapter.get_tools()
        assert tools[0]["argumentMode"] == "string"
        result = await adapter.invoke_tool("string_tool", {"value": "ok"}, "string-invocation")
    assert result["code"] == "STRING"


@pytest.mark.parametrize(("chrome_major", "expected_mode", "expected_code"), [
    (151, "string", "STRING"),
    (155, "object", "OBJECT"),
])
async def test_auto_profile_gate_is_pinned_to_chrome_151_and_155_fixtures(
    lab_server: str, chrome_major: int, expected_mode: str, expected_code: str
) -> None:
    async with BrowserClient() as client:
        assert client.page
        await client.page.goto(lab_server)
        await client.page.evaluate(f'''() => {{
          Object.defineProperty(navigator, 'userAgent', {{configurable: true, value: 'Mozilla/5.0 Chrome/{chrome_major}.0.0.0 Safari/537.36'}});
          const tool = {{name: 'versioned_tool', inputSchema: {{type: 'object'}}}};
          document.modelContext = {{
            async getTools() {{ return [tool]; }},
            async executeTool(_tool, args) {{ return {{code: typeof args === '{'string' if expected_mode == 'string' else 'object'}' ? '{expected_code}' : 'WRONG'}}; }},
          }};
        }}''')
        adapter = WebMCPAdapter(client.page)
        await adapter.install()
        tools = await adapter.get_tools()
        assert tools[0]["argumentMode"] == expected_mode
        result = await adapter.invoke_tool("versioned_tool", {"value": "ok"}, f"chrome-{chrome_major}")
        assert result["code"] == expected_code


async def test_auto_profile_preserves_string_arguments_for_compatibility_host(lab_server: str) -> None:
    async with BrowserClient() as client:
        assert client.page
        await client.page.goto(lab_server)
        adapter = WebMCPAdapter(client.page)
        await adapter.install()
        tools = await adapter.get_tools()
        assert tools[0]["argumentMode"] == "string"
