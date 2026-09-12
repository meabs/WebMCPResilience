# Architecture

## Design rule: one contract, no split brain

```text
CLI JSON ─┐
Console ──┼──> CommandAPI ──> validation · preflight · runner · reducer · bundle
Local MCP ┘                                  │
                                             ▼
                           Playwright Chromium + document.modelContext
```

`CommandAPI` is the only application boundary for execution. User interfaces
and agent control transport requests to it; they do not recreate browser,
scheduler, replay, or evidence logic. This keeps CLI-only operation complete
and prevents an MCP-based coding agent from receiving a different test result
than a developer in the terminal.

## Browser adapter boundary

The WebMCP adapter is the sole browser-facing boundary. It installs a narrow
page helper, discovers page tools, starts/invokes/cancels calls, reads the
configured state object, drains lifecycle events, and collects non-mutating
readiness evidence. The scenario engine does not depend on browser-specific
WebMCP shapes directly, which limits the impact of an evolving browser API.

## Execution lifecycle

1. **Validate** the scenario contract and compatibility requirements.
2. **Resolve and validate** every browser navigation target.
3. **Launch a fresh browser context** for each candidate schedule.
4. **Probe** the page and calculate a capability fingerprint.
5. **Discover** required page tools from the live document.
6. **Run** declared actions, faults, and invariant checks.
7. **Record** trace, state observations, artifact metadata, policy, and result.
8. **Reduce** a failure only through fresh-run reproduction.
9. **Persist** one redacted, versioned bundle.
10. **Replay** only when the saved compatibility and capability contract match.

## Concurrency model

The scheduler groups actions by declared millisecond offsets. For simultaneous
tool/UI work, it can initiate an eligible tool promise in the browser before
sending UI work. That produces useful actor-level races while respecting
Playwright's transport serialisation. The saved schedule is a deterministic
logical ordering, not a synthetic model checker or a guarantee of physical
sub-frame timing.

## Evidence model

`RunBundle` is the public interchange format for CLI, CI, console, and local
MCP consumers. It carries compatibility/version information, preflight data,
scenario, tool inventory, action/fault declarations, selected schedule, trace,
artifacts, approvals, redaction metadata, and replay instructions. Redaction
is applied on persistence and again when external evidence is displayed.

## Product boundary

WebMCP Resilience complements generic inspection, protocol conformance,
Playwright automation, browser/device clouds, and agent evaluation. It owns the
behavioural-resilience question: whether a real web application's state remains
correct while WebMCP tools, UI interactions, and declared failures interleave.
