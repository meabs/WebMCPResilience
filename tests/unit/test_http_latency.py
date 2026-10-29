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
