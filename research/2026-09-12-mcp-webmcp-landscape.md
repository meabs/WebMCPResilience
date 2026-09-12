# MCP and WebMCP tooling landscape: WebMCP Resilience

Researched 12 September 2026. External claims link only to the product owner;
repository claims link to the current implementation. **MCP-server tooling and
browser WebMCP tooling are adjacent, not interchangeable.**

## Conclusion

WebMCP Resilience has a real differentiator when it tests a running browser
application across *declared UI and WebMCP-tool interleavings*, faults,
observable-state invariants, reduction, and safe replay. It is not
differentiated as a generic tool inspector: [MCP Inspector v2](https://github.com/modelcontextprotocol/inspector)
already has Web, CLI, and TUI interfaces for MCP servers, and Chrome's
[WebMCP DevTools inspector](https://developer.chrome.com/docs/devtools/application/webmcp)
already lists live tools, supports parameterised manual calls, shows call
history/results/errors, and diagnoses schema violations.

WebMCP is still moving and browser-specific. Chrome documents origin-isolation
and `tools` Permissions Policy gates (including cross-origin iframe delegation)
and says headless use is possible but its primary design is local,
human-in-the-loop work. [Chrome WebMCP guide](https://developer.chrome.com/docs/ai/webmcp)
This makes repeatable browser preflight and a portable run bundle useful; it
does not make the project a substitute for core-MCP conformance.

## Current capability matrix

Legend: **Yes** = documented/current implementation; **Partial** = useful
subset or explicit boundary; **—** = outside the product's stated scope.
“Not documented” is intentionally not a claim of absence.

| Capability | MCP Inspector v2 | Chrome WebMCP / Model Context Tool Inspector | Official MCP Conformance | WebMCP Resilience (current repo) | Relevant OpenAI agent control plane |
| --- | --- | --- | --- | --- | --- |
| Tool inspection / manual calls | **Yes** — CLI covers connect, `initialize`, list, call, and assertions; UI/TUI share the package. [CLI](https://github.com/modelcontextprotocol/inspector/blob/main/clients/cli/README.md) | **Yes** — DevTools shows live tools/history; users edit params and run a tool, isolating handler reliability from model choice. [Chrome](https://developer.chrome.com/docs/devtools/application/webmcp) | **Partial** — client/server test scenarios send requests and capture responses, rather than serving as a general explorer. [Conformance](https://github.com/modelcontextprotocol/conformance) | **Partial** — `preflight` inventories live page tools; scenarios may invoke only discovered tools. [README](../README.md), [commands](../src/webmcp_resilience/commands.py) | **Yes** — Responses lists remote-MCP tools and exposes call arguments/output/error. [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp) |
| CLI / interactive surface | **Yes** — one binary provides Web UI, scriptable CLI, and Ink TUI. [Inspector](https://github.com/modelcontextprotocol/inspector) | **Partial** — DevTools UI; Chrome docs do not specify a comparable CLI. [Chrome](https://developer.chrome.com/docs/devtools/application/webmcp) | **Yes** — CLI plus composite GitHub Action. [Conformance](https://github.com/modelcontextprotocol/conformance) | **Yes** — CLI `preflight`/`validate`/`run`/`replay`/`diff`/`report`/`trace` and composer/trace TUI share `CommandAPI`. [CLI](../src/webmcp_resilience/cli.py) | **API/control plane**, not an inspector UI. [Agents overview](https://developers.openai.com/api/docs/guides/agents) |
| Conformance / readiness | **Partial** — documented server smoke testing, not the official conformance runner. [Inspector smoke testing](https://github.com/modelcontextprotocol/inspector/blob/main/docs/smoke-testing-an-mcp-server.md) | **Partial** — input/output schema diagnostics and tool-call inspection, not a published conformance suite. [Chrome](https://developer.chrome.com/docs/devtools/application/webmcp) | **Yes, for core MCP** — versioned client/server scenarios, wire-schema checks, frozen requirement sets, results and expected-failure baselines. [Conformance](https://github.com/modelcontextprotocol/conformance) | **Partial, browser-native** — non-mutating `preflight` probes WebMCP availability and tool schema/annotation quality; `validate` checks scenario semantics. It makes no official MCP/WebMCP conformance claim. [commands](../src/webmcp_resilience/commands.py) | **—** — consumes remote MCP, not a conformance framework. [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp) |
| Deterministic concurrency, faults, invariants, reduction, replay | Official materials document stable UI automation selectors/status, not a general fault scheduler, reducer, or portable replay bundle. [Web client](https://github.com/modelcontextprotocol/inspector/blob/main/clients/web/README.md) | Not documented; Chrome positions it for live inspection/debugging. [Chrome](https://developer.chrome.com/docs/devtools/application/webmcp) | **Partial** — parallel suites and protocol result capture, but not UI-plus-tool schedule exploration/fault injection/reduction/replay. [Conformance](https://github.com/modelcontextprotocol/conformance) | **Yes** — bounded seeded permutations of declared simultaneous actions, fresh browser sessions, declared latency/timeout/duplicate/cancel/navigation/HTTP faults, invariant DSL, fresh-run action reduction, and recorded-schedule replay. [explorer](../src/webmcp_resilience/engine/explorer.py), [runner](../src/webmcp_resilience/engine/runner.py), [reducer](../src/webmcp_resilience/engine/reducer.py), [commands](../src/webmcp_resilience/commands.py) | **Partial/adjacent** — agent evals can grade traces over time, but this is not a deterministic browser test/replay system. [Agent evals](https://developers.openai.com/api/docs/guides/agent-evals) |
| Evidence safety / portability | **Partial** — documented `--config` is read-only and its web backend requires a bearer token; no portable execution-evidence bundle is documented. [v1→v2 migration](https://github.com/modelcontextprotocol/inspector/blob/main/docs/v1-to-v2-migration.md) | Not assessed beyond local DevTools scope. | **Partial** — emits `checks.json` plus stdout/stderr; not a browser replay bundle. [Conformance](https://github.com/modelcontextprotocol/conformance) | **Partial-to-strong** — run-oriented CLI commands persist a versioned bundle; recursive key/query redaction, compatibility-fingerprint checks, and explicit mutation approval exist. Screenshots are deliberately labelled potentially sensitive, not claimed redacted. [bundle model](../src/webmcp_resilience/models/bundle.py), [commands](../src/webmcp_resilience/commands.py) | **Partial** — managed trace visualisation/evals exist, but detailed trace retrieval/export is not a public-beta API; do not treat it as portable evidence. [Observability](https://developers.openai.com/api/docs/guides/agents-api/observability) |
| Agent control plane | **—** — developer inspector. | **Partial** — page-tool debugging, with user permission/confirmation part of WebMCP's model. [Chrome agent overview](https://developer.chrome.com/docs/ai/agents) | **—** — conformance harness. | **Partial** — per-run read-only default and explicit `--allow-mutations`, captured in the bundle; no identity, centrally managed policy, approval queue, or remote-agent orchestration. [runner](../src/webmcp_resilience/engine/runner.py), [bundle model](../src/webmcp_resilience/models/bundle.py) | **Yes, OpenAI-scoped** — managed Agents API/SDK or direct Responses, with approvals, guardrails, handoffs and tracing. [Agents overview](https://developers.openai.com/api/docs/guides/agents), [Approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals) |

## Actual moat

- **Browser reality plus controlled schedules.** The runner drives Chromium and
  `document.modelContext`, combines real UI actions with tool calls, and varies
  only interactions the scenario declared. This targets UI/tool races that core
  MCP server tools cannot observe. [README](../README.md), [explorer](../src/webmcp_resilience/engine/explorer.py)
- **Reproducible failure evidence.** Failing runs record schedule, trace,
  browser/tool inventory, artifacts, a minimized declared-action repro, and a
  replay command. Replay rejects incompatible bundle/capability fingerprints
  rather than silently reinterpreting history. [commands](../src/webmcp_resilience/commands.py)
- **Safety-aware experiments.** Mutation-capable tools are blocked unless
  explicitly authorised; persisted structures cross a redaction boundary and
  screenshots are visibly classified as potentially sensitive. [runner](../src/webmcp_resilience/engine/runner.py), [bundle model](../src/webmcp_resilience/models/bundle.py)

The bundle/CLI compatibility is part of the moat: a console must remain a view
over the same executable scenario and replay contract, never a second path.

## Table-stakes gaps and boundaries

- **Do not duplicate generic MCP inspection or core-MCP conformance.** Associate
  official Conformance `checks.json` with a browser run if useful, but do not
  label `preflight` official conformance.
- **Broaden preflight diagnostically.** Current code records API/tool
  availability and schema/annotation quality, but not a full browser version,
  origin-isolation/Permissions-Policy diagnosis, document/iframe topology, or
  lifecycle compatibility report. Those are practical WebMCP compatibility
  expectations because Chrome explicitly gates the API on origin isolation and
  policy. [Chrome](https://developer.chrome.com/docs/ai/webmcp), [commands](../src/webmcp_resilience/commands.py)
- **Finish the evidence-safety story.** Text/URL redaction exists, but a
  screenshot can still contain secrets. Add retention/export policy and optional
  screenshot suppression or visual redaction before claiming security-review
  readiness. [bundle model](../src/webmcp_resilience/models/bundle.py)
- **Keep control-plane scope narrow.** The mutation switch is a test safety
  boundary, not enterprise policy administration. If integration is needed,
  attach provider approval/policy records rather than rebuilding a generic MCP
  gateway; OpenAI's documented baseline is per-call approval and tool filtering.
  [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)

## Sources

Primary sources: [MCP Inspector v2](https://github.com/modelcontextprotocol/inspector),
[MCP Conformance Test Framework](https://github.com/modelcontextprotocol/conformance),
[Chrome WebMCP / Model Context Tool Inspector](https://developer.chrome.com/docs/devtools/application/webmcp),
and [OpenAI developer documentation](https://developers.openai.com/api/docs/guides/agents).
Chrome's WebMCP guide says the API is under active discussion; re-check browser
claims before treating a draft behavior as stable.

## MCP Inspector integration strategy

**Recommendation: remain a separate, interoperable WebMCP Resilience project;
contribute narrowly upstream; do not fork Inspector.** Inspector v2 is public
source with **MIT** declarations in its README/package metadata, but the
repository currently has no visible license-text file and GitHub does not
detect a repository license. Treat it as a practical MIT-licensed upstream,
but obtain maintainer/legal confirmation before redistributing a derivative.
It is not an open extension platform: it deliberately does not publish `core/`
for third-party use. Its documented architecture is a shared `InspectorClient`
for MCP-server connections, with Web/CLI/TUI clients over that core; the web
client is a React SPA plus Node/Hono backend. [Package metadata](https://github.com/modelcontextprotocol/inspector/blob/v2/main/package.json),
[architecture](https://github.com/modelcontextprotocol/inspector/blob/v2/main/docs/architecture.md),
[project layout](https://github.com/modelcontextprotocol/inspector/blob/v2/main/README.md),
[public repository metadata](https://api.github.com/repos/modelcontextprotocol/inspector).

That is a different boundary from this repository: Inspector is a generic MCP
*client* that connects to MCP servers and manually/synchronously exercises
protocol methods (`initialize`, `tools/list`, `tools/call`, resources and
prompts). WebMCP Resilience drives a *browser application* through Playwright
and `document.modelContext`, mixing declared DOM actions and page-tool calls
under seeded schedules/faults, then minimizes and replays a portable evidence
bundle. [Inspector CLI scope](https://github.com/modelcontextprotocol/inspector/blob/v2/main/clients/cli/README.md),
[this runner](../README.md), [runner](../src/webmcp_resilience/engine/runner.py).
Browser origin/policy/lifecycle diagnostics, fault scheduling, UI/tool
interleavings, reduction, and guarded browser-bundle replay are therefore not
a natural Inspector-client feature and should not be proposed as a wholesale
upstream port.

- **Contribute upstream** only a small generic Inspector improvement with a
  clear MCP-server/client fit (for example, a machine-readable probe or an
  app-metadata diagnostic). File a detailed **v2** issue first; external
  contributors submit issues/prompts rather than pull requests, and maintainers
  implement the change. [Contribution policy](https://github.com/modelcontextprotocol/inspector/blob/v2/main/CONTRIBUTING.md).
- **Integrate at the CLI/evidence seam** when useful: allow a scenario or CI
  job to invoke Inspector's documented CLI separately and attach its JSON output
  as supplementary evidence. Keep `webmcp replay` as the sole executable path
  for WebMCP bundles; do not make the console reinterpret or mutate them.
- **Fork only if** Inspector maintainers explicitly decline a required,
  broadly-useful Inspector feature *and* its maintenance burden is justified.
  A fork would inherit a fast-moving multi-client application plus its
  maintainer-led, issue-driven process, while still not solving browser-side
  schedule exploration. Prefer no fork by default.

Revisit an upstream contribution if Inspector formally adds a browser-WebMCP
transport/adapter or a supported plugin boundary. Until then, keep integration
one-way and contractual: Inspector may supply generic MCP observations; this
CLI owns browser execution, resilience semantics, and the replay bundle.
