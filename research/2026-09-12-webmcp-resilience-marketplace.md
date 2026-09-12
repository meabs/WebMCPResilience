# WebMCP Resilience: marketplace comparison

Research date: 12 September 2026. External statements link to primary sources. “Not documented” means the reviewed material does not establish a capability; it is not evidence of absence.

## Positioning

**WebMCP Resilience is a local, deterministic resilience-test and evidence system for a WebMCP-enabled web application—not a generic MCP client, browser-driving agent, protocol-conformance suite, or device cloud.** It drives real Chromium through Playwright, interleaves declared UI actions and `document.modelContext` calls, injects declared faults, checks observable-state invariants, reduces failures, and records a redacted replay bundle. [README](../README.md), [runner](../src/webmcp_resilience/engine/runner.py), [reducer](../src/webmcp_resilience/engine/reducer.py)

## Marketplace map

| Product/category | What it is for | Difference from WebMCP Resilience | Relationship |
| --- | --- | --- | --- |
| [Chrome WebMCP + Model Context Tool Inspector](https://developer.chrome.com/docs/ai/webmcp) | Chrome’s proposed standard and live page-tool inspector: registered tools, manual calls, schema checks, structured results/errors. | Live inspection/debugging, not documented seeded UI/tool schedule exploration, fault injection, minimisation, or portable replay. | Complement: develop/debug tools in Chrome; run resilience scenarios in local/CI environments. |
| [MCP Inspector v2](https://github.com/modelcontextprotocol/inspector/blob/main/docs/v1-to-v2-migration.md) | Generic **MCP-server** inspector with Web, CLI, and TUI surfaces; CLI invokes protocol methods over stdio/SSE/HTTP. | Resilience drives a **browser application** and its in-page WebMCP API while combining DOM activity and page-tool calls. | Stay separate; optionally attach Inspector observations as supplementary evidence. |
| [MCP Conformance](https://github.com/modelcontextprotocol/conformance) | Core MCP client/server scenarios, protocol capture, wire-schema checks, `checks.json`, and a GitHub Action. | Protocol correctness is not browser/WebMCP behavioural resilience. | Run both: Conformance for protocol implementations; Resilience for page UI + tool interaction. |
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | An MCP server that gives LLMs browser control via structured accessibility snapshots, suited to exploratory/persistent agent loops. | Browser operation for agents versus declared WebMCP/UI flows that are repeatable, faultable, reducible, and replayable. | Agents can discover a flow, then persist a Resilience scenario. The local Resilience MCP server is deliberately a policy adapter over its CLI contract, not another browser-control interface. |
| [Playwright Test + Trace Viewer](https://playwright.dev/docs/trace-viewer) | General browser automation plus post-run actions, DOM snapshots, source, console, network, screenshots, and attachments. | Resilience builds on Playwright but adds WebMCP inventory/preflight, fault timing, invariants, reduction, compatibility checks, and a replayable redacted bundle. | Foundation, not replacement. Keep Resilience’s bundle as the replay contract; link Playwright traces when useful. |
| [BrowserStack MCP / Automate](https://www.browserstack.com/docs/browserstack-mcp-server/overview) | Vendor cloud for real browsers/devices, agent-accessible execution, logs, screenshots, and test-management workflows. | Execution grid/lifecycle platform, not a documented WebMCP-specific fault-and-replay engine. | Potential execution partner only after confirming exact Chromium/WebMCP capability per image. Keep scenarios and bundles portable. |

## Why now

Chrome calls WebMCP a **proposed** standard, with an origin trial from Chrome 149 and a local testing flag. It provides imperative JavaScript tools and declarative form annotations, but is under active discussion and subject to change. Its page API is `document.modelContext`, including tool registration, discovery and execution. [Chrome WebMCP](https://developer.chrome.com/docs/ai/webmcp), [W3C Community Group draft](https://webmachinelearning.github.io/webmcp/)

WebMCP is gated by origin isolation and the `tools` Permissions Policy; the default policy is `self`, and a cross-origin iframe needs `allow="tools"`. Chrome positions it primarily for local, human-in-the-loop work rather than headless use. This creates a gap for explicit browser preflight, a CLI-first test runner, and portable execution evidence. [Chrome WebMCP](https://developer.chrome.com/docs/ai/webmcp), [imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api)

Chrome’s security guidance identifies indirect prompt injection and recommends `untrustedContentHint`, `consequentialHint`, `readOnlyHint`, and narrowly trusted origins. Resilience can test timing/failure behaviour around declared tools; it cannot claim to solve LLM prompt injection. [Chrome security guidance](https://developer.chrome.com/docs/ai/webmcp/secure-tools)

## Differentiators worth claiming

1. **WebMCP-plus-UI resilience, not tool enumeration.** Scenarios combine human, agent, and system actions. The runner uses page-discovered tools and declared observable state only. [README](../README.md), [scenario model](../src/webmcp_resilience/models/scenario.py)
2. **Controlled failure reproduction.** Bounded seeded schedules, declared latency/timeout/duplicate/cancel/navigation/HTTP faults, invariants, and fresh-run reduction yield an executable minimal repro. [explorer](../src/webmcp_resilience/engine/explorer.py), [faults](../src/webmcp_resilience/engine/faults.py), [reducer](../src/webmcp_resilience/engine/reducer.py)
3. **One contract for CLI, console, and agents.** The CLI is primary; the console writes ordinary YAML and replays through the CLI; the local MCP server is a typed, policy-enforcing `CommandAPI` adapter. Thus CLI-only deployment remains complete while agent access is first-class. [README](../README.md), [agent control](../src/webmcp_resilience/agent_control.py), [console client](../src/webmcp_resilience/console_client.py)
4. **Safe portable evidence.** Every command writes a versioned bundle with discovery, environment, schedule, trace, artifacts, result and replay data. Text/URLs are recursively redacted and potentially sensitive screenshots remain metadata-only through console/agent interfaces. [bundle model](../src/webmcp_resilience/models/bundle.py), [commands](../src/webmcp_resilience/commands.py)

## Claims to avoid

- “Official WebMCP conformance.” MCP Conformance covers core MCP client/server protocol behaviours, not page-tool/UI resilience.
- “Cross-browser WebMCP coverage.” The reviewed official implementation is a Chrome origin trial; verify every target runtime before offering support.
- “Prompt-injection prevention” or “enterprise governance.” The local policy has fixed origins, mutation permission, concurrency and artifacts; it is not a centrally managed security plane.
- “A substitute for Playwright.” Playwright remains the automation substrate and already provides trace investigation.

## Go-to-market and roadmap implications

Lead with teams shipping agent-actuated, stateful web workflows where a late, duplicated, cancelled, malformed, or concurrent tool call can violate a business invariant. Demonstrate one short scenario finding a real race, producing a minimized reproduction, and replaying a portable bundle.

- **Now:** CLI, scenario examples, CI template, browser preflight, agent MCP server, and evidence bundle—an agent-accessible but CLI-first wedge.
- **Next:** attach Playwright traces and MCP Conformance `checks.json` as supplementary artifacts, without duplicating either product.
- **Later:** vendor execution adapters only when they preserve the exact Chromium/WebMCP capability fingerprint and bundle contract.
- **Watch:** Chrome trial/API/security changes and WebMCP standardisation. Do not make multi-browser or permanent-spec claims until source evidence exists.

## Sources

Primary sources: [Chrome WebMCP](https://developer.chrome.com/docs/ai/webmcp), [Chrome WebMCP security](https://developer.chrome.com/docs/ai/webmcp/secure-tools), [WebMCP Community Group draft](https://webmachinelearning.github.io/webmcp/), [MCP Inspector](https://github.com/modelcontextprotocol/inspector), [MCP Conformance](https://github.com/modelcontextprotocol/conformance), [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp), [Playwright Trace Viewer](https://playwright.dev/docs/trace-viewer), and [BrowserStack MCP](https://www.browserstack.com/docs/browserstack-mcp-server/overview).
