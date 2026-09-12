# Resilience Forge: the human/tool race

This is a deliberately vulnerable WebMCP application fixture. The lab exposes
`claim_slot`, a single-capacity operation with an async gap between its
availability check and commit. A human can click **Claim the single slot** at
the same time that an agent invokes the tool. Both callers can observe the
same available slot, so the observable invariant fails with two active claims.

The bug is in `examples/lab/app.js`; the scenario is the executable contract:
`.webmcp/scenarios/vulnerable-human-tool-race.yaml`. It is a regression asset,
not a production fix. Do not delete saved failure artifacts to make a run pass.

## CLI loop

From this directory, with the repository virtualenv active:

```bash
# Terminal 1, from the repository root
webmcp demo

# Terminal 2, from examples/resilience-forge
webmcp preflight --path / --ci --run-id inspect --json
webmcp validate .webmcp/scenarios/vulnerable-human-tool-race.yaml --run-id validated --json
webmcp run .webmcp/scenarios/vulnerable-human-tool-race.yaml --ci --adversarial --seed 7 --allow-mutations --run-id race-failure --json
webmcp replay .webmcp/runs/race-failure/bundle.json --ci --allow-mutations --run-id race-replay --json
webmcp diff .webmcp/runs/race-failure/bundle.json .webmcp/runs/race-replay/bundle.json --json
```

The complete first-run handoff is also one command from the repository root:

```bash
webmcp demo-race --seed 7
webmcp demo-race --seed 7 --json
```

It starts this local fixture, performs preflight and validation, runs the
seeded adversarial schedule, reduces the failure, and replays the saved bundle
through `CommandAPI`. The JSON form is one stable document; the human form
prints the compact failure handoff.

The run exits non-zero because the invariant is supposed to fail. Inspect the
redacted textual evidence with `webmcp console` or the local MCP control demo.
The saved bundle contains the exact adversarial schedule; replay does not
re-roll it. The reducer also writes `repro.yaml` after confirming that the
failure survives removal of the agent-side wait; the human-side wait remains
as the declared observation point that lets deferred commits become visible.

For same-offset groups, the runner starts eligible WebMCP tool promises in the
page before issuing the UI command. This is why the fixture exercises a real
async check-then-commit race rather than relying on an enlarged sleep alone.
The logical guarantee does not extend to sub-frame, CDP-level, or microtask-
level timing: Playwright page commands remain transport-serialised.

To model a handoff to another developer or coding agent, copy only the
bundle and replay that redacted evidence from its new location:

```bash
cp .webmcp/runs/race-failure/bundle.json /tmp/webmcp-race-bundle.json
webmcp replay /tmp/webmcp-race-bundle.json --ci --allow-mutations --run-id copied-replay --json
```

## Local MCP agent-control loop

Keep `webmcp demo` running, then run:

```bash
python agent_control_demo.py
```

The script starts the read/write-authorized local stdio control adapter and
performs the same sequence through typed MCP calls:

```text
inspect -> validate -> run -> failure evidence -> replay
```

`--allow-mutations` is an explicit operator decision for this fixture. The
agent cannot add that permission in a request. The agent receives redacted
text and metadata-only sensitive artifacts, and the run still goes through
the same `CommandAPI`, scheduler, reducer, bundle validation, and replay path
as the CLI.

For the terminal console flight deck:

```bash
webmcp console .webmcp/runs/race-failure/bundle.json --replay-allow-mutations
```

Press `m` for the safe minimized reproduction view, `c` to compare a replay
bundle, or `r` to invoke the portable replay contract. `--print` provides a
non-interactive evidence view.
