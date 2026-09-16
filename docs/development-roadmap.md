# WebMCP Resilience: application review and development roadmap

Reviewed 16 September 2026 against working tree based on `2310744`. The README had existing uncommitted edits and was left unchanged. This is a development proposal, not a commitment to delivery dates or a claim of protocol certification.

## Recommendation

Build the trusted resilience regression layer for WebMCP applications: prove that human actions, tool calls, retries, cancellation and navigation preserve application invariants, then give developers portable evidence that reproduces the same failure.

The current product already implements much of that workflow. Prioritize the truthfulness of its results and native-browser compatibility before adding more UI or broader agent features. Google’s inspection and evaluation tools make generic discovery and tool-call testing a weak place to differentiate. The opportunity is shared application state, failure identity, reproducibility and useful CI gates.

Primary customer hypothesis: frontend/platform engineers introducing WebMCP into stateful applications such as reservations, checkout and account workflows. Validate this with three independent pilot teams; demand and willingness to pay have not been established by this review.

## Current application

This is a Python 3.12+ developer tool, version 0.1.0, with Playwright execution, Pydantic/YAML scenarios, a Typer CLI, Textual console and local MCP control adapter. It is not primarily a hosted web application.

| Area | Existing implementation | Assessment |
| --- | --- | --- |
| Execution | Timed human/tool actions, six fault types, state and result invariants | Substantial foundation; timing and cancellation semantics need tightening |
| Exploration | Same-offset permutations and seeded one-millisecond timing variations | Useful bounded search concept; current generation is not computationally bounded |
| Evidence | Versioned bundles, traces, network metadata, screenshots, redaction, approvals | Strong product boundary; preserve it |
| Replay | Saved schedules, capability and tool-contract checks, strict mode, offline diff | Valuable differentiation; logical ordering is not deterministic physical timing |
| Failure reduction | Fresh browser runs while removing actions | Does not require the same failure to recur |
| Interfaces | CLI, console, pytest fixture and local MCP adapter | Keep one execution boundary; fix fixture configuration parity |
| Safety | Explicit mutation authority and agent startup policy | Useful controls; tool hints are declarations, not proof of harmlessness |
| Distribution | Python packaging, demo fixture, GitHub Actions | Needs clean-install verification and explicit native support matrix |

Evidence: [architecture](architecture.md), [commands](../src/webmcp_resilience/commands.py), [runner](../src/webmcp_resilience/engine/runner.py), [bundle model](../src/webmcp_resilience/models/bundle.py), [agent control](../src/webmcp_resilience/agent_control.py), [CI](../.github/workflows/test.yml).

### Verification performed

`.venv/bin/pytest -q` passed: **131 passed in 35.56 seconds**, including browser-backed tests. This establishes the checked-in test baseline, not native WebMCP conformance: the end-to-end tests use compatibility fixtures and do not require a native API/flag profile. No application behavior was changed during this review.

A small instrumented execution of `schedules()` with eight simultaneous actions and `limit=12` returned 12 schedules but enumerated **40,320 permutations**. Other findings below are code-inspection findings unless explicitly identified as exercised.

### Findings that should shape priorities

| Priority | Finding and evidence | Consequence / next action |
| --- | --- | --- |
| P0 | [Explorer](../src/webmcp_resilience/engine/explorer.py), `schedules`, materializes `list(permutations(group))` before slicing | Factorial work despite a small limit. Use lazy bounded generation, explicit budgets and a large-group regression |
| P0 | [CommandAPI.run](../src/webmcp_resilience/commands.py), nested `reproduces`, returns true for any exception | A different failure can replace the original during minimization. Match failure kind, invariant/tool and stable signature; distinguish harness errors |
| P0 | [Runner](../src/webmcp_resilience/engine/runner.py), `_execute`, cancels the first unfinished task without matching `action.cancel` | With multiple pending tools, the wrong call can be cancelled. Add stable invocation references and matching semantics |
| P0 | [Adapter](../src/webmcp_resilience/browser/adapter.js) always JSON-stringifies arguments | Chrome documents string arguments as deprecated from version 155. Add tested version/capability profiles; do not assume installed Chromium 151 is already broken |
| P1 | [Adapter](../src/webmcp_resilience/browser/adapter.js) discovers with `fromOrigins` but invokes using unscoped discovery and name-only selection | Cross-origin tools can disappear at invocation; duplicate names across frames are ambiguous. Preserve origin/frame identity and the discovered tool handle |
| P1 | [Runner](../src/webmcp_resilience/engine/runner.py), `_run_group`, prestarts eligible tools ahead of UI regardless of the selected permutation | Some nominal permutations collapse to the same dispatch behavior. Record effective dispatch and measure unique schedules rather than claiming all selected orders were exercised |
| P1 | [CommandAPI](../src/webmcp_resilience/commands.py) opens a fresh browser for each candidate but defines no backend reset contract | Browser freshness does not reset server state. Add explicit authorized setup/reset hooks and verify initial observable state |
| P1 | [Pytest fixture](../src/webmcp_resilience/pytest_plugin.py) omits `browser_channel` | Native configuration can differ between CLI and pytest. Share browser launch configuration |
| P1 | [Runner](../src/webmcp_resilience/engine/runner.py) records lifecycle events without refreshing cached descriptors; background exceptions are gathered without inspection | Dynamic registration and navigation failures can be misrepresented. Test stale handles, changed annotations and background failure propagation |
| P2 | [README](../README.md) quickstart has a malformed virtualenv command; CI installs editable source only | Repair onboarding and smoke-test installed wheels outside the checkout |

Additional investigation: timeout currently wraps the Python invocation wait; prove whether browser-side work is actually aborted and whether side effects continue afterward. Treat this as an unresolved behavioral question, not a verified defect. Likewise, classify a host without the fixture marker as “unidentified” unless stronger evidence establishes that it is native.

## Landscape and protocol direction

The September 15 draft identifies WebMCP as a **Draft Community Group Report**, not a W3C Standard or Standards Track specification. It exposes browser-local tools; it should not be treated as interchangeable with the MCP JSON-RPC client/server protocol. Microsoft and Google edit the draft. These facts support investing in an adaptable browser boundary, not promising universal support. [Current draft](https://webmachinelearning.github.io/webmcp/)

Chrome documents an origin trial starting in Chrome 149 and a local testing flag. Both imperative JavaScript tools and declarative HTML forms are part of the developer surface. Native support must therefore be tested against specific binaries, channels, flags and API profiles. [Chrome overview](https://developer.chrome.com/docs/ai/webmcp)

| Layer | Examples / source | Implication |
| --- | --- | --- |
| Browser inspection and developer utilities | [GoogleChromeLabs WebMCP tools](https://github.com/GoogleChromeLabs/webmcp-tools) | Integrate with existing discovery/debugging workflows; do not build another general inspector |
| Agent/tool evaluation | [GoogleChromeLabs WebMCP tools](https://github.com/GoogleChromeLabs/webmcp-tools) | Separate agent task success from application-state correctness; use eval outputs as inputs to reviewed scenarios where useful |
| Protocol behavior | [WebMCP draft and linked WPT suite](https://webmachinelearning.github.io/webmcp/) | Use browser tests as compatibility evidence; do not label a resilience pass as protocol certification |
| Browser automation | [Playwright](https://playwright.dev/docs/intro), [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Reuse execution infrastructure; differentiate through fault schedules and failure evidence |
| MCP debugging | [MCP Inspector](https://github.com/modelcontextprotocol/inspector) | Relevant to this application's local MCP adapter; not a substitute for native WebMCP testing |
| SDKs, polyfills and framework integrations | See [source-linked landscape research](research/webmcp-landscape-2026-09-16.md) | Potential fixture and distribution partners; label polyfill/native results separately |

Near-term compatibility work is concrete: Chrome documents object arguments, string-argument deprecation from 155, unregister-without-cancelling behavior from 153, cancellation signals, tool-change events and origin-scoped discovery. These are documented version transitions, not proof that every installed browser supports them. [Imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api)

Strategic inference: lifecycle and shared-state behavior will matter increasingly as tools are tied to component mount/unmount and forms. Add coverage for forms, navigation, registration withdrawal, permissions and human confirmation. Cross-browser availability, final standardization dates and wider agent adoption remain uncertain; gate investment on verified implementations and pilot needs. The companion research records proposals separately from implemented/documented capabilities.

## Delivery roadmap

Planning assumption: one experienced full-time engineer, with access to pilot application owners. Windows are estimates from project start. Complete acceptance gates before expanding scope; protocol changes may alter effort.

### Phase 1 — Trustworthy results and native baseline (weeks 1–3)

**Outcome:** developers can trust that a reported failure and reduced reproduction describe the same bug on an identified runtime.

1. Fix bounded exploration, targeted cancellation and failure-signature matching. Introduce explicit run/reduction time budgets and report exhausted budgets.
2. Record requested, dispatched and completed actions distinctly; report actual executed/unique schedule counts. Preserve user-declared per-actor ordering unless the scenario explicitly permits reordering.
3. Add explicit native profiles and a separate compatibility-host lane. Support documented argument encoding through a selected profile, with no retry of a potentially mutating call just to guess the API shape.
4. Add native fixtures and pinned browser CI, plus a non-blocking forward-compatibility lane. Include fixture configuration parity and clean-wheel CLI smoke tests.
5. Fix quickstart instructions. Publish an honest supported-runtime table and limitations.

**Acceptance:** a 20-action simultaneous scenario respects its generation budget; cancellation selects the declared invocation with two tools pending; minimization rejects an unrelated exception; native preflight/run/replay pass without fixture fallback; supported argument profiles pass; original failure artifacts remain intact.

**Dependencies:** a verified native browser binary and isolated fixture. If a newer version is unavailable, test its documented profile with a clearly labeled contract fixture and leave native support unclaimed.

### Phase 2 — Real WebMCP lifecycle coverage (weeks 4–7)

**Outcome:** resilience checks cover real forms, component lifecycles and origin boundaries.

1. Preserve tool identity across discovery/invocation; add frame/origin-qualified selectors and explicit ambiguity errors. Keep handles in the live browser and stable identity evidence in bundles.
2. Add declarative form fixtures: validation failure, user edits during execution, submission, duplicate submission and navigation/result handling.
3. Cover registration withdrawal during execution, tool replacement, `toolchange`, stale permissions and navigation reattachment.
4. Define cancellation versus timeout versus unregister behavior. Assert continued/aborted work and resulting application state independently.
5. Introduce authorized setup/reset and initial-state checks, suitable for server-backed test environments. Add repeated replay with reproduction counts and an “inconclusive/flaky” outcome.
6. Version changed scheduling/identity semantics. Keep old bundles readable and explicitly reject incompatible replay rather than silently reinterpret them.

**Acceptance:** an imperative fixture, declarative fixture and multi-frame fixture each demonstrate known failures and fixed controls; two identically named tools are resolved correctly; backend state is reset and checked; pinned deterministic fixtures reproduce the same signature on 20/20 runs. Do not generalize that target to all external applications.

**Dependencies:** Phase 1 identities, budgets and native lanes. Keep application state an explicit declared boundary, not inferred from arbitrary DOM text.

### Phase 3 — Adoption and CI workflow (weeks 8–10)

**Outcome:** independent teams can integrate the tool without author support.

1. Extend existing initialization/console authoring with inventory-driven YAML starters and reviewed recipes for idempotency, inventory, stale state, cancellation and navigation. Require authors to declare business invariants.
2. Add a multi-scenario suite manifest, CI budgets and aggregate reporting using existing JSON/JUnit capabilities. Preserve a bundle per run and surface the failure signature and exact replay command.
3. Provide a read-only HTML evidence report with requested/effective schedules, state transitions and original-versus-reduced comparison. Replay remains delegated to `webmcp replay` through the mutation guard.
4. Publish installable artifacts and CI examples, including failure artifact upload and safe secret-binding guidance. Redacted credentials must not silently become replay inputs; use explicit local bindings outside portable evidence.
5. Run three independent pilot integrations and track setup friction, true defects and replay success.

**Acceptance:** three pilot applications beyond the bundled lab; at least two reproduce a real defect and demonstrate a passing fixed revision; median first useful result under 30 minutes as a target; every completed run emits a parseable outcome and evidence location. Measure these targets rather than presenting them as existing results.

**Dependencies:** trustworthy replay and reset semantics. Start recruiting pilot teams in Phase 1, not after building the onboarding features.

### Phase 4 — Evidence-led expansion (weeks 11–16+, conditional)

Choose the next investment from measured pilot bottlenecks:

| Trigger | Investment | Completion evidence |
| --- | --- | --- |
| Missed races despite good scenarios | Event barriers, bounded timing windows, fault/argument reduction, opt-in schema-based generation | Finds additional known seeded defects within a fixed CI budget; records all generated inputs |
| Teams already run WebMCP Evals or framework SDKs | Thin import/export adapters and fixture examples | An imported case becomes reviewable YAML and runs through the same CLI contract |
| Evidence sharing is the main pain | Team history, hosted artifact viewing and baseline comparisons | Authorized pilot demand; retention/redaction controls; no alternate execution engine |
| A second browser implements the necessary surface | Add that native backend to the compatibility matrix | The same fixture corpus passes on an explicitly supported profile |

Refactor the large command module along execution, evidence and compatibility boundaries only where needed for these deliveries. Avoid a broad rewrite.

## Measures, non-goals and protocol watch

Track time to first useful scenario; same-signature replay success/attempts; false reductions; native-profile pass rate; executed unique schedules per second; pilot defects caught and retained CI usage. Separate unsupported runtime, policy denial, infrastructure error and application invariant failure.

Defer a generic agent chat UI, a new WebMCP SDK/polyfill, autonomous production fault injection, broad browser-cloud infrastructure and a hosted service until demand justifies them. The existing disconnected schema fuzzer should not become a headline feature before generated arguments are captured and replayable.

At each release, review the draft changelog/issues, Chrome API/version notes, WPT results and official developer tools. Record source revision, installed binary, launch configuration and adapter profile in compatibility evidence. Upstream small reproducible browser defects when appropriate, without implying conformance certification.

Maintain the repository's invariants: scenario YAML is executable input; `.webmcp/runs/<run-id>/bundle.json` is the portable evidence/replay contract; never delete saved failure artifacts; console replay uses the CLI and cannot bypass mutation authority.

**First development slice:** fix the bounded explorer, same-failure reducer and cancellation targeting with focused regressions, then establish one true native run/replay CI lane. Those changes protect the product's central promise before expanding its surface.
