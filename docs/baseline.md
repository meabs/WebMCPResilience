# Step 0 baseline

Recorded 2026-10-07 (UTC) on Linux 6.12.94+.

## Environment

The README specifies a standard virtual environment, editable installation with
the `dev` extra, and Playwright Chromium:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/playwright install chromium
```

The base image did not include `ensurepip`, so the first command failed with
the system message requesting `python3.12-venv`. The completed setup used:

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
rm -rf .venv
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/playwright install chromium
```

Installed versions:

| Component | Version |
| --- | --- |
| Python | 3.12.3 |
| Playwright Python package | 1.63.0 |
| Playwright Chromium | 153.0.8010.12 |
| WebMCP Resilience | 0.1.0 (editable) |

Chromium was recorded by launching the installed Playwright `chromium` browser
and reading `browser.version`. The bundled browser's user agent also reported
`HeadlessChrome/153.0.8010.12`.

## Commands and outcomes

| Command | Exit status | Outcome |
| --- | ---: | --- |
| `.venv/bin/pytest -q` | 0 | 159 passed in 46.00s |
| `.venv/bin/pytest -m e2e` | 0 | 14 passed, 145 deselected, in 41.54s |
| `.venv/bin/webmcp preflight --ci --json` | 2 | Failed: `http://localhost:3000/` refused the connection. No entry point could be detected from this command. |
| `.venv/bin/webmcp demo-race --json` | 0 | The intentional vulnerable race failed its invariant, produced `.webmcp/runs/demo-race-failure/bundle.json`, and its built-in replay produced the same intentional failure. |
| `.venv/bin/webmcp replay .webmcp/runs/demo-race-failure/bundle.json --allow-mutations --json` | 1 | Replayed the expected `claims.active <= claims.capacity` invariant violation, with unchanged tool-contract fingerprint and `allowed_exact_match` replay decision. |

The standalone replay requires the lab server because `demo-race` stops its
temporary server on completion. The server and replay commands were:

```bash
.venv/bin/webmcp demo
.venv/bin/webmcp replay .webmcp/runs/demo-race-failure/bundle.json --allow-mutations --json
```

An exit status of 1 from the replay is expected here: the replay successfully
reproduces an intentionally failing scenario. It wrote a new failing bundle
and reported `error_code: "invariant_violation"`, rather than an execution or
replay-compatibility error.

The demo preflight evidence detected `document.modelContext` through the
project's compatibility host (`mode: "compatibility_host"`, `native: false`).
The standalone project preflight did not reach a page because its default
target was not running.

## Version-facts verification

Checked 2026-10-07 against the [Chrome WebMCP Imperative API
documentation](https://developer.chrome.com/docs/ai/webmcp/imperative-api) and
the [WebMCP Chrome Status
record](https://chromestatus.com/feature/5117755740913664/activity).

| Plan fact | Result | Evidence |
| --- | --- | --- |
| `navigator.modelContext` deprecated; `document.modelContext` canonical in Chrome 150 | Cannot confirm the Chrome 150 milestone from the current public imperative API page. The page consistently documents `document.modelContext`, but does not mention `navigator.modelContext` or a 150 transition. | [Imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api) |
| Unregistering no longer cancels in-flight work in Chrome 153 | Confirmed. The documentation says “As of Chrome 153” unregistering a tool does not cancel or break in-flight executions. | [Unregister tools](https://developer.chrome.com/docs/ai/webmcp/imperative-api#unregister-tools) |
| JSON-stringified input arguments deprecated in Chrome 155 | Confirmed. The documentation says JSON-stringified input arguments are deprecated from Chrome 155. | [Execute tool](https://developer.chrome.com/docs/ai/webmcp/imperative-api#execute-tool) |
| `debugging` annotation available in Chrome 156 | Confirmed. The annotation documentation says it is available from Chrome 156. | [Tool annotations](https://developer.chrome.com/docs/ai/webmcp/imperative-api#tool-annotations-optional) |
| Origin trial spans Chrome 149 through 156 | Cannot confirm the first and last release from the current public page or accessible Chrome Status record. The documentation links an origin trial, but does not state those milestones. | [Imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api), [Chrome Status](https://chromestatus.com/feature/5117755740913664/activity) |

## Source observations for later steps

These observations describe the baseline only; this step makes no behavior
change.

- The adapter's primary selection is already
  `document.modelContext ?? navigator.modelContext` in
  `src/webmcp_resilience/browser/adapter.js`. The fallback is active without an
  explicit compatibility-path comment. The probe already reports the selected
  location as `document.modelContext` or `navigator.modelContext`.
- There are no production references to `modelContextTesting` or
  `provideContext`.
- Tool fingerprints already include `annotations`: the canonical contract has
  `name`, input schema, output schema, and annotations in
  `src/webmcp_resilience/tool_contracts.py`. An unknown annotation therefore
  currently changes the fingerprint and is classified as an annotation change
  requiring human review.
- Invocation arguments are stringified in
  `src/webmcp_resilience/browser/adapter.js` when the selected profile is
  `legacy-string`, the compatibility host is used, or detected Chrome is
  earlier than 155. Chrome 155 and later use structured objects for a native
  host under the `auto` profile.
- The baseline browser is Chrome 153, so any later capability that requires
  Chrome 155 or 156 must be capability-gated and skipped with a reason in
  this environment.
