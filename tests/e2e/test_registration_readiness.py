"""Regression coverage for asynchronous registration and document replacement."""
import os
import asyncio
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from webmcp_resilience.browser.webmcp_adapter import WebMCPAdapter, WebMCPUnavailable
from webmcp_resilience.commands import CommandAPI
from webmcp_resilience.config import Config
from webmcp_resilience.engine.runner import ScenarioRunner
from webmcp_resilience.models.scenario import Scenario

pytestmark = pytest.mark.e2e

# A compatibility host keeps these tests independent of native WebMCP availability.
PAGE = """<script>
setTimeout(() => {
  const tool = {name: 'read', inputSchema: {type: 'object'},
                annotations: {readOnlyHint: true}};
  Object.defineProperty(document, 'modelContext', {value: {
    __webmcpResilienceCompatibilityHost: true,
    getTools: async () => [tool],
    executeTool: async () => ({ok: true}),
    addEventListener: () => {},
  }, configurable: true});
}, 150);
</script>"""


@pytest.fixture
async def client(monkeypatch, request):
    async with async_playwright() as playwright:
        options = {"headless": True}
        if executable := os.getenv('WEBMCP_TEST_BROWSER_EXECUTABLE'):
            options['executable_path'] = executable
        elif channel := os.getenv('WEBMCP_TEST_BROWSER_CHANNEL'):
            options['channel'] = channel
        browser = await playwright.chromium.launch(**options)
        page = await browser.new_page()
        content = PAGE
        if getattr(request, 'param', 'api') == 'tools':
            content = PAGE.replace('setTimeout(() => {', '(() => {').replace(
                'getTools: async () => [tool]',
                'getTools: async () => performance.now() >= 150 ? [tool] : []',
            ).replace('}, 150);', '})();')
        await page.route('http://readiness.test/**', lambda route: route.fulfill(body=content, content_type='text/html'))

        class Client:
            pass

        instance = Client()
        instance.page, instance.browser = page, browser

        @asynccontextmanager
        async def factory(*args, **kwargs):
            yield instance

        monkeypatch.setattr('webmcp_resilience.commands.BrowserClient', factory)
        try:
            yield instance
        finally:
            await browser.close()


@pytest.mark.parametrize('client', ['api', 'tools'], indirect=True)
async def test_run_and_replay_wait_before_contract_validation(client, tmp_path):
    api = CommandAPI(Config(base_url='http://readiness.test', tool_registration_grace_ms=1000), output_dir=tmp_path)
    scenario = Scenario(name='late-tool', actors={'agent': [{'invoke': 'read'}]})
    result = await api.run(scenario=scenario, run_id='late-run')
    assert result.result['passed'] is True
    assert result.tool_inventory[0]['name'] == 'read'
    api.save(result)
    replayed = await api.replay(tmp_path / 'late-run' / 'bundle.json', run_id='late-replay')
    assert replayed.result['passed'] is True
    assert replayed.tool_contract_replay_decision['allowed'] is True


async def test_reload_reinstalls_adapter_and_waits_for_registration(client):
    await client.page.goto('http://readiness.test/')
    runner = ScenarioRunner(client.page, Scenario(name='reload', actors={}))
    await runner.adapter.install()
    runner._set_tools(await runner.adapter.wait_for_tools({'read'}))
    await client.page.reload()
    # The init script restores the bridge in the new document immediately.
    assert await client.page.evaluate('Boolean(window.__webmcp_resilience)')
    from webmcp_resilience.models.scenario import TimedAction
    await runner._execute('agent', TimedAction(invoke='read'))
    await asyncio.gather(*runner.invocations.values())
    assert any(event.type == 'tool.result' for event in runner.recorder.run.events)


async def test_reinstall_preserves_pending_promises_and_handles(client):
    await client.page.goto('http://readiness.test/')
    adapter = WebMCPAdapter(client.page)
    await adapter.install()
    descriptor = (await adapter.wait_for_tools({'read'}))[0]
    await adapter.start_tool('read', {}, 'pending', descriptor)
    await adapter.install()
    assert await adapter.await_started_tool('pending') == {'ok': True}


async def test_missing_tool_has_bounded_registration_wait(client):
    await client.page.goto('http://readiness.test/')
    adapter = WebMCPAdapter(client.page)
    await adapter.install()
    with pytest.raises(WebMCPUnavailable, match='missing'):
        await adapter.wait_for_tools({'missing'}, timeout_ms=300)


async def test_http_latency_preserves_a_real_server_response(client):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(201)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Expose-Headers', 'X-Test')
            self.send_header('X-Test', 'original')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f'http://127.0.0.1:{server.server_port}/api/read'
        await client.page.goto('http://readiness.test/')
        runner = ScenarioRunner(client.page, Scenario(name='slow-server', actors={}, faults=[
            {'type': 'http_latency', 'url': endpoint, 'duration_ms': 200},
        ]))
        await runner._install_http_faults()
        response = await client.page.evaluate('''async url => {
            const start = performance.now();
            const response = await fetch(url);
            return {status: response.status, header: response.headers.get('X-Test'),
                    body: await response.json(), elapsed: performance.now() - start};
        }''', endpoint)
        assert response['status'] == 201
        assert response['header'] == 'original'
        assert response['body'] == {'ok': True}
        assert response['elapsed'] >= 200
        event = runner.recorder.run.events[-1]
        assert event.name == 'http_latency'
        assert event.data['duration_ms'] == 200
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


async def test_http_latency_delays_ms_let_a_later_reply_overtake_an_earlier_one(client):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f'http://127.0.0.1:{server.server_port}/api/save'
        await client.page.goto('http://readiness.test/')
        runner = ScenarioRunner(client.page, Scenario(name='out-of-order', actors={}, faults=[
            {'type': 'http_latency', 'url': f'{endpoint}*', 'delays_ms': [400, 20]},
        ]))
        await runner._install_http_faults()
        order = await client.page.evaluate('''async url => {
            const done = [];
            const first = fetch(url + '?n=1').then(() => done.push('first'));
            await new Promise((r) => setTimeout(r, 50));
            const second = fetch(url + '?n=2').then(() => done.push('second'));
            await Promise.all([first, second]);
            return done;
        }''', endpoint)
        assert order == ['second', 'first']
        event = runner.recorder.run.events[-1]
        assert event.name == 'http_latency'
        assert event.data['delays_ms'] == [400, 20]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
async def test_slow_setup_does_not_shrink_the_gap_between_scheduled_actions(client, monkeypatch):
    await client.page.goto('http://readiness.test/')
    scenario = Scenario(name='gap', actors={
        'agent': [{'at': '0ms', 'invoke': 'read'}],
        'other': [{'at': '200ms', 'invoke': 'read'}],
    })
    runner = ScenarioRunner(client.page, scenario)
    install = runner.adapter.install

    async def slow_install():
        await install()
        await asyncio.sleep(0.3)  # setup that takes longer than the first offset

    monkeypatch.setattr(runner.adapter, 'install', slow_install)
    run = await runner.run()
    invokes = [event.timestamp_ms for event in run.events if event.type == 'tool.invoke']
    assert len(invokes) == 2
    assert invokes[1] - invokes[0] >= 180
