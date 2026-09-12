# Getting started

WebMCP Resilience runs against a live web application. It does not host your
application, manufacture data, or replace its test environment.

## Prerequisites

- Python 3.12 or later
- Chromium installed through Playwright
- A running application with WebMCP tools available to the browser runtime

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/playwright install chromium
.venv/bin/webmcp init
```

Set the target and optional observable-state expression in
`.webmcp/config.yaml`:

```yaml
base_url: http://localhost:3000
browser: chromium
state_script: window.__app.getObservableState()
```

The state expression runs in the page and must return an object. It is the
state source for invariants. If it is absent, the runner observes an empty
object rather than guessing from the DOM or traffic.

## First proof: the included race fixture

```bash
.venv/bin/webmcp demo-race --seed 7
```

This intentionally finds a failure in the bundled Resilience Forge app. The
command starts a temporary local server, saves preflight/validation/run/replay
bundles, prints the invariant handoff, then stops the server.

Use `--json` for one stable machine-readable document:

```bash
.venv/bin/webmcp demo-race --seed 7 --json
```

## First project scenario

Create `.webmcp/scenarios/checkout-race.yaml`:

```yaml
name: checkout-race
url: /checkout
actors:
  agent:
    - at: 0ms
      invoke: reserve_inventory
      args: {sku: "sku-42", quantity: 1}
  human:
    - at: 0ms
      action: click
      selector: '#reserve'
invariants:
  - inventory.reserved <= inventory.available
```

Run the workflow:

```bash
.venv/bin/webmcp preflight --ci --json
.venv/bin/webmcp validate .webmcp/scenarios/checkout-race.yaml --json
.venv/bin/webmcp run .webmcp/scenarios/checkout-race.yaml --ci --adversarial --seed 7 --json
```

Tool calls are read-only by default. Pass `--allow-mutations` only when the
target is isolated and you are authorised to change it.

## CI pattern

Use the same commands locally and in CI. Save `.webmcp/runs/` as a CI artifact
when a failure occurs.

```bash
.venv/bin/webmcp preflight --ci --run-id preflight --json
.venv/bin/webmcp run .webmcp/scenarios/checkout-race.yaml \
  --ci --adversarial --seed 7 --run-id checkout-race --json
.venv/bin/webmcp replay .webmcp/runs/checkout-race/bundle.json --json
```

For an intentionally failing regression fixture, preserve the non-zero exit
code and upload its bundle. Do not remove the evidence just to make a pipeline
green.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| WebMCP tools are not found | Run `preflight`; confirm browser/runtime support and that the page registers its tools before timeout. |
| Browser blocks WebMCP | Check secure context, origin isolation, `tools` Permissions Policy, and iframe delegation in the preflight report. |
| Invariant values are missing | Configure a `state_script` that returns an object. |
| A mutation is denied | Use an isolated target and explicitly pass `--allow-mutations`; agent requests cannot self-authorise. |
| Replay is rejected | The saved bundle's browser capability fingerprint or compatibility requirements do not match the current runtime. |

Continue with [scenarios, scheduling, and replay](scenarios-and-replay.md).
