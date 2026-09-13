# Scenarios, scheduling, and replay

## The scenario contract

A scenario is portable YAML. It declares exactly the interactions that may be
run; the engine does not create tool arguments, selectors, records, or state on
its own.

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
  - type: latency
    tool: reserve_inventory
    duration_ms: 500
    at: before_invoke
invariants:
  - inventory.reserved <= inventory.available
```

Each action has exactly one operation: `invoke`, `retry`, `cancel`, or a UI
`action`. Supported UI actions are `click`, `fill`, `select`, `navigate`, and
`wait`. Times are integer milliseconds such as `0ms` or `250ms`.

## State: two explicit mechanisms

`state_script` in `.webmcp/config.yaml` returns the object checked by state
invariants. It runs inside the page and must return an object.

```yaml
state_script: window.__app.getObservableState()
```

`scenario.state.tool` is different: it declares a discovered read-only WebMCP
tool whose result is resolved into action arguments.

```yaml
state:
  tool: get_checkout_snapshot
actors:
  agent:
    - invoke: confirm_order
      args: {expectedVersion: ${state.version}}
```

Neither mechanism silently reads network traffic or arbitrary DOM content.

## Optional tool contracts and observable result assertions

Scenarios may declare expectations for only the tools whose contract matters to
that scenario. A fingerprint is optional:

```yaml
tool_contracts:
  reserve_inventory:
    fingerprint: "<optional pinned per-tool fingerprint>"
    required_inputs: [sku, quantity]
    read_only: false
    semantic_version: "1"
    result_invariants:
      - results.reserve_inventory.OK == 1
    expected_result_codes: [OK, STALE_STATE]
```

Before any scenario action, live discovery checks the fingerprint (when
present), required inputs, `annotations.readOnlyHint`, and semantic version.
Semantic versions are explicit page declarations (`semanticVersion` or an
annotation `semanticVersion`/`version`); the framework does not invent them.

After invocation, `expected_result_codes` constrains observed `result.code`
values and `result_invariants` uses the same deterministic comparison language
as ordinary result invariants. Counts are exposed as
`results.<tool>.<code>`. These assertions are limited to declared inputs,
outputs, result codes, state invariants, and versions. They do not infer or
prove unchanged business semantics.

## Faults

| Fault | Timing | Effect |
| --- | --- | --- |
| `latency` | `before_invoke`, `after_invoke` | Delays an invocation at a declared point. |
| `timeout` | `before_invoke` | Bounds the call duration. |
| `duplicate_invocation` | `before_invoke`, `after_invoke` | Repeats the resolved call once. |
| `cancellation` | `before_invoke` | Cancels an in-flight call. |
| `navigation` | `before_invoke`, `after_invoke` | Performs declared browser navigation. |
| `http_error` | `before_invoke` | Routes matching browser HTTP traffic to a declared response status. |

Fault timing is explicit. Unsupported timing combinations are validation errors.

## Scheduling semantics

`--adversarial --seed N` explores bounded permutations of simultaneous,
declared actions in fresh browser sessions. The selected schedule and seed are
saved to the run bundle.

For a same-offset group, eligible WebMCP tool promises are started in page
context before the runner sends the accompanying UI action. That allows the UI
to begin before the tool result is awaited. Playwright page commands remain
transport-serialised, so the promise is **logical actor concurrency**, not a
claim of sub-frame, CDP-level, or microtask-level control.

The bundle records requested offsets, selected schedule, seed, and trace event
timestamps. Replay validates compatibility, then uses the recorded logical
schedule; it does not re-roll exploration.

## Failure reduction and replay

When a run fails, the reducer removes declared actions only when a fresh browser
session still reproduces the failure. It does not edit traces, substitute data,
or guess new arguments.

```bash
.venv/bin/webmcp replay .webmcp/runs/<run-id>/bundle.json --ci --json
# Opt into exact fingerprint matching, including compatible evolution.
.venv/bin/webmcp replay .webmcp/runs/<run-id>/bundle.json --strict-tool-contracts --ci --json
```

Replay restores a recorded `state_script` and the origin captured in the
bundle, so a copied bundle does not require a hand-written local
`.webmcp/config.yaml` for its observable-state boundary. It still executes
against a live application at that origin; start the application before replay.

Replay always retains and compares the canonical recorded tool-inventory
fingerprint. A changed fingerprint is classified using the bundle's saved
`fingerprint_policy`, never the current machine's configuration. By default,
replay permits only proven-compatible drift: optional input fields added,
required inputs made optional, optional output fields added, and description
changes when descriptions were excluded from that saved policy. Tool removals,
new required inputs, narrowed input types or enums, narrowed/removed outputs,
and `readOnlyHint` or `destructiveHint` changes are breaking and reject. Any
other change is unknown and rejects. `--strict-tool-contracts` rejects every
fingerprint drift, including compatible drift.
Description-only edits remain an exact match under strict mode when the saved
policy excluded descriptions, because they never change that canonical
fingerprint; they remain visible in `descriptive_only_changes` diagnostics.

Every replay bundle records the baseline and live fingerprints, saved policy,
full per-tool drift report, policy impact, and `tool_contract_replay_decision`.
Rejected replays return structured `tool_contract_drift`, `policy_impact`,
`replay_decision`, and per-tool reasons in CLI JSON and local MCP responses.
Before a browser opens, replay rebuilds the canonical contract from the saved
inventory and requires it to match both saved fingerprints. Missing, malformed,
or inconsistent evidence is rejected as an `evidence_integrity` contract error.

## Evidence bundle

`bundle.json` is the handoff contract. It includes compatibility requirements,
browser evidence, preflight output, canonical redacted per-tool contracts and
fingerprints, the complete inventory fingerprint, drift/expectation status and
replay decision,
scenario, schedules, trace, state observations, policy approvals, artifacts,
result, and replay command.

`webmcp diff` and the console baseline comparison keep tool compatibility drift
separate from behavioural drift. They report added, removed, changed input,
changed output, changed annotations, descriptive-only, and unchanged tools.
Breaking changes include removals, newly required inputs, narrowed enum/type,
safety-annotation changes, and incompatible output shapes. Optional inputs and
descriptive-only changes are compatible; unrecognized schema changes are
reported as unknown for human review.

Textual structured evidence and URL credentials are recursively redacted.
Screenshots are marked potentially sensitive and remain metadata-only through
the console and agent-control interfaces.

## Console workbench

`webmcp console --discovery .webmcp/runs/<preflight-id>/bundle.json` opens the
scenario composer. It emits ordinary portable YAML and equivalent CLI/MCP
requests. A composer save validates through `CommandAPI` first. Multiple actors
and ordered timed actions support `invoke`, `retry`, `cancel`, `click`,
`fill`, `select`, `navigate`, and `wait`; executable fault declarations support
the timing matrix above. Provide either the actors JSON object or the ordered
actions JSON list; supplying both is rejected so no accepted action can be
silently discarded. There is no metadata-only fault control.

`webmcp console --history` opens the read-only run-history workspace. History
keys are `b` pin baseline, `t` render the redacted timeline, `r` replay, `c`
compare, `m` show safe minimized-repro metadata, and `h` export a safe
handoff. Trace view also supports `q`, `home`, `end`, `r`, `c`, `m`, and `h`.
The timeline is an actor-labelled chronological event list with nested state
diffs; it does not provide per-actor lane or event/tool filter views. The same
compact chronological view is available as
`webmcp console <bundle> --print` for CI logs.

Replay classification is based on the fresh JSON bundle/result: an expected
invariant failure reproduced by replay is successful replay work and is shown
as “Failure reproduced”, even though the CLI process exits 1 for the failing
invariant. A malformed result or incompatible bundle is an execution/contract
failure. `webmcp handoff <bundle>` writes redacted Markdown and JSON incident
metadata; screenshots, network payloads, and other potentially sensitive
artifact contents are never embedded.
