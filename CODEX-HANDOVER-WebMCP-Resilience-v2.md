# WebMCP Resilience — Codex Build Handover

## Objective

Build the first working version of **WebMCP Resilience**, an open-source adversarial testing framework for WebMCP-enabled web applications.

The product should answer:

> What happens when agents, humans, retries, failures and timing conditions interact with the same WebMCP-enabled application state?

This is **not** another WebMCP inspector, schema linter, generic eval framework, or LLM benchmark.

The initial product should focus on:

- WebMCP tool discovery
- deterministic tool invocation
- browser/UI actor execution
- concurrent actor scheduling
- fault injection
- invariant checking
- execution tracing
- deterministic replay of failures
- CI-friendly CLI
- first-class structured control surface for coding agents such as Codex and Claude Code

The core implementation should be **Python-first**, with a deliberately isolated **JavaScript browser adapter**.

---

# 1. Technical Direction

## Primary stack

Use:

- Python 3.12+
- pytest
- Hypothesis
- asyncio
- Pydantic
- PyYAML
- Playwright for Python
- Typer for CLI
- Rich for terminal output

Browser-side integration:

- small JavaScript adapter injected through Playwright
- adapter must isolate WebMCP API churn from the Python core

Agent-operability is a product principle, not a later UI feature. Build the CLI on top of a reusable Command API and ensure all important operations can return versioned JSON.

Do not build a frontend application for v0.1.

Do not require an LLM.

Do not require any cloud service.

Do not add authentication, persistence services, accounts or telemetry backends.

The first release must work entirely locally and in CI.

---

# 2. Architectural Shape

```text
                    ┌───────────────────┐
                    │       CLI         │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Scenario Engine  │
                    └─────────┬─────────┘
                              │
               ┌──────────────┼──────────────┐
               │              │              │
               ▼              ▼              ▼
          State Explorer   Fault Engine   Trace Engine
               │
               ▼
          Playwright
               │
               ▼
      Browser WebMCP Adapter
               │
               ▼
         Web Application
```

Suggested package layout:

```text
webmcp-resilience/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── webmcp_resilience/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── models/
│       │   ├── scenario.py
│       │   ├── trace.py
│       │   └── result.py
│       ├── browser/
│       │   ├── playwright_client.py
│       │   ├── webmcp_adapter.py
│       │   └── adapter.js
│       ├── engine/
│       │   ├── runner.py
│       │   ├── scheduler.py
│       │   ├── invariants.py
│       │   └── explorer.py
│       ├── faults/
│       │   ├── base.py
│       │   ├── latency.py
│       │   ├── duplicate.py
│       │   ├── cancellation.py
│       │   └── navigation.py
│       ├── trace/
│       │   ├── recorder.py
│       │   ├── replay.py
│       │   └── serializer.py
│       └── reporting/
│           ├── console.py
│           └── junit.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── examples/
│   └── checkout/
└── .github/
    └── workflows/
        └── test.yml
```

---

# 3. Core Domain Model

The public mental model must remain limited to:

- Scenario
- Actor
- Action
- Fault
- Invariant
- Trace

Do not leak internal scheduler or browser-adapter concepts into the user-facing DSL unless strictly necessary.

## Scenario

Represents one resilience test.

Example:

```yaml
name: checkout-concurrency
url: /cart

actors:
  agent:
    - invoke: checkout

  human:
    - action: fill
      selector: '#quantity'
      value: '2'

faults:
  - duplicate_invocation
  - latency

invariants:
  - orders.created <= 1
  - payments.captured <= 1
```

Use Pydantic models for configuration validation.

---

# 4. Browser Adapter

Create a small JS adapter injected into the page via Playwright.

The Python engine must call only this adapter, not browser WebMCP APIs directly.

Conceptual browser contract:

```javascript
window.__webmcp_resilience = {
	async getTools() {},
	async invokeTool(name, args) {},
	async cancel(invocationId) {},
	async getState() {}
}
```

The adapter should normalise whichever WebMCP testing API exists in the current Chromium environment.

Design this behind a Python `WebMCPAdapter` interface so future changes can be contained.

Do not hard-wire business logic into the adapter.

---

# 5. CLI

Implement with Typer.

Required commands for v0.1:

```bash
webmcp init
webmcp inspect
webmcp test
webmcp replay
```

## `webmcp init`

Creates:

```text
.webmcp/
├── config.yaml
├── scenarios/
└── failures/
```

Default config:

```yaml
base_url: http://localhost:3000
browser: chromium
```

## `webmcp inspect`

Launches the configured app and prints discovered WebMCP tools.

Example output:

```text
Found 4 WebMCP tools

✓ search_products
✓ add_to_cart
✓ checkout
✓ get_order
```

## `webmcp test`

Run one or more scenarios.

Support:

```bash
webmcp test
webmcp test checkout
webmcp test checkout --adversarial
webmcp test --ci
```

## `webmcp replay`

Replay a saved failure:

```bash
webmcp replay .webmcp/failures/checkout-a84f.yaml
```

---

# 6. Scenario Engine

Build a deterministic scheduler first.

The scheduler must be able to execute:

- agent WebMCP actions
- human Playwright actions
- system/fault actions

Support explicit timing:

```yaml
actors:
  agent:
    - at: 0ms
      invoke: checkout

  human:
    - at: 100ms
      action: fill
      selector: '#quantity'
      value: '2'
```

Do not implement exhaustive state-space exploration immediately.

For v0.1, support:

- declared ordering
- deterministic timing
- a small number of generated permutations

Then add Hypothesis-driven exploration once the basic engine is stable.

---

# 7. Human Actor

Support these Playwright actions:

- click
- fill
- select
- navigate
- wait

Example:

```yaml
human:
  - action: click
    selector: '#quantity'

  - action: fill
    selector: '#quantity'
    value: '2'
```

All actions must emit trace events.

---

# 8. Agent Actor

Agent actions are deterministic WebMCP invocations.

Example:

```yaml
agent:
  - invoke: checkout
    args:
      basketId: abc123
```

No LLM is involved.

Support:

- invoke
- retry
- cancel

Each invocation must have an internal invocation ID.

---

# 9. Fault Injection

Implement an extensible fault interface.

Initial faults:

## latency

Delay a selected tool invocation.

Example:

```yaml
faults:
  - type: latency
    tool: checkout
    duration_ms: 2000
```

## duplicate invocation

Repeat a WebMCP call.

## cancellation

Cancel an in-flight invocation.

## navigation

Navigate away during execution.

Do not attempt full network chaos engineering in v0.1.

Keep fault implementations deterministic and reproducible.

---

# 10. Invariants

Support simple invariant expressions first.

Examples:

```yaml
invariants:
  - orders.created <= 1
  - payments.captured <= orders.created
```

Internally, evaluate against a normalised state dictionary.

Example:

```python
{
	'orders': {
		'created': 1,
	},
	'payments': {
		'captured': 1,
	},
}
```

Avoid `eval()`.

Use a constrained parser or expression library.

Later versions can support Python callbacks.

Invariant evaluation should happen:

- after relevant actions
- at scenario completion

On failure, record:

- invariant
- observed state
- triggering event
- full trace

---

# 11. Trace Model

Every meaningful event should generate a trace record.

Suggested shape:

```json
{
	"timestamp_ms": 92,
	"actor": "agent",
	"type": "tool.invoke",
	"name": "checkout",
	"invocation_id": "inv_123",
	"data": {},
	"state_snapshot": {}
}
```

Event types should include at least:

```text
scenario.start
scenario.end

tool.discovered
tool.invoke
tool.result
tool.error
tool.cancel
tool.retry

ui.click
ui.fill
ui.navigate

fault.injected

state.observed

invariant.pass
invariant.fail
```

The trace format should be versioned.

Example:

```json
{
	"schema_version": "0.1"
}
```

---

# 12. Failure Recording

When an invariant fails, write a reproduction file under:

```text
.webmcp/failures/
```

Example:

```text
checkout-concurrency-a84f.yaml
```

The reproduction must contain enough information to run deterministically again.

Example:

```yaml
scenario: checkout-concurrency

sequence:
  - at: 0
    actor: agent
    invoke: checkout

  - at: 43
    actor: human
    action: fill
    selector: '#quantity'
    value: '2'

  - at: 91
    actor: agent
    retry: checkout

failed_invariant:
  expression: payments.captured <= 1

observed:
  payments.captured: 2
```

---

# 13. Replay

`webmcp replay` must execute the recorded sequence exactly.

Example result:

```text
Replaying checkout-concurrency-a84f

✓ agent checkout
✓ human quantity = 2
✓ agent retry checkout

FAIL

payments.captured <= 1

Observed:
payments.captured = 2
```

Replay must be deterministic enough to use as a regression test.

---

# 14. Hypothesis Integration

Do not block v0.1 on advanced state-machine exploration.

Once the core deterministic runner works, introduce Hypothesis to generate:

- action ordering
- retries
- cancellation points
- latency ranges
- malformed argument values

Key requirement:

When Hypothesis discovers a failure, shrink the sequence to the smallest reproducible failing scenario.

Example:

Initial failure:

```text
checkout
quantity change
latency
navigation
retry
cancel
retry
```

Shrunk failure:

```text
checkout
quantity change
retry
```

This is a signature capability and should become a core differentiator.

---

# 15. Reporting

Initial console output should use Rich.

Example:

```text
WebMCP Resilience

Scenario: checkout-concurrency

Runs:   74
Passed: 73
Failed: 1

FAIL

Invariant:
payments.captured <= 1

Observed:
payments.captured: 2

Minimal sequence:

+000ms agent  checkout
+043ms human  quantity = 2
+091ms agent  retry checkout

Saved:
.webmcp/failures/checkout-concurrency-a84f.yaml
```

Also support JUnit XML for CI.

Do not build a graphical report UI in v0.1.

---

# 16. Example Application

Create a minimal example WebMCP-enabled checkout application used solely for integration tests and demos.

It should intentionally contain one race/idempotency bug that WebMCP Resilience can discover.

Suggested behaviour:

- cart with quantity
- checkout WebMCP tool
- simulated order creation
- simulated payment capture
- duplicate checkout invocation can initially double-charge

The demo should show:

```text
bug exists
   ↓
resilience test finds it
   ↓
failure trace saved
   ↓
bug fixed
   ↓
replay passes
```

Keep the example app deliberately small.

It can be plain HTML/JS or a minimal framework app.

---

# 17. CI

Provide GitHub Actions example:

```yaml
- name: Install
  run: pip install -e .

- name: Install Chromium
  run: playwright install chromium

- name: Run tests
  run: webmcp test --ci
```

Exit code:

- `0` all scenarios pass
- non-zero when scenario/invariant failures occur

JUnit report should be suitable for normal CI test reporting.

---

# 18. Non-Goals for v0.1

Do NOT build:

- LLM agent orchestration
- GPT / Claude / Gemini comparisons
- local model inference
- SaaS backend
- production telemetry
- user accounts
- dashboards
- authentication
- generic MCP server testing
- WebMCP schema quality scoring
- static prompt-injection scanner
- distributed execution
- Firefox/Safari support
- full OpenTelemetry integration
- exhaustive model checking
- enterprise policy engine

Do not let attractive future features delay the core resilience loop.

---


# 18A. Agent-Friendly Control Surface

Design the core so Codex, Claude Code and other coding agents can drive the framework directly.

## Command API

Create a stable internal Command API between interfaces and the resilience engine. The CLI must call this API rather than embedding business logic.

Target shape:

```text
CLI ───────────┐
CLI --json ────┤
MCP server ────┼──> Command API ──> Resilience Core
Python API ────┘
```

Implement the Command API and JSON CLI in the initial architecture even if the MCP server lands in a later milestone.

## Structured JSON

Every important CLI operation must support `--json`. Do not require agents to parse Rich output.

Use a versioned result envelope, for example:

```json
{
  "schema_version": "0.1",
  "status": "failed",
  "scenario": "checkout-concurrency",
  "failure_id": "fail_7c92",
  "invariant": "payments.captured <= 1",
  "observed": {"payments.captured": 2},
  "minimal_sequence": [
    "agent.checkout",
    "human.quantity_change",
    "agent.retry"
  ],
  "replay_command": "webmcp replay fail_7c92 --json"
}
```

Maintain stable exit codes.

## MCP control server

Add an MCP server adapter over the Command API. It must control WebMCP Resilience; it must not duplicate or impersonate the browser-side WebMCP implementation being tested.

Target tools:

```text
list_capabilities
inspect_site
list_tools
list_scenarios
create_scenario
plan_scenario
run_scenario
run_adversarial
get_failure
replay_failure
minimise_failure
get_trace
```

Keep MCP schemas explicit and compact. Results should reuse the same structured domain/result models as the CLI JSON interface.

## Capability discovery

Expose supported:

- actors
- actions
- faults
- invariant operators
- state probes
- browser capabilities
- framework/version information

Do this through the Command API and MCP `list_capabilities` operation. Optionally persist a generated `.webmcp/capabilities.json` for repository-local discovery.

## Scenario synthesis

Provide a structured operation to generate a normal starter scenario from discovered WebMCP tools and a requested focus such as concurrency, retry, cancellation, state mutation or boundary inputs.

Do not introduce a separate AI-only scenario format. Generated scenarios must be ordinary portable YAML/domain models that humans can inspect and edit.

## Agent repository guidance

Create `AGENTS.md` at the repository root. It should tell coding agents how to:

```text
webmcp inspect --json
webmcp test --json
webmcp test --adversarial --json
webmcp replay <failure-id> --json
```

Document `.webmcp/scenarios`, `.webmcp/failures` and trace artefacts. Explicitly state that saved failure reproductions are regression assets and must not be silently deleted.

## Closed-loop remediation workflow

The design must enable this external-agent loop:

```text
inspect -> generate/run -> fail -> minimise -> inspect trace
        -> agent edits source -> replay -> regression test
```

WebMCP Resilience itself must not edit the target application's source. It supplies deterministic evidence to the calling coding agent.

## Plan/dry-run

Provide a plan/dry-run operation that validates and expands a scenario without executing it. This is particularly important for autonomous coding agents.

## No separate AI execution semantics

Agent-driven tests must use exactly the same scenario runner, scheduler, faults, invariants and traces as human-driven tests. Do not create an `ai_mode` execution path.

# 19. MVP Acceptance Criteria

The MVP is complete when a user can:

1. Install the package.
2. Initialise a local WebMCP Resilience project.
3. Point it at a WebMCP-enabled web application.
4. Discover WebMCP tools.
5. Invoke WebMCP tools without an LLM.
6. Define a scenario in YAML.
7. Run agent and human actions in one scenario.
8. Overlap those actions.
9. Inject at least latency and duplicate invocation faults.
10. Define at least one system invariant.
11. Detect an invariant violation.
12. Produce an ordered execution trace.
13. Save a failure as a deterministic reproduction.
14. Replay that failure from the CLI.
15. Run the suite in GitHub Actions.
16. Produce a JUnit result.
17. Return versioned machine-readable results using `--json`.
18. Expose core operations through a reusable Command API.
19. Publish repository guidance in `AGENTS.md`.
20. Allow an external coding agent to inspect a failure, retrieve its trace and replay it without parsing human console output.

---

# 20. Delivery Plan

## Milestone 1 — Skeleton

Deliver:

- package layout
- pyproject
- Typer CLI
- config loading
- Pydantic scenario model
- basic unit tests
- reusable Command API boundary
- versioned result models
- `--json` output for implemented commands
- initial `AGENTS.md`

Commands should exist even if some return stubbed implementation errors.

## Milestone 2 — Browser/WebMCP Integration

Deliver:

- Playwright launcher
- browser adapter injection
- tool discovery
- deterministic tool invocation
- `webmcp inspect`

## Milestone 3 — Scenario Runner

Deliver:

- human actions
- agent actions
- deterministic scheduler
- trace recorder
- basic invariant evaluator
- `webmcp test`

## Milestone 4 — Faults and Replay

Deliver:

- latency
- duplicate invocation
- cancellation if browser support allows it
- navigation fault
- failure serialization
- `webmcp replay`

## Milestone 5 — Adversarial Exploration

Deliver:

- Hypothesis integration
- generated ordering/timing
- sequence shrinking
- minimal failure reproduction

## Milestone 6 — Agent Control Surface

Deliver:

- MCP server adapter over Command API
- capability discovery
- scenario plan/dry-run
- starter scenario synthesis
- end-to-end test proving an MCP client can inspect, run, retrieve a failure and replay it

## Milestone 7 — CI and Demo

Deliver:

- JUnit output
- GitHub Action example
- deliberately vulnerable checkout demo
- README walkthrough
- end-to-end tests

---

# 21. Quality Requirements

Code should be:

- typed
- testable
- modular
- async-safe
- straightforward rather than clever

Use Ruff for linting and formatting.

Use mypy or pyright for type checking.

Use pytest for the project's own tests.

Aim for good separation between:

- browser integration
- scenario modelling
- scheduling
- fault injection
- trace recording
- reporting

The browser/WebMCP integration must be replaceable without rewriting the engine.

---

# 22. Coding Conventions

Prefer:

- dataclasses or Pydantic models for structured domain data
- protocols/ABCs only where they genuinely improve replaceability
- small focused modules
- dependency injection through constructors rather than globals
- explicit async boundaries

Avoid:

- large framework dependencies
- hidden global state
- unnecessary plugin systems in v0.1
- speculative abstractions
- premature distributed architecture

---

# 23. First Implementation Task

Start with Milestone 1.

Produce:

```text
pyproject.toml
src/webmcp_resilience/
tests/
README.md
```

Implement:

```bash
webmcp --help
webmcp init
```

`webmcp init` must create a valid `.webmcp` project directory.

Add automated tests.

Then move to Milestone 2.

Do not attempt to implement the entire product in one enormous pass.

Commit logical milestones separately where possible.

---

# 24. Product Definition

The product succeeds when this loop works reliably:

```text
Discover
   ↓
Break
   ↓
Shrink
   ↓
Replay
   ↓
Fix
   ↓
Regress
```

The differentiated value is not simply calling WebMCP tools.

It is finding timing, state and concurrency failures that normal tool-level tests do not expose.

---

# 25. Product Tagline

> **Break your WebMCP application before an agent does.**

---

# Scope Correction — Read Before Implementation

Do not implement the complete agent control surface in v0.1.

```text
v0.1  Resilience Engine + machine-operable foundation
v0.2  Agent operability / MCP adapter
v0.3+ Intelligent assistance and scenario synthesis
```

## v0.1 Architecture

Implement:

```text
CLI
 │
 ▼
Command API
 │
 ▼
Resilience Core
```

The Command API is an internal typed Python application/service layer, NOT HTTP, a network service, an MCP server or an agent runtime.

The CLI MUST invoke this layer rather than engine internals.

## Package Adjustment

Add:

```text
src/webmcp_resilience/
├── commands/
│   ├── api.py
│   ├── requests.py
│   └── results.py
├── capabilities/
│   ├── model.py
│   └── compatibility.py
```

`commands/` is the stable application boundary. Rich rendering must remain outside it.

## Machine-Readable Results

Relevant v0.1 commands must support `--json`. Use one canonical result model and separate Rich/JSON renderers.

Results use a versioned envelope containing at least `contract_version`, `engine_version` and operation-specific structured data.

## Capability Compatibility

Saved scenarios, traces and failures must carry capability compatibility metadata. Initial capability groups are `scenario`, `faults`, `invariants` and `trace`.

Before replay, compare saved requirements with installed capabilities and reject semantic incompatibility explicitly. Never silently reinterpret a saved failure. Migration tooling is future scope.

## Validation

Add:

```bash
webmcp validate <scenario>
```

Validate schema, engine/capability compatibility, fault availability, invariant syntax and tool references where possible.

Do not implement the earlier generic dry-run/plan feature.

## AGENTS.md

Include concise repository guidance for coding agents. This is documentation, not a runtime product surface.

Document:

```bash
webmcp inspect --json
webmcp validate <scenario> --json
webmcp test <scenario> --json
webmcp replay <failure> --json
```

Also state that saved failures are regression artefacts, agents must not delete failures merely to make CI pass, and callers must not bypass the Command API.

## Execution Safety

Never create `ai_mode`, `agent_mode` or a privileged agent runner.

All callers eventually use:

```text
Command API
    │
    ▼
Resilience Core
```

This is deliberate dogfooding of the framework's resilience principles.

## Isolation Boundaries

Keep browser and future agent transport volatility separate:

```text
WebMCP Browser API → Browser Adapter → Canonical WebMCP Model → Engine
MCP/other client   → Transport Adapter → Command API          → Engine
```

Do not couple MCP transport concepts to browser WebMCP integration.

## v0.2 — Do Not Implement Yet

Defer:

- MCP server/control adapter
- runtime capability discovery exposed to agents
- agent-triggered tests through MCP
- failure/trace retrieval through MCP
- agent-triggered replay
- closed-loop coding-agent workflow

Design v0.1 so these can be added cleanly.

## v0.3+ — Explicitly Deferred

Do not implement scenario synthesis, LLM-generated scenarios, local SLM execution, autonomous exploration, cross-model regression or autonomous remediation.

## Revised v0.1 Commands

```bash
webmcp --help
webmcp init
webmcp inspect
webmcp validate
webmcp test
webmcp replay
```

Relevant commands support `--json`.

## Revised Implementation Order

### Milestone 1 — Foundation

Package skeleton, Pydantic models, typed Command API, result envelope, Rich/JSON renderers, config, `init`, `validate`, unit tests.

Acceptance: CLI does not call engine internals directly and Rich/JSON use identical result objects.

### Milestone 2 — Browser Integration

Playwright, isolated JS adapter, canonical WebMCP tool model, discovery, invocation and `inspect`.

### Milestone 3 — Resilience Execution

Scenario runner, agent/human actors, deterministic scheduler, state observation, invariants and traces.

### Milestone 4 — Fault and Replay

Latency, duplicate invocation, cancellation where supported, navigation, failure serialization, capability metadata, compatibility checking and replay.

### Milestone 5 — Adversarial Exploration

Hypothesis timing/order exploration, shrinking and minimal reproductions.

### Milestone 6 — CI and Demo

JUnit, GitHub Actions, vulnerable checkout demo, `AGENTS.md`, end-to-end docs.

Only after these milestones are stable should v0.2 MCP work begin.

## Scope Test

Before adding any v0.1 feature ask:

> Does this directly improve our ability to discover, reproduce or prevent behavioural failures in a WebMCP application?

If not, defer it.

The v0.1 loop remains:

```text
Discover → Break → Shrink → Replay → Fix → Regress
```
