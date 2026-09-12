# Where WebMCP Resilience fits

WebMCP Resilience is a behavioural resilience layer. It complements the tools
that inspect a WebMCP contract or check one tool call; it does not replace
them.

| Tool | Primary question | What this project adds |
| --- | --- | --- |
| Chrome DevTools | What does this page expose, and how does it behave in the browser? | A portable scenario, invariant, failure bundle, and exact replay contract. |
| MCP Inspector | Can I discover, inspect, and manually invoke MCP tools? | Concurrent human/UI and agent schedules, fault injection, reduction, and CI assertions. |
| MCP Conformance | Does an implementation follow the MCP protocol and required shapes? | Does the live application remain correct when protocol-valid calls overlap, retry, or meet UI state changes? |
| WebMCP Resilience | Does shared application state remain valid under hostile timing? | This is the question it is designed to answer. |

The boundary is intentional:

```text
DevTools / Inspector / Conformance  -> contract, protocol, and browser readiness
WebMCP Resilience                   -> stateful behaviour under concurrency and failure
```

Use the other tools to establish that a tool is exposed, shaped correctly,
and callable. Then use a resilience scenario to declare the real application
invariant and exercise the same page through multiple control surfaces.

All three entry points share the same safety policy:

- CLI commands go through `CommandAPI` and require explicit
  `--allow-mutations` for the vulnerable fixture.
- The terminal console is view-only unless launched with its explicit replay
  mutation flag.
- Local MCP control receives immutable startup authority; a request cannot
  escalate it. Sensitive artifacts remain metadata-only and textual evidence
  is redacted.

The policy is defence in depth for unsafe agent-originated requests. It is not
prompt-injection prevention, SSRF containment, or enterprise governance.

## Portable state and scheduling contract

YAML is the language-neutral scenario contract shared by Git, CI, terminal
console authoring, Python/pytest integration, and coding-agent generation.
State is never inferred from rendered DOM text, network traffic, or hidden app
internals. A configured `state_script` must return the observable object used
by invariants. A `scenario.state.tool` is a separate declared, discovered
`readOnlyHint: true` source for resolving action arguments only. An invariant
without a state script fails before execution with a remediation message.

Run bundles record `execution.scheduler` version `1.0`: requested offsets, the
selected logical schedule, adversarial seed, and recorded trace timestamps.
Replay consumes that recorded logical schedule. Same-offset WebMCP tools start
in page context before their associated UI dispatch; Playwright page commands
remain transport-serialised. The guarantee is logical action ordering and
descriptor equality, not sub-frame, CDP-level, or microtask-level timing.

The included local Forge can produce a tested terminal handoff:

```text
FAIL run · run race-failure
Failed invariant: claims.active <= claims.capacity
Observed state: {"claims": {"active": 2, "capacity": 1}}
Capability fingerprint: f877cce7340284d4
Bundle: .webmcp/runs/race-failure/bundle.json
Reduced repro: .webmcp/runs/race-failure/repro.yaml
Replay: webmcp replay .webmcp/runs/race-failure/bundle.json --run-id race-failure --allow-mutations
```

WebMCP Resilience does not test whether an AI agent is smart enough to find the checkout button; it tests whether the checkout application is robust enough to survive being driven through WebMCP.

The portable `.webmcp/runs/<run-id>/bundle.json` is the handoff boundary. A
developer or coding agent can copy that redacted bundle and reproduce the same
recorded schedule through `webmcp replay`, the console flight deck, or
`replay_run` over local MCP control. None of those consumers needs to parse
human-formatted output or invent tool arguments.
