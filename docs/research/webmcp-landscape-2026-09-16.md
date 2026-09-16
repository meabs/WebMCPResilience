# WebMCP landscape and protocol direction

Research date: 16 September 2026. Primary sources only. This is a dated research snapshot, not a claim that experimental browser behavior is stable. Repository pages are moving targets; implementation decisions should pin browser and package versions and record observed capabilities.

## Executive assessment

**Recommendation: position WebMCP Resilience as the reproducible application reliability and evidence layer for WebMCP.** Basic inspection, invocation and model tool-selection evaluation already have first-party tooling. The defensible product hypothesis is repeatable failure injection, assertions about application outcomes, protocol compatibility, and portable evidence that can be replayed safely in CI. This is a strategic inference from the tooling below, not a verified market-size or customer-demand claim.

## Protocol status and compatibility

The specification is a **Draft Community Group Report dated 15 September 2026**. It explicitly says it is neither a W3C Standard nor on the W3C Standards Track. Its editors include Microsoft and Google. It defines a browser API for page-provided tools; it should not be treated as synonymous with the complete remote MCP transport/server protocol. The current draft places the API on `document.modelContext`, with asynchronous registration, discovery and execution. `RegisteredTool` includes origin and window identity; the draft also describes lifecycle cleanup and tool-change notification. Browser-agent discovery uses a different internal mechanism from in-page discovery, so successful script invocation alone does not prove a particular assistant can discover and use the tool. [W3C Community Group draft](https://webmachinelearning.github.io/webmcp/)

Chrome's overview, updated **7 August 2026**, describes an **origin trial from Chrome 149**, plus `chrome://flags/#enable-webmcp-testing` for local development. This is experimental availability, not a claim of default cross-browser support. The API is intended principally for local workflows with a human participating; headless execution can be useful for testing but does not cover every real user interaction. Registration is gated by origin isolation and the `tools` Permissions Policy, whose default is `self`. [Chrome overview](https://developer.chrome.com/docs/ai/webmcp)

Chrome's imperative API documentation, updated **11 September 2026**, documents:

- `document.modelContext.getTools()` and `executeTool(registeredTool, inputObject, options)`.
- Registration lifetime controlled by an `AbortSignal`; invocation cancellation uses a separate signal.
- `toolchange` events for changing discovery state.
- Cross-origin discovery requiring `fromOrigins`, explicit `exposedTo`, and frame permission delegation.
- A note that JSON-stringified execution arguments are deprecated from **Chrome 155**. Treat this as a documented version boundary; this research does not establish Chrome 155 as the user's current stable browser.

The immediate implication is to select adapters by observed capabilities and explicitly preserve older experimental profiles, rather than hard-code one historical `navigator.modelContext` testing interface. [Chrome imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api)

Declarative tools are an equally important surface: annotated HTML forms can require manual submission or use `toolautosubmit`; `agentInvoked`, `respondWith`, form activation and cancellation affect observable behavior. A test that directly calls a JavaScript callback cannot establish declarative-form compatibility. [Chrome declarative API](https://developer.chrome.com/docs/ai/webmcp/declarative-api)

Microsoft's participation is verified by the draft and original proposal acknowledgments, but this review did **not** verify a first-party Edge stable-release support commitment. Nor did it verify Safari or Firefox native support. Polyfill availability must not be presented as native browser support. [Proposal repository](https://github.com/webmachinelearning/webmcp)

## Tooling landscape

| Offering | Verified role | Implication for this application |
| --- | --- | --- |
| Google Chrome Labs WebMCP tools | Model Context Tool Inspector extension, demos, and a WebMCP Evals CLI for checking model tool calls from test cases and schemas. | Do not build a generic inspector or basic tool-selection CLI as the main differentiation. Import/export compatible eval data where practical. |
| Chrome DevTools WebMCP pane | Registration, schema inspection and invocation history; agent integration through Chrome DevTools for agents, with `--categoryWebMCP`. | Integrate with native diagnostics and provide incident evidence above the debugging surface. |
| Chrome DevTools for agents | MCP server and CLI for browser inspection, automation, performance traces, network and console diagnostics. | A complementary agent interface or diagnostic integration, not something to reimplement wholesale. |
| Microsoft Playwright MCP | Browser automation through structured accessibility snapshots; its README also recommends CLI plus skills for some coding-agent workflows. | Keep a concise JSON CLI as the primary automation contract. An MCP wrapper is optional distribution, not the product foundation. |
| MCP-B / WebMCP-org packages | Independent polyfill, TypeScript types, React hooks, browser transports and local MCP relay. The repository explicitly disclaims official W3C/MCP status. | Test native and polyfilled surfaces separately; support SDK fixtures instead of creating another adoption SDK. |
| Angular | Experimental lifecycle-bound tool registration and Signal Forms integration; APIs may change outside major releases. | Framework lifecycle and stale-registration tests are useful compatibility fixtures. |

Sources: [Google Chrome Labs tools](https://github.com/GoogleChromeLabs/webmcp-tools), [DevTools pane](https://developer.chrome.com/docs/devtools/application/webmcp), [Chrome DevTools for agents](https://github.com/ChromeDevTools/chrome-devtools-mcp), [Playwright MCP](https://github.com/microsoft/playwright-mcp), [MCP-B packages](https://github.com/WebMCP-org/npm-packages), [Angular WebMCP](https://angular.dev/ai/webmcp).

Google's evaluation guidance, updated **28 May 2026**, separates deterministic application/tool tests from probabilistic model evaluations. It identifies tool selection, arguments, ordering, state transitions, output interpretation and complete user journeys as distinct failure surfaces. Therefore a passing deterministic scenario must not be marketed as proof that an arbitrary agent will complete the task. An optional model-evaluation layer should record model identity, prompt, sampling settings, repetitions and success criteria separately. [Chrome eval guidance](https://developer.chrome.com/docs/ai/webmcp/evals)

## Future direction: evidence versus speculation

The proposal repository lists **open questions**, not commitments: multimodal and streaming input/output; output schemas and native schema validation; cross-document responses; built-in-agent exposure; user prompting; progress reporting; skills; and service-worker integration. These justify extension points and research fixtures, not delivery promises tied to browser release dates. [Proposal open questions](https://github.com/webmachinelearning/webmcp#open-questions)

Concrete discussions worth tracking:

- **Persistent tools via workers**, opened 26 June 2026: document navigation can destroy ongoing tool execution. This is an open design discussion and a strong justification for navigation/cleanup tests today. [Issue 212](https://github.com/webmachinelearning/webmcp/issues/212)
- **Approval boundaries**, opened 3 September 2026: a host able to invoke tools and manipulate the DOM may satisfy a page's own approval step. This is a contributor-reported threat model, not a browser security guarantee or settled specification behavior. The CLI mutation guard must remain authoritative regardless of page hints. [Issue 288](https://github.com/webmachinelearning/webmcp/issues/288)
- **Portable workflow records**, opened in August 2026: a contributor proposes preserving completed tasks as reviewable documents and explicitly excludes carrying authentication or approval into replay. This validates relevance as a discussion topic, not endorsement of this project's bundle format or an imminent standard. [Issue 261](https://github.com/webmachinelearning/webmcp/issues/261)

Chrome's security guidance, updated **1 September 2026**, emphasizes indirect prompt injection and that registering a tool exposes functionality to agents. Add adversarial metadata/output fixtures, but distinguish static checks from measured resistance by an actual agent. No schema validator can establish truthful output or safe intent. [Chrome tool security](https://developer.chrome.com/docs/ai/webmcp/secure-tools)

## Recommended roadmap inputs

1. **Compatibility first:** native `document.modelContext` adapter, explicitly named legacy adapters, browser/version/capability provenance in every bundle, and fixtures for imperative and declarative tools. Unsupported features should report unsupported, not silently pass or switch to another mechanism.
2. **Reliable lifecycle evidence:** delayed registration, tool replacement, stale references, navigation, cancellation, frame permissions and origin gating. Record discovery, invocation and assertion failures as separate stages.
3. **Outcome assertions:** test UI and backing application state in addition to returned JSON. Add network failure, latency, malformed response and repeat-call scenarios, including duplicate side effects and partial completion.
4. **Portable replay contract:** retain scenario YAML, browser/protocol profile, tool schema fingerprints, fixture identity and evidence integrity metadata. Revalidate prerequisites before replay; label a rerun against changed live services as such. Never imply a bundle reproduces credentials or grants mutation authorization.
5. **CI ergonomics:** machine-readable results, baselines, regression diffs and actionable artifact links. Preserve failure YAML, repro YAML, traces, screenshots, network evidence and bundles. The console must invoke `webmcp replay` and retain the CLI guard.
6. **Optional agent eval integration:** adopt/import first-party eval cases before adding a new standalone eval engine. Measure stochastic outcomes separately from deterministic test pass rates.
7. **Research only until supported:** worker lifetime, streaming, multimodal, structured output contracts and browser-native approval semantics. Promote these only after specification plus tested implementation evidence.

## Limits of this research

This is a primary-source capability review, not an exhaustive competitor inventory or a benchmark. Documentation was inspected, but external tools and browsers were not executed here. The Chrome Status page and some repository subdirectories did not expose usable content through the browser research tool; version claims above rely on dated Chrome documentation. No market adoption totals, willingness-to-pay estimates, cross-browser launch dates or guaranteed protocol roadmap are asserted.
