import pytest

from webmcp_resilience.faults import build_effects
from webmcp_resilience.faults.http import response_handler
from webmcp_resilience.models.scenario import Fault


@pytest.mark.parametrize('fields', [
    {}, {'url': '**/api/**'}, {'url': '**/api/**', 'duration_ms': -1},
    {'url': '**/api/**', 'duration_ms': 10, 'tool': 'save'},
    {'url': '**/api/**', 'duration_ms': 10, 'at': 'after_invoke'},
])
def test_http_latency_requires_a_url_and_positive_duration(fields):
    with pytest.raises(ValueError):
        Fault(type='http_latency', **fields)


@pytest.mark.asyncio
async def test_http_latency_delivers_original_response_after_delay(monkeypatch):
    events = []

    class Response:
        async def dispose(self):
            events.append('dispose')

    response = Response()

    class Route:
        async def fetch(self):
            events.append('fetch')
            return response

        async def fulfill(self, **kwargs):
            assert kwargs == {'response': response}
            events.append('fulfill')

    async def sleep(seconds):
        assert seconds == 0.75
        events.append('delay')

    monkeypatch.setattr('webmcp_resilience.faults.http.asyncio.sleep', sleep)
    effect = build_effects([Fault(type='http_latency', url='**/api/**', duration_ms=750)])[0]
    await response_handler(effect)(Route())
    assert events == ['fetch', 'delay', 'fulfill', 'dispose']


@pytest.mark.parametrize('fields', [
    {'url': '**/api/**', 'delays_ms': []},
    {'url': '**/api/**', 'delays_ms': [100, -1]},
    {'url': '**/api/**', 'delays_ms': [100], 'duration_ms': 50},
    {'delays_ms': [100]},
])
def test_http_latency_delays_ms_validation(fields):
    with pytest.raises(ValueError):
        Fault(type='http_latency', **fields)


def test_delays_ms_only_for_http_latency():
    with pytest.raises(ValueError):
        Fault(type='http_error', url='**/api/**', delays_ms=[100])


@pytest.mark.asyncio
async def test_http_latency_delays_ms_apply_in_request_order_and_repeat_the_last(monkeypatch):
    slept = []

    class Response:
        async def dispose(self):
            pass

    class Route:
        async def fetch(self):
            return Response()

        async def fulfill(self, **kwargs):
            pass

    async def sleep(seconds):
        slept.append(round(seconds * 1000))

    monkeypatch.setattr('webmcp_resilience.faults.http.asyncio.sleep', sleep)
    handler = response_handler(build_effects([Fault(type='http_latency', url='**/api/**', delays_ms=[400, 50])])[0])
    for _ in range(4):
        await handler(Route())
    assert slept == [400, 50, 50, 50]
