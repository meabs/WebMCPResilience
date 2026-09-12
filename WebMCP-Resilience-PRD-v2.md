# WebMCP Resilience — Product Requirements Document

**Status:** Draft 0.1
**Date:** 11 September 2026
**Product type:** Open-source developer tooling
**Working name:** WebMCP Resilience

## 1. Executive Summary

WebMCP gives browser-based agents a structured way to discover and invoke tools exposed by web applications. The emerging ecosystem is increasingly capable at answering basic development questions such as whether a tool registers correctly, whether its schema is valid, whether it can be manually invoked, and whether a model can select an appropriate tool.

A significant gap remains between **tool correctness** and **application resilience**.

Real agentic applications are stateful and concurrent. Agents may retry calls, invoke tools in unexpected sequences, submit pathological arguments, continue while a human changes application state, encounter navigation during execution, or experience partial dependency failures. Existing WebMCP tooling does not comprehensively exercise these behaviours.

**WebMCP Resilience** is an adversarial, stateful and concurrency-testing framework for WebMCP applications.

Its purpose is simple:

> Prove that an agent-facing web application remains correct when agents, humans and failures interact in unexpected ways.

The product complements rather than replaces Chrome DevTools, WebMCP unit-testing libraries, browser grids and model-evaluation frameworks.

Its primary concepts are:

- **Scenarios** — reproducible tests of application behaviour.
- **Actors** — agents and humans operating concurrently.
- **State** — observable application/domain state.
- **Faults** — injected abnormal conditions.
- **Invariants** — conditions that must always remain true.
- **Traces** — complete execution records for diagnosis and replay.

The initial product will be an open-source CLI and Python testing framework, with later support for visual trajectory debugging and production telemetry.

---

## 2. Problem Statement

The current WebMCP testing ecosystem largely validates individual interactions:

```text
Tool registered?
      ↓
Schema valid?
      ↓
Tool callable?
      ↓
Expected response?
```

Production applications face a more difficult problem:

```text
What happens when calls overlap?
      ↓
What happens when the human changes state?
      ↓
What happens when the agent retries?
      ↓
What happens when execution is cancelled?
      ↓
What happens during navigation?
      ↓
What happens when dependencies fail?
      ↓
Does application state remain valid?
```

WebMCP introduces a particularly important concurrency problem because the same browser application may be controlled simultaneously through multiple interaction surfaces:

```text
              UI events
Human ─────────────────────┐
                           │
                    Application State
                           │
Agent ─────────────────────┘
          WebMCP tools
```

Traditional browser automation assumes a user controls the UI. API testing assumes a programmatic client controls an endpoint. WebMCP can expose both forms of control against the same live application state.

This creates new classes of race conditions, stale-state problems, duplicate side effects and inconsistent outcomes.

---

## 3. Product Vision

WebMCP Resilience should become the standard open-source framework for testing the **behavioural resilience** of WebMCP applications.

The product should answer:

> "Can I safely deploy this WebMCP-enabled application when I cannot control exactly how agents will interact with it?"

Longer term, it should provide a common event and trace model spanning pre-production testing and production observability.

---

## 4. Positioning

WebMCP Resilience deliberately occupies a different layer from existing tooling.

| Tool category | Primary question |
|---|---|
| Chrome DevTools | What tools does this page expose? |
| WebMCP unit testing | Does my tool register and execute? |
| Browser CI/grid | Does it work in this browser/runtime? |
| WebMCP eval frameworks | Can an agent choose and use the right tool? |
| Cross-model evaluation | How do different models interpret the contract? |
| **WebMCP Resilience** | **What happens when interactions and failures become hostile or concurrent?** |

Short positioning statement:

> **Chrome DevTools inspects it. Evals exercise the agent. WebMCP Resilience tries to break the application.**

---

## 5. Goals

### 5.1 Primary Goals

The product MUST:

1. Discover and invoke WebMCP tools in a real browser environment.
2. Execute deterministic WebMCP scenarios without requiring an LLM.
3. Model human and agent actors operating concurrently.
4. Inject failures into tool execution and application dependencies.
5. Generate pathological and boundary-case tool arguments.
6. Define and continuously validate application invariants.
7. detect race conditions and duplicate side effects.
8. Capture complete execution trajectories.
9. Reduce failures into deterministic reproducible scenarios where practical.
10. Run locally and in CI/CD pipelines.
11. Isolate browser/WebMCP API integration so specification changes do not infect the core framework.

### 5.2 Secondary Goals

The product SHOULD eventually:

- support model-driven agents alongside deterministic actors;
- provide visual trajectory replay;
- expose OpenTelemetry-compatible traces;
- support production WebMCP telemetry;
- compare behaviour across browser implementations;
- integrate with GitHub Actions and common CI systems;
- generate machine-readable JUnit/JSON/SARIF reports.

---

## 6. Non-Goals

The initial product WILL NOT attempt to:

- replace Chrome WebMCP DevTools;
- become another general WebMCP inspector;
- provide a WebMCP SDK for application developers;
- compete directly with model-selection/evaluation frameworks;
- provide a general-purpose browser automation framework;
- provide an LLM benchmark leaderboard;
- host a WebMCP tool registry;
- deploy or provision application infrastructure;
- provide production telemetry in the MVP.

---

## 7. Target Users

### Primary Persona — Web Application Engineer

Develops WebMCP-enabled applications and needs confidence that tools behave correctly beyond basic happy-path execution.

Needs:

- local testing;
- reproducible failures;
- CI integration;
- useful diagnostics;
- minimal new testing syntax.

### Secondary Persona — Solution / Platform Architect

Needs to establish engineering controls around agent-facing applications.

Needs:

- behavioural assurance;
- architectural invariants;
- evidence that concurrency and failure scenarios have been tested;
- reports suitable for engineering governance.

### Secondary Persona — QA / Reliability Engineer

Wants to apply resilience and property-based testing techniques to agent-facing web applications.

Needs:

- fault injection;
- generated scenarios;
- deterministic replay;
- failure minimisation;
- machine-readable CI output.

---

## 8. Core Product Concepts

### 8.1 Scenario

A scenario describes a test environment, actors, actions, faults and expected invariants.

Example:

```yaml
scenario: checkout-race

initial:
  cart.quantity: 1
  inventory.available: 2

actors:
  agent:
    - at: 0ms
      invoke: checkout
    - at: 250ms
      retry: checkout

  human:
    - at: 100ms
      ui:
        action: change_quantity
        value: 2

faults:
  - at: 150ms
    inject:
      service: payment
      latency: 2000ms

assert:
  orders.created: 1
  payments.captured: 1
  inventory.remaining: 0
```

### 8.2 Actor

An actor is a source of state-changing behaviour.

MVP actor types:

- `agent` — deterministic WebMCP tool invocation;
- `human` — browser/UI interaction through Playwright.

Future actors:

- OpenAI agent;
- Anthropic agent;
- Gemini agent;
- local/Ollama agent;
- custom agent adapter.

### 8.3 State

State represents observable application or domain facts.

State providers should be pluggable and may include:

- DOM state;
- JavaScript/application store;
- network responses;
- test APIs;
- database adapters;
- emitted events.

### 8.4 Invariant

An invariant is a condition that MUST remain true regardless of execution ordering.

Example:

```python
@webmcp.invariant
def never_double_charge(state):
    assert state.payment_count <= state.order_count
```

Typical invariants include:

- a payment is never captured twice;
- inventory never becomes negative;
- cancelled operations produce no side effects;
- unauthorised actions never succeed;
- completed orders cannot return to draft state;
- idempotent calls produce one business outcome.

### 8.5 Fault

Faults deliberately disturb normal execution.

MVP fault types:

- latency;
- timeout;
- HTTP error;
- tool cancellation;
- duplicate invocation;
- navigation during execution;
- component/page state mutation.

Later:

- network disconnection;
- dependency degradation;
- malformed responses;
- browser lifecycle events;
- authentication/session expiry.

### 8.6 Trace

Every execution produces an ordered event stream describing what occurred.

Example:

```text
T+0000 agent discovered checkout
T+0043 checkout invoked
T+0061 handler entered
T+0094 human changed quantity → 2
T+0102 state.version 18 → 19
T+0143 payment request sent
T+0201 agent retried checkout
T+0203 duplicate invocation detected
T+0340 navigation started
T+0347 cancellation fired
T+0351 payment completed
```

The trace becomes the foundation for failure analysis, replay and future production telemetry.

---

## 9. Functional Requirements

### FR1 — WebMCP Discovery

The framework MUST discover tools exposed by the active document through the available WebMCP testing interface.

It MUST record:

- tool name;
- description;
- input schema;
- output schema where available;
- annotations;
- lifecycle information;
- registration/unregistration events where observable.

### FR2 — Deterministic Invocation

Tests MUST be able to invoke tools without an LLM.

```python
result = await webmcp.call(
    'add_to_basket',
    {
        'productId': 'ABC123',
        'quantity': 2
    }
)
```

This is essential for reproducibility.

### FR3 — Concurrent Actors

The execution engine MUST allow human and agent actions to overlap.

The scheduler MUST support:

- explicit timing;
- barriers;
- randomised ordering;
- generated interleavings.

### FR4 — Interleaving Exploration

Given concurrent actions, the framework SHOULD explore alternative valid execution orders.

For example:

```text
Human → Agent → Retry
Agent → Human → Retry
Agent → Retry → Human
```

The purpose is to discover race conditions that a single deterministic test ordering would miss.

### FR5 — Property-Based Generation

The framework SHOULD integrate with Hypothesis to generate:

- boundary values;
- missing optional/required properties;
- invalid types;
- unexpected enum values;
- extreme strings;
- large arrays;
- Unicode values;
- repeated calls;
- sequences of valid actions.

### FR6 — Stateful Testing

The framework MUST support tests where correctness is defined by final or intermediate application state rather than a hardcoded tool-call trajectory.

### FR7 — Fault Injection

Tests MUST be able to inject controlled faults at known points in execution.

Example:

```python
async with webmcp.inject_latency('submit_order', 5000):
    call = webmcp.call('submit_order', order)
    await call.cancel()

assert await orders.count() == 0
assert await payments.count() == 0
```

### FR8 — Invariant Monitoring

Invariants MUST be evaluated throughout scenario execution where possible, not merely at test completion.

When an invariant fails, the report MUST identify:

- failed invariant;
- state at failure;
- preceding actions;
- actors involved;
- execution ordering.

### FR9 — Failure Reduction

For generated or concurrency failures, the framework SHOULD attempt to minimise the sequence required to reproduce the issue.

Output example:

```text
Minimal reproduction saved:
.webmcp/failures/checkout-7c92.yaml
```

### FR10 — Deterministic Replay

Saved failures MUST be replayable without the original random exploration process.

```bash
webmcp resilience replay .webmcp/failures/checkout-7c92.yaml
```

### FR11 — Execution Trace

Every scenario MUST produce a structured execution trace.

Supported initial formats:

- human-readable timeline;
- JSON.

Later:

- OpenTelemetry;
- interactive visual timeline.

### FR12 — CI Integration

The CLI MUST return standard process exit codes and SHOULD output:

- console report;
- JSON;
- JUnit XML.

Future support:

- SARIF;
- GitHub annotations;
- CI artefact upload.

---

## 10. CLI Experience

Primary command:

```bash
webmcp resilience test
```

Example output:

```text
WebMCP Resilience

Discovery
✓ 18 tools discovered
✓ schemas valid

Baseline
✓ 142 deterministic scenarios

Adversarial
✓ malformed arguments
✓ boundary values
✓ duplicate invocation
✓ cancellation

Concurrency
✗ checkout.concurrent_quantity_change

Invariant violated:
  payments.captured <= orders.created

Timeline:
  agent checkout          +0ms
  human qty 1→2          +43ms
  agent checkout retry   +91ms
  payment captured      +128ms
  payment captured      +147ms

Minimal reproduction:
  .webmcp/failures/checkout-7c92.yaml
```

Additional commands:

```bash
webmcp resilience discover
webmcp resilience test
webmcp resilience fuzz
webmcp resilience replay <scenario>
webmcp resilience trace <run>
```

---

## 11. Python API

Python is the preferred orchestration language because the project can leverage pytest, Hypothesis, asyncio and the broader Python testing ecosystem.

Illustrative API:

```python
from webmcp_resilience import Scenario

scenario = Scenario('checkout')

scenario.tool('checkout')
scenario.ui('change_quantity')
scenario.fault('payment_latency')
scenario.fault('tool_cancel')
scenario.fault('navigation')

scenario.invariant(
    lambda state: state.payment_count <= state.order_count
)

scenario.explore()
```

pytest integration:

```python
@pytest.mark.webmcp
async def test_checkout_is_idempotent(webmcp):
    await webmcp.concurrent(
        webmcp.call('checkout'),
        webmcp.call('checkout')
    )

    assert await webmcp.state('orders.created') == 1
    assert await webmcp.state('payments.captured') == 1
```

---

## 12. Architecture

```text
                     CLI / pytest
                          │
                          ▼
                 Scenario Engine
                          │
       ┌──────────────────┼──────────────────┐
       │                  │                  │
       ▼                  ▼                  ▼
 Actor Scheduler     Fault Engine      Invariant Engine
       │                  │                  │
       └──────────────────┼──────────────────┘
                          │
                          ▼
                     Event Bus
                          │
       ┌──────────────────┼──────────────────┐
       │                  │                  │
       ▼                  ▼                  ▼
 Browser Adapter     State Adapters      Trace Recorder
       │
       ▼
 Playwright / Chromium
       │
       ▼
 WebMCP API Adapter
       │
       ▼
 navigator/document model context
```

### Architectural Principle — Isolate WebMCP API Churn

The WebMCP specification remains evolving. No core test logic should depend directly on a specific browser API shape.

All platform access MUST go through an adapter:

```text
Core Framework
      │
WebMCP Adapter Interface
      │
      ├── ChromiumCurrentAdapter
      ├── ChromiumLegacyAdapter
      ├── PolyfillAdapter
      └── FutureBrowserAdapter
```

This is a fundamental product requirement, not an implementation convenience.

---

## 13. Unified Event Model

Testing, replay and future observability SHOULD share a common event format.

Example:

```json
{
  "timestamp": "2026-09-11T20:42:01.210Z",
  "session": "abc123",
  "scenario": "checkout-race",
  "actor": "agent",
  "type": "tool.invoke",
  "tool": "checkout",
  "arguments": {},
  "stateVersion": 42
}
```

Core event categories:

```text
tool.discovered
tool.registered
tool.unregistered
tool.invoke
tool.result
tool.error
tool.cancel

ui.action
ui.navigation

state.change

fault.injected
fault.recovered

invariant.checked
invariant.failed
```

This event model is strategically important because it allows the same conceptual model to later support production telemetry.

---

## 14. Flight Recorder

Every scenario run MUST generate an execution record.

Initial representation:

```text
AGENT     ──invoke────────retry─────────────────
              │             │
UI        ───────quantity=2────────navigate─────
              │                       │
TOOL      ─────execute────────────────X─────────
                     │
PAYMENT   ───────────request────────────success─
```

MVP provides textual and JSON output.

A later web UI will provide:

- timeline navigation;
- actor lanes;
- tool visibility at each point;
- input/output inspection;
- state differences;
- fault markers;
- invariant failures;
- replay controls.

---

## 15. Adversarial Test Categories

### Argument Attacks

- malformed types;
- missing required values;
- unexpected fields;
- boundary values;
- extreme payload sizes;
- Unicode/encoding edge cases.

### Invocation Behaviour

- duplicate calls;
- rapid repeated calls;
- out-of-order calls;
- stale arguments;
- cancellation;
- retries.

### Human/Agent Concurrency

- UI mutation while a tool executes;
- navigation during execution;
- human cancellation;
- human confirmation while agent state changes;
- competing writes to the same state.

### Dependency Failures

- latency;
- timeout;
- HTTP 429;
- HTTP 5xx;
- partial response;
- dependency recovery.

### Security Scenarios — Later Phase

- confused-deputy behaviour;
- parameter smuggling;
- indirect prompt-injection influence;
- unexpected consequential actions;
- cross-origin state mutation;
- missing confirmation;
- privilege escalation attempts.

Security testing should favour dynamic behaviour over purely static schema scoring.

---

## 16. Model-Driven Testing

LLMs are NOT required for core testing.

Deterministic testing answers:

> Is the application behaviour correct?

Model evaluation answers:

> Can this model correctly understand and operate the application?

These concerns should remain separate.

Future adapters may support:

```text
agents/
├── deterministic
├── openai
├── anthropic
├── gemini
└── ollama
```

Existing WebMCP evaluation frameworks should be integrated where practical rather than reimplemented.

---

## 17. Production Observability — Future Product Direction

The event model can later support a lightweight production telemetry SDK.

Conceptual usage:

```javascript
import { observeWebMCP } from '@webmcp-resilience/telemetry'

observeWebMCP({
  endpoint: '/webmcp-telemetry',
  sampleRate: 0.1,
  redact: ['email', 'customerId']
})
```

Potential metrics:

```text
tool.calls
tool.duration
tool.success
tool.failure
tool.cancelled
tool.schema_failure
tool.retry
tool.concurrent_ui_change
```

Example operational view:

```text
searchProducts

Success               96.2%
p95                    412ms
Cancelled               1.8%
Schema rejection        0.7%
Agent retries            4.3%

Most common rejected value:
  maxPrice = "$100"
  expected = number
```

This is explicitly outside the MVP but should influence event-model design from the beginning.

---

## 18. Repository Structure

Proposed structure:

```text
webmcp-resilience/
├── webmcp_resilience/
│   ├── browser/
│   ├── adapters/
│   ├── scenarios/
│   ├── actors/
│   ├── state/
│   ├── faults/
│   ├── invariants/
│   ├── fuzz/
│   ├── tracing/
│   └── reporters/
├── pytest_webmcp/
├── examples/
│   ├── checkout/
│   ├── booking/
│   └── todo/
├── github-action/
├── docs/
└── tests/
```

---

## 19. MVP Scope

The MVP MUST demonstrate the unique proposition rather than trying to cover the entire WebMCP ecosystem.

### MVP Capabilities

1. Chromium/Playwright execution.
2. WebMCP tool discovery.
3. Deterministic tool invocation.
4. pytest integration.
5. Human actor through Playwright.
6. Concurrent human + agent actions.
7. Basic scheduling/interleaving.
8. Tool retries and duplicate invocation.
9. Cancellation testing.
10. Latency and HTTP failure injection.
11. User-defined invariants.
12. JSON and textual trace.
13. Deterministic replay.
14. JUnit output.
15. GitHub Actions example.

### MVP Showcase Scenario

The primary demo SHOULD be a checkout application because the invariants are immediately understandable.

Demonstrate:

```text
Agent starts checkout
        ↓
Human changes basket
        ↓
Payment becomes slow
        ↓
Agent retries
        ↓
Naive implementation double-charges
        ↓
WebMCP Resilience catches invariant violation
        ↓
Failure reduced to deterministic replay
```

This explains the project's purpose far more effectively than a collection of schema validation examples.

---

## 20. Delivery Roadmap

### v0.1 — Foundation

- WebMCP browser adapter;
- Playwright integration;
- tool discovery/invocation;
- pytest fixture;
- scenario runner;
- JSON/text trace;
- CI execution.

### v0.2 — Resilience

- multiple actors;
- concurrent actions;
- retries;
- cancellation;
- latency injection;
- HTTP failures;
- invariants.

This release establishes the project's real differentiation.

### v0.3 — Exploration

- Hypothesis integration;
- generated arguments;
- generated action sequences;
- interleaving exploration;
- failure shrinking;
- deterministic reproduction files.

### v0.4 — Flight Recorder

- visual trajectory viewer;
- actor lanes;
- state diffs;
- fault/invariant markers;
- replay controls;
- OpenTelemetry export.

### v0.5 — Agent Adapters

- existing WebMCP eval integration;
- OpenAI adapter;
- Anthropic adapter;
- Gemini adapter;
- Ollama/local-model adapter.

### v1.x — Production Assurance

- telemetry SDK;
- production trajectory analytics;
- schema/behaviour drift;
- browser compatibility matrix;
- security scenario packs.

---

## 21. Success Metrics

Early open-source metrics:

- GitHub adoption/stars;
- package downloads;
- external contributors;
- applications using the GitHub Action;
- number of reproducible bugs discovered using the framework;
- integrations with existing WebMCP projects.

Product-quality metrics:

- percentage of generated failures that can be deterministically replayed;
- average failure-reduction ratio;
- test execution overhead;
- flaky-test rate;
- compatibility across supported Chromium versions.

The most meaningful qualitative metric is:

> Developers find failures with WebMCP Resilience that conventional browser/unit tests did not find.

---

## 22. Risks

### WebMCP Specification Churn

**Risk:** Browser APIs and lifecycle semantics continue changing.

**Mitigation:** Strict adapter boundary around all WebMCP platform integration.

### Browser Vendor Adoption

**Risk:** WebMCP remains predominantly Chromium-specific.

**Mitigation:** Keep the core framework browser-neutral; treat browser integration as plugins/adapters.

### Existing Tools Expand

**Risk:** Official/community testing frameworks add adversarial testing.

**Mitigation:** Focus differentiation on state machines, concurrency, invariant exploration, fault injection and failure reduction rather than generic test execution.

### Non-Deterministic Tests Become Flaky

**Risk:** Concurrency testing creates difficult-to-reproduce failures.

**Mitigation:** Seeded scheduling, complete event capture and deterministic failure replay are core requirements.

### Scope Expansion

**Risk:** Project drifts into generic agent evaluation, browser testing, observability and security scanning simultaneously.

**Mitigation:** MVP is exclusively about behavioural resilience of WebMCP applications.

---

## 23. Design Principles

### Determinism Before AI

An LLM should not be required to reproduce a software defect.

### Test State, Not Just Trajectories

Correct final and intermediate state matters more than whether an agent followed one predetermined sequence.

### Assume Multiple Actors

Human and agent interactions are first-class concurrent operations.

### Invariants Over Examples

A strong invariant can expose failures across thousands of generated scenarios.

### Failures Must Be Explainable

Finding a race condition without producing a reproducible trace merely transfers suffering from QA to engineering.

### Integrate, Don't Rebuild

Use Playwright, pytest, Hypothesis, OpenTelemetry and existing WebMCP evaluation projects rather than creating weaker replacements.

### Expect the Standard to Change

WebMCP-specific browser integration remains isolated from the scenario and resilience engine.

---

## 23A. Agent Control Surface

WebMCP Resilience must be designed so coding agents can autonomously inspect, generate, execute, diagnose and replay resilience tests without scraping terminal prose.

### Architecture

```text
                 ┌──────────────┐
                 │     CLI      │
                 └──────┬───────┘
                        │
                 ┌──────▼───────┐
                 │ Command API  │
                 └──────┬───────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
          ▼             ▼             ▼
       CLI JSON        MCP         Python API
          │             │             │
          └─────────────┴─────────────┘
                        │
                        ▼
                Resilience Core
```

The Command API is the stable application boundary. CLI, MCP and Python integrations must delegate to it rather than reimplementing behaviour.

### Required machine-readable operations

The agent interface should expose equivalents of:

```text
list_capabilities
inspect_site
list_tools
list_scenarios
create_scenario
run_scenario
run_adversarial
get_failure
replay_failure
minimise_failure
get_trace
```

An MCP server should expose these operations to compatible coding agents. The MCP layer is a control surface for the testing framework; it is separate from the WebMCP interface being tested.

### Structured results

Every command must support machine-readable output. Results should use a versioned envelope rather than requiring agents to parse Rich console text.

Example:

```json
{
  "schema_version": "0.1",
  "status": "failed",
  "scenario": "checkout-concurrency",
  "failure_id": "fail_7c92",
  "invariant": "payments.captured <= 1",
  "observed": {
    "payments.captured": 2
  },
  "minimal_sequence": [
    "agent.checkout",
    "human.quantity_change",
    "agent.retry"
  ],
  "replay_command": "webmcp replay fail_7c92 --json"
}
```

CLI commands should support `--json` and stable exit codes.

### Capability discovery

Agents must be able to discover supported actions, faults, state probes, invariant operators and browser capabilities before creating a scenario. This avoids agents inventing unsupported DSL constructs.

The project should maintain a machine-readable capability description under `.webmcp/capabilities.json` or expose the equivalent dynamically through the Command API.

### Scenario synthesis

A coding agent should be able to request a starter scenario based on discovered WebMCP tools and a testing focus such as:

```text
concurrency
retry
cancellation
state mutation
boundary inputs
```

Scenario synthesis must produce normal portable scenario definitions. It must not create a separate opaque AI-only test format.

### Repository guidance

The repository should include an `AGENTS.md` containing concise instructions for coding agents, including:

```text
webmcp inspect --json
webmcp test --json
webmcp test --adversarial --json
webmcp replay <failure-id> --json
```

It should explain the scenario directory, failure artefacts, trace files and the rule that saved failures are regression assets and should not be silently deleted.

### Closed-loop remediation

The framework must support this autonomous engineering loop:

```text
Agent inspects application
        ↓
Agent creates/selects resilience scenario
        ↓
Framework discovers failure
        ↓
Framework minimises failure
        ↓
Agent retrieves trace and reproduction
        ↓
Agent modifies application source
        ↓
Framework replays exact failure
        ↓
Agent runs adversarial regression suite
        ↓
Framework verifies invariants
```

WebMCP Resilience should provide evidence and reproduction data, not autonomously modify application source itself. Source remediation remains the responsibility of the calling coding agent or developer.

### Agent safety and determinism

Agent-driven execution must use the same scenario engine, fault engine and invariant evaluator as human-driven execution. There must be no separate execution semantics for an "AI mode."

Support a dry-run/plan operation so an agent can validate a generated scenario before execution. Consequential or destructive test operations should remain constrained to the explicitly configured test target.

### Future agent adapters

LLM-driven test exploration may later be supported through adapters for hosted and local models, but it is not required for core correctness testing. Deterministic and property-based execution remains authoritative.

---

## 24. Open Questions

1. Should the package name be `webmcp-resilience`, `pytest-webmcp-resilience`, or a shorter independent brand?
2. What state adapter should be provided as the default beyond DOM inspection?
3. Can browser/network fault injection be handled entirely through Playwright, or is a proxy layer worthwhile?
4. How much scheduling control can reliably be achieved around browser-side asynchronous handlers?
5. Should failure scenarios use YAML as the canonical portable representation?
6. Should the trace event schema be designed as OpenTelemetry semantic conventions from v0.1?
7. Can WebMCP tool execution expose enough lifecycle information for reliable cancellation and navigation-race testing across spec versions?
8. Which existing WebMCP eval framework should become the first model-agent adapter?
9. Should the MCP control server ship in v0.1 or v0.2 after the Command API and JSON CLI are stable?
10. What minimum capability schema should be guaranteed for coding-agent interoperability?

---

## 25. Product Thesis

The WebMCP ecosystem is rapidly solving **discovery, registration, schema validation and basic execution**.

The harder problem comes next.

Agent-facing websites are concurrent stateful systems in which humans, agents, browser lifecycle events and backend dependencies can all modify the outcome of an interaction.

Testing those systems requires more than proving that a tool handler returns the expected JSON.

**WebMCP Resilience exists to deliberately create the conditions under which agent-facing applications fail, identify violated business invariants, and turn those failures into deterministic reproductions that engineers can fix.**

That is the product boundary and its primary differentiation.

---

# Scope Clarification — Agent Operability

This section supersedes earlier wording that implies the complete agent-facing control surface is required for v0.1.

## Release Boundary

### v0.1 — Resilience Engine

v0.1 is exclusively concerned with behavioural resilience plus the machine-operable foundation needed for CI, reproducibility and future adapters.

Required:

- WebMCP discovery and deterministic invocation
- human/browser actors and concurrent execution
- fault injection and invariant evaluation
- execution traces, failure recording, shrinking and deterministic replay
- CI integration
- internal typed Command API
- versioned JSON results
- capability compatibility metadata
- `AGENTS.md` usage guidance

The Command API is an internal application/service boundary, not an HTTP or network API:

```text
CLI
 │
 ▼
Command API
 │
 ▼
Resilience Core
```

### v0.2 — Agent Operability

v0.2 adds an MCP control adapter, runtime capability discovery, agent-triggered execution, failure/trace retrieval and closed-loop fix → replay → regress workflows.

The MCP adapter MUST remain a transport over the existing Command API and MUST NOT introduce alternative execution semantics.

### v0.3+ — Intelligent Assistance

Explicitly deferred:

- scenario synthesis
- LLM-generated scenarios
- autonomous test generation/exploration
- cross-model regression
- local SLM orchestration
- autonomous remediation inside the framework

## FR13 — Command API

All v0.1 user-facing operations MUST invoke a typed internal Command API independent of terminal presentation. It MUST expose structured request/result models, contain no Rich presentation logic, and provide the future integration boundary for other transports.

## FR14 — Structured Results

Relevant v0.1 commands MUST support versioned machine-readable JSON output. Terminal and JSON output MUST render the same underlying result object.

Example:

```json
{
  "contract_version": "1",
  "engine_version": "0.1.0",
  "status": "failed",
  "scenario": "checkout-concurrency",
  "failure_id": "fail_7c92",
  "failed_invariant": "payments.captured <= 1",
  "observed": {
    "payments.captured": 2
  }
}
```

## FR15 — Capability Contract

Scenarios, failures and traces MUST carry compatibility metadata sufficient to determine whether they can be safely executed by the installed engine.

Capability groups SHOULD initially include:

- scenario schema
- fault model
- invariant model
- trace schema

Example:

```yaml
requires:
  resilience: '>=0.1,<0.3'
  capabilities:
    scenario: '1'
    faults: '1'
    invariants: '1'
    trace: '1'
```

Semantic incompatibilities MUST fail explicitly rather than being silently translated. Replay means replay; changed semantics must not be disguised as compatibility.

## FR16 — Execution Safety

Programmatic execution MUST use exactly the same scenario engine, target restrictions, fault controls, invariant semantics and replay behaviour as CLI execution.

There MUST NOT be a privileged `ai_mode` or alternative agent execution path.

## FR17 — Agent Control Adapter

**Target release: v0.2.**

An MCP adapter MAY expose Command API operations to coding agents. It MUST use existing request/result models, preserve deterministic replay and target restrictions, and introduce no separate testing semantics.

FR17 is not a v0.1 acceptance criterion.

# Product Principle — Dogfood Agent Resilience

WebMCP Resilience tests applications against concurrent, erroneous and adversarial agent behaviour. Its own programmatic surface must therefore apply the same principles: deterministic execution, explicit capabilities, constrained targets, versioned contracts and reproducible actions.

```text
Agent
  │
  ▼
Transport Adapter
  │
  ▼
Command API
  │
  ▼
Resilience Core
```

No agent integration may bypass the Command API to reach engine internals.

# Compatibility Architecture

Browser/spec churn terminates at the Browser Adapter:

```text
WebMCP Browser API
        │
        ▼
Browser Adapter
        │
        ▼
Canonical WebMCP Model
        │
        ▼
Resilience Core
```

Automation/transport churn terminates at the Command Adapter:

```text
CLI / Future MCP Client
        │
        ▼
Command Adapter
        │
        ▼
Canonical Command Model
        │
        ▼
Resilience Core
```

# Validation Instead of Dry Run

A general-purpose dry-run/plan mode is NOT required for v0.1.

v0.1 MUST instead provide:

```bash
webmcp validate <scenario>
```

Validation should check scenario syntax, schema/capability compatibility, fault availability, invariant syntax and referenced WebMCP tools where discovery is possible. It does not promise to predict runtime behaviour.

# Revised Roadmap

## v0.1

```text
Discover
Execute
Fault
Invariant
Trace
Shrink
Replay
CI
JSON
```

## v0.2

```text
MCP adapter
Runtime capability discovery
Agent execution
Failure/trace retrieval
Closed-loop fix → replay → regress
```

## v0.3+

```text
Scenario synthesis
LLM-generated tests
Autonomous exploration
Cross-model evaluation
Local model adapters
Advanced remediation assistance
```

# Revised MVP Scope Guard

The v0.1 MVP is exclusively about **behavioural resilience**. Machine-readable results and the Command API are included as foundations for CI, reproducibility and future integrations, not because v0.1 is an agent-tooling product.

Explicitly outside v0.1: MCP server/control plane, scenario synthesis, LLM orchestration, autonomous remediation, runtime agent capability negotiation, cross-model testing, production observability, remote APIs and dashboards.

For every proposed v0.1 feature ask:

> Does this directly improve our ability to discover, reproduce or prevent behavioural failures in a WebMCP application?

If not, defer it.
