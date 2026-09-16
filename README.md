<div align="center">

# WebMCP Resilience

**Find timing bugs before users do.**

Test whether your WebMCP app stays correct when people and tools act at the same time.

[Documentation](#start-here) · [Example fixture](examples/resilience-forge) · [Architecture](docs/architecture.md) · [Where it fits](docs/comparison.md)

[![Tests](https://github.com/meabs/WebMCPResilience/actions/workflows/test.yml/badge.svg)](https://github.com/meabs/WebMCPResilience/actions/workflows/test.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-3DA639.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg)](pyproject.toml)

</div>

WebMCP Resilience is a developer test tool for WebMCP-enabled web applications.
It checks whether the application stays correct when people, AI tools, and
normal application events overlap. It adds familiar problems such as slow
responses or repeated calls, then saves a small record you can rerun if it
finds a bug.

For example, if one item is left, a person might click **Reserve** while an AI
tool calls `reserve_inventory`. Each action can appear to work on its own while
together they reserve two items. WebMCP Resilience runs that overlap
deliberately and checks a rule such as “reserved items must never exceed items
available.”

You describe a scenario in reviewable YAML: the page to open, the people,
tools, and system actors involved, the actions they take, the timing to try,
and the rules that must hold. The runner performs those declared browser and
tool actions in fresh browser sessions. It can introduce latency, timeouts,
duplicate calls, cancellation, navigation, and selected HTTP failures; explore
bounded orderings of simultaneous actions; and check observable application
state, expected tool results, and optional tool-contract expectations.

When a rule breaks, the tool reduces the case to the smallest declared scenario
that reproduces the same failure. It saves portable, redacted evidence with the
scenario, selected schedule, browser setup, tool inventory, state observations,
trace, result, and replay instructions. A replay checks that the browser and
tool contract are still compatible, so an old failure is not silently treated
as the same bug after the application changes.

An **actor** is a person, AI tool, or system taking an action. An **invariant**
is a rule the app must never break. WebMCP Resilience tests application
correctness once a tool is used; it does not assess whether an AI agent is
clever enough to discover or choose the right tool.

## See it find and replay a failure

The included Resilience Forge fixture is intentionally vulnerable. Run it to
watch WebMCP Resilience find a human/tool race, minimize it, and save a
replayable evidence bundle.

[![Watch the WebMCP Resilience demo-race video](docs/assets/demo-race.gif)](docs/assets/demo-race.mp4)

*A 13-second walkthrough of the included `demo-race` workflow. Select the
preview to open the full video.*

```bash
git clone https://github.com/meabs/WebMCPResilience.git
cd WebMCPResilience
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/playwright install chromium
.venv/bin/webmcp demo-race
```

For an external staging target, bootstrap its local resilience configuration
in one command, then replace the generated observable-state placeholder with
your app's explicit state boundary:

```bash
.venv/bin/webmcp init --from-url https://staging.example.test/checkout
```

```text
Failed invariant: claims.active <= claims.capacity
Observed state: {"claims": {"active": 2, "capacity": 1}}
Bundle: .webmcp/runs/demo-race-failure/bundle.json
Replay: webmcp replay .webmcp/runs/demo-race-failure/bundle.json --run-id demo-race-failure --allow-mutations
```

See the [fixture source and scenario](examples/resilience-forge) for the full
application and its vulnerable booking flow.

`demo-race` exits 0 when it finds and replays this intentional failure. The
demo's temporary server stops when it finishes, so start the lab in another
terminal before replaying its saved bundle:

```bash
.venv/bin/webmcp demo
# In another terminal:
.venv/bin/webmcp replay .webmcp/runs/demo-race-failure/bundle.json --allow-mutations --json
```

## Why it exists

An AI tool can call your site successfully while still triggering a bug when it
overlaps with a person’s action. WebMCP Resilience finds those timing bugs and
gives you a repeatable record to fix them before they affect users.

```text
Chrome DevTools / MCP Inspector / MCP Conformance → Is the tool callable?
WebMCP Resilience                              → Is the application resilient?
```

## Start here

### Choose your path

| You want to… | Start here |
| --- | --- |
| Prove the workflow locally | [`webmcp demo-race`](#see-it-find-and-replay-a-failure) |
| Test your own WebMCP app | [First project scenario](docs/getting-started.md#first-project-scenario) |
| Add deterministic checks to CI | [CI pattern](docs/getting-started.md#ci-pattern) |
| Define concurrency and fault scenarios | [Scenario reference](docs/scenarios-and-replay.md) |
| Start from a common scenario | [Recipe library](examples/recipes/README.md) |
| Give a coding agent controlled access | [Agent control and safety](docs/agent-control.md) |

## What you need before you start

Before you write a scenario, make sure you have:

1. A page that registers WebMCP tools through `document.modelContext`.
2. A `base_url` reachable from Playwright.
3. An explicit `state_script` that returns the observable state used by your
   invariants.
4. An isolated test target if you plan to permit mutations.

```yaml
# .webmcp/config.yaml
base_url: http://localhost:3000
browser: chromium
state_script: window.__app.getObservableState()
# Optional: settings for a native WebMCP browser. Check them with `webmcp preflight`.
browser_channel: chromium
browser_args: ["--enable-features=WebMCPTesting"]
# `auto` uses the documented browser profile. Pin this when your browser differs.
# webmcp_profile: legacy-string  # auto | native-object
# Stop a tool call that has not finished after 15 seconds (set null to disable).
# invoke_timeout_ms: 15000
# Optional: reset a server-backed test app before each run.
# reset_script: "await fetch('/test/reset', {method: 'POST'})"
# initial_state: {claims: {active: 0, capacity: 1}}
```

`state_script` tells WebMCP Resilience exactly what app state to check. It does
not guess from page text, network traffic, or hidden browser state.

The included lab works everywhere supported by Playwright because it uses a
compatibility host. That is useful for local tests, but it does not prove that
a browser has native WebMCP support.

To test a native browser, run `preflight` with the exact browser, channel, and
flags you plan to use. Then run and replay a scenario without the lab fixture.
Each saved bundle records the browser version, launch settings, argument
profile, and available capabilities. This project does not claim protocol
conformance.

### Common setup problems

- **A native tool rejects its arguments:** older Chrome WebMCP builds use JSON
  strings. Set `webmcp_profile: legacy-string`. The saved bundle records the
  argument mode and replay rejects a different one.
- **Preflight finds no tools:** the page may be stuck while registering them.
  Check its `registerTool` call, including options such as `AbortSignal`, and
  increase `tool_registration_grace_ms` if registration is simply slow.
- **A run stops with `tool_invoke_timeout`:** the tool did not finish within
  `invoke_timeout_ms` (15 seconds by default). This often means a declarative
  tool is waiting for a browser-side confirmation, rather than an app bug.
- **A selector matches more than one element:** make it more specific, use
  `:nth-of-type(n)`, or use a role-based selector.

## A complete workflow

```bash
# Check that the browser and WebMCP tools are ready. This does not change app state.
.venv/bin/webmcp preflight --ci --json

# Check the scenario, then run it with repeatable timing in fresh browser sessions.
.venv/bin/webmcp validate .webmcp/scenarios/checkout-race.yaml --json
.venv/bin/webmcp run .webmcp/scenarios/checkout-race.yaml \
  --ci --adversarial --seed 7 --json

# Replay a saved failure, or compare two saved runs without opening a browser.
.venv/bin/webmcp replay .webmcp/runs/<run-id>/bundle.json --json
# Require the original fingerprint exactly, including safe compatible drift.
.venv/bin/webmcp replay .webmcp/runs/<run-id>/bundle.json --strict-tool-contracts --json
.venv/bin/webmcp diff left/bundle.json right/bundle.json --json
```

`run`, `preflight`, and `replay` save portable evidence under
`.webmcp/runs/<run-id>/bundle.json`. A bundle records the scenario, runtime
compatibility, redacted tool-contract inventory, schedule, trace, observable
state, approvals, artifact metadata, outcome, replay decision, and replay command. `diff`
compares two existing bundles without launching a browser.

## Scenario in one screen

Scenarios are reviewable YAML: coding agents can generate them, humans can
review them in Git, and every way of running the tool uses the same file.

This scenario tells the framework: while an AI calls `reserve_inventory`, a
person also clicks reserve—check that inventory never reserves more than is
available.

```yaml
name: concurrent-tool-and-ui
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
faults:
  - type: duplicate_invocation
    tool: reserve_inventory
    at: before_invoke
invariants:
  - inventory.reserved <= inventory.available
final_invariants:
  - checkout.submitted == true
```

For state-aware inputs, fault timing, result invariants, reduction, replay, and
optional tool-contract assertions, see the [scenario and replay guide](docs/scenarios-and-replay.md).

## Features

- **One consistent workflow** — the command line, Python, terminal console,
  and local agent control all run the same browser checks.
- **People and tools acting together** — describe actions by a person, an AI
  tool, or the system on one live page, at controlled times.
- **Common failure conditions** — test slow responses, timeouts, duplicate
  calls, cancellations, navigation, and HTTP failures deliberately.
- **Useful failure evidence** — when a rule breaks, the tool reduces the run
  to the smallest case that still shows the problem and saves it for replay.
- **Safe replay** — saved evidence checks that the browser and available tools
  still match, so an old failure is not silently reinterpreted as a new one.

## CLI first. Console and agent access when needed.

The CLI is fully capable on its own. The optional terminal console composes
ordinary scenario YAML and inspects ordinary evidence bundles; it does not
introduce a second runner.

```bash
# Compose from a preflight inventory or inspect saved evidence.
.venv/bin/webmcp console --discovery .webmcp/runs/<preflight-id>/bundle.json
.venv/bin/webmcp console .webmcp/runs/<run-id>/bundle.json --compare baseline.json

# Start the typed local MCP JSON-RPC adapter in read-only mode.
.venv/bin/webmcp agent-server --project-root .
```

The agent adapter fixes its project root, allowed target origins, concurrency,
artifact sensitivity, and mutation authority at startup. Requests cannot
escalate those permissions. Read the [agent safety model](docs/agent-control.md)
before enabling mutations.

## Documentation

| Guide | Use it for |
| --- | --- |
| [Getting started](docs/getting-started.md) | Your first scenario, CI loop, and troubleshooting |
| [Scenarios, scheduling, and replay](docs/scenarios-and-replay.md) | DSL, state boundaries, faults, reduction, and bundle contract |
| [Agent control and safety](docs/agent-control.md) | Local MCP operations, immutable policy, and redaction |
| [Architecture](docs/architecture.md) | Command seams and concurrency model |
| [Where it fits](docs/comparison.md) | Boundaries with DevTools, MCP Inspector, Playwright, and browser clouds |

## Boundaries

WebMCP Resilience does not replace Chrome DevTools, MCP Inspector, MCP
Conformance, Playwright, browser clouds, or agent-evaluation frameworks.

It does not test whether an AI agent is clever enough to find the checkout
button. It tests whether the checkout application remains robust when driven
through WebMCP.

## Contributing and verification

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q src
```

For the browser-backed proof fixture:

```bash
.venv/bin/pytest -m e2e
```

See [AGENTS.md](AGENTS.md) for automation guidance. Licensed under
[Apache-2.0](LICENSE).
