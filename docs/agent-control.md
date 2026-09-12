# Agent control and safety

WebMCP Resilience can be operated entirely through the CLI. The optional local
MCP server exists so coding agents can use typed operations without parsing
human terminal output.

```bash
# Default: fixed, read-only policy.
.venv/bin/webmcp agent-server --project-root .

# Explicit operator authority for an isolated mutable target only.
.venv/bin/webmcp agent-server --project-root . --allow-mutations
```

## One execution path

The stdio MCP server is a policy adapter over `CommandAPI`; it is not a browser
driver or a separate runner. CLI commands, the terminal console, local MCP
requests, and Python integration use the same validation, scheduling, bundle,
reduction, and replay paths.

## What an agent can do

The server advertises typed MCP tool schemas through `tools/list`. Operations
cover capabilities, preflight, scenario listing and validation, deterministic
templates, run and replay, run summaries, filtered trace slices, diffs,
textual failure/reproduction evidence, and artifact metadata.

All successful responses use a versioned envelope. Scenario and run references
are project-relative or validated run identifiers; callers cannot select
arbitrary filesystem paths.

## Immutable startup policy

The operator fixes these boundaries when starting the server:

- project root
- allowed HTTP(S) target origins
- mutation permission
- maximum concurrent agent operations
- artifact sensitivity policy and approval context

Requests cannot expand those boundaries. Navigation values are rejected when
they are ambiguous, scheme-relative, cross-origin, encoded to hide separators,
or rely on browser backslash normalisation. URLs are resolved and then checked
against allowed origins before Playwright receives them.

Read-only mode blocks mutating UI actions (`click`, `fill`, `select`),
cancellation, and tools without a `readOnlyHint`. `navigate` and `wait` remain
available only within the fixed target policy.

## Evidence safety

Bundles redact structured sensitive fields and URL credential/query values.
Console and agent display paths redact again so imported bundles are not trusted
solely because their producer claims they were sanitized. Potentially sensitive
screenshots are never returned as agent content; the agent receives metadata
only. Redacted textual failure/reproduction artifacts are available when their
artifact policy permits them.

## Scope boundary

These controls are defence in depth for unsafe agent-originated test requests.
They are not a claim of prompt-injection prevention, comprehensive SSRF
containment, remote identity management, or centrally managed enterprise
governance. Keep the server local, narrowly scoped, and pointed at an isolated
test target when mutations are permitted.
