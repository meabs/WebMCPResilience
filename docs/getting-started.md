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
# Or target a reachable external test environment in one command. This writes
# a safe observable-state placeholder for you to replace.
.venv/bin/webmcp init --from-url https://staging.example.test/checkout
```

Set the target and optional observable-state expression in
`.webmcp/config.yaml`:

```yaml
base_url: http://localhost:3000
browser: chromium
state_script: window.__app.getObservableState()
# Native WebMCP example (verify this exact binary with `webmcp preflight`):
browser_channel: chromium
browser_args: ["--enable-features=WebMCPTesting"]
# `auto` uses the documented browser profile. Pin this when your browser differs.
# webmcp_profile: legacy-string  # auto | native-object
# Stop a tool call that has not finished after 15 seconds (set null to disable).
# invoke_timeout_ms: 15000
# Let React/Vue-style observable state settle before *continuous* invariants.
# state_settle_ms: 25
# Optional backend reset/state contract:
# reset_script: "await fetch('/test/reset', {method: 'POST'})"
# initial_state: {claims: {active: 0, capacity: 1}}
```

The state expression runs in the page and must return an object. It is the
state source for invariants. If it is absent, the runner observes an empty
object rather than guessing from the DOM or traffic.

The bundled lab is a compatibility host; its tests do not establish native
browser support. Use `preflight` against the exact browser binary, channel and
flags you intend to support, then run and replay a scenario without the
fixture before treating that profile as native evidence.

If preflight says the tool inventory is empty, WebMCP may be present but the
page has not registered its tools. Check registration code, `registerTool`
options such as `AbortSignal`, and your browser's origin-trial setup. The
runner waits two seconds for registration by default; set
`tool_registration_grace_ms` if your test app needs longer.

If a tool never finishes, the runner stops it after `invoke_timeout_ms` and
reports `tool_invoke_timeout`. This usually means a declarative tool is waiting
for a user confirmation or another browser-side completion step; it is not
automatically an application bug.

Set `timeout_ms` on an individual `invoke` or `retry` when one action needs a
different budget; it overrides `invoke_timeout_ms` only for that action. For
reactive apps whose observable state lags a completed click or tool call, set
`state_settle_ms` to a small explicit value before continuous invariants run.
Use `final_invariants` for eventual, end-of-workflow assertions.

## First proof: the included race fixture

```bash
.venv/bin/webmcp demo-race --seed 7
```

This intentionally finds a failure in the bundled Resilience Forge app. The
command starts a temporary local server, saves preflight/validation/run/replay
bundles, prints the invariant handoff, then stops the server.
It exits 0 when it successfully finds the intentional failure. In contrast,
`run` and `replay` exit 1 when their scenario result fails an invariant.

To replay the saved demo bundle after `demo-race` exits, serve the lab again in
another terminal:

```bash
.venv/bin/webmcp demo
# In another terminal:
.venv/bin/webmcp replay .webmcp/runs/demo-race-failure/bundle.json \
  --allow-mutations --json
```

The bundle restores the recorded state expression and target origin. The live
application is still required; it is not embedded in the evidence bundle.

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

This repository includes a composite GitHub Action that performs preflight,
runs a declared scenario, and uploads `.webmcp/runs/` when either step fails:

```yaml
- uses: ./.github/actions/webmcp-resilience
  with:
    scenario: .webmcp/scenarios/checkout-race.yaml
    allow-mutations: "true" # only for an isolated test target
```

## Troubleshooting

| Symptom | First check |
| --- | --- |
| WebMCP tools are not found | Run `preflight`; confirm browser/runtime support and that the page registers its tools before timeout. |
| Browser blocks WebMCP | Check secure context, origin isolation, `tools` Permissions Policy, and iframe delegation in the preflight report. |
| Invariant values are missing | Configure a `state_script` that returns an object. |
| A mutation is denied | Use an isolated target and explicitly pass `--allow-mutations`; agent requests cannot self-authorise. |
| Replay is rejected | The saved bundle's browser capability fingerprint or compatibility requirements do not match the current runtime. |

Continue with [scenarios, scheduling, and replay](scenarios-and-replay.md).
