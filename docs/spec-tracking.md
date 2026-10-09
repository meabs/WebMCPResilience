# WebMCP specification tracking

Last verified: 2026-10-08.

WebMCP remains an evolving browser proposal. This repository records facts only
when a public Chrome source states the milestone explicitly.

## Version facts

| Change | Chrome version | Current status | Source and verification date |
| --- | ---: | --- | --- |
| `navigator.modelContext` deprecated; `document.modelContext` canonical | 150 | Unconfirmed. Current imperative API documentation uses `document.modelContext` but does not state a Chrome 150 migration or deprecation milestone. | [Imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api), checked 2026-10-08 |
| Unregistering a tool no longer cancels in-flight executions | 153 | Confirmed. | [Imperative API: unregister tools](https://developer.chrome.com/docs/ai/webmcp/imperative-api#unregister-tools), checked 2026-10-08 |
| JSON-stringified input arguments deprecated | 155 | Confirmed. | [Imperative API: execute tool](https://developer.chrome.com/docs/ai/webmcp/imperative-api#execute-tool), checked 2026-10-08 |
| `debugging` tool annotation available | 156 | Confirmed. | [Imperative API: tool annotations](https://developer.chrome.com/docs/ai/webmcp/imperative-api#tool-annotations-optional), checked 2026-10-08 |
| Origin trial window | 149–156 | Confirmed by current Chrome Status. This was unconfirmed in the Step 0 baseline because the then-reviewed public imperative API page did not list the window. | [Chrome Status: WebMCP](https://chromestatus.com/feature/5117755740913664), checked 2026-10-08 |

## Trial and post-156 status

Chrome Status currently lists WebMCP's origin trial as Chrome 149 through 156
and lists shipping in Chrome 157 for desktop, Android, and Android WebView.
Those are Chrome Status milestones, not a statement that the feature has
already shipped. The imperative API documentation was last updated 2026-09-21
and continues to describe WebMCP as under active discussion.

## Cross-origin discovery

Checked 2026-10-08 against the [Imperative API cross-origin
documentation](https://developer.chrome.com/docs/ai/webmcp/imperative-api#cross-origin-iframes).
The documentation states that `getTools()` returns same-origin tools by
default. Cross-origin discovery requires the caller to list a secure origin in
`fromOrigins`, and the registering origin must explicitly expose the tool to
the caller. It also states that cross-origin iframe registration is disabled
by default and requires `tools` Permissions Policy delegation.

The local two-origin fixture in Step 6 is a compatibility-host simulation of
those documented discovery rules. It does not establish that the local
Playwright Chromium baseline implements native WebMCP cross-origin behavior;
the current public docs do not provide a separate milestone for this exact
combination of iframe, policy, and exposure checks.

## Consequential tool annotations

Checked 2026-10-09 against the [Imperative API tool
annotations](https://developer.chrome.com/docs/ai/webmcp/imperative-api#tool-annotations-optional).
Chrome documents `consequentialHint` as metadata for significant or
non-reversible actions that allows agents and browsers to enforce mandatory
user confirmation. The documentation does not define a browser-prompt result
that an application test can observe. Step 7 therefore checks an application's
scenario-declared confirmation workflow and completed tool result; it does not
claim to test Chrome's confirmation prompt.

## Automated tracking

The weekly `demo preflight contract` GitHub Actions job compares the committed
[`preflight-demo-snapshot.json`](preflight-demo-snapshot.json) with the demo
fixture's live preflight evidence. It checks the selected entry point and the
full sorted tool-name inventory. It also runs on push and pull request so
snapshot changes are reviewed before the weekly schedule.

## Definition of done and skipped work

| Item | Status or reason |
| --- | --- |
| Step 1 planning-document move | Already completed before this staged plan was executed. |
| Step 2 preflight entry-point evidence | No new evidence field was added: the source already emitted `preflight.webmcp.api.location`; Step 2 audited and tested that existing evidence. |
| Chrome 155 structured-argument behavior on the local baseline | Simulated with a version-pinned fixture because the local Playwright Chromium baseline is 153. The CI Chrome-beta matrix runs the same capability gate on a newer channel when available; it skips with a reason below 155. |
| Chrome 153 unregister behavior below the capability threshold | Intentionally skipped with recorded `fault.skipped` evidence; it must never fail a run solely because the browser is older. |
| Native cross-origin WebMCP validation on the local baseline | Simulated through the compatibility-host fixture because Chromium 153 does not provide native WebMCP evidence. The fixture is explicitly documented as non-conformance coverage. |
| Browser confirmation prompt behavior for `consequentialHint` | Not asserted. The Step 7 invariant tests application workflow declarations and result completion, not a browser UI prompt. |
| Future post-157 status | Not asserted until Chrome Status or another public Chrome source provides a verified update. |
