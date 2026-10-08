# Unregister during invoke fixture

This fixture demonstrates a tool registration being aborted while its
already-started invocation is pending. It requires Chrome 153 or later and
uses the fixture-only `__webmcpResilienceUnregisterTool` hook, which aborts the
same `AbortController` signal used for registration.

From the repository root:

```bash
# Terminal 1
python3 -m http.server 4173 --directory examples/unregister-during-invoke

# Terminal 2
cd examples/unregister-during-invoke

# Vulnerable variant exits 1 after leaving partial state.
../../.venv/bin/webmcp run vulnerable.yaml \
  --ci --allow-mutations --run-id unregister-vulnerable --json

# The saved failure replays into a distinct directory and again exits 1.
../../.venv/bin/webmcp replay .webmcp/runs/unregister-vulnerable/bundle.json \
  --ci --allow-mutations --run-id unregister-vulnerable-replay --json

# Safe variant exits 0 because the pending invocation completes.
../../.venv/bin/webmcp run safe.yaml \
  --ci --allow-mutations --run-id unregister-safe --json
```

The included `.webmcp/config.yaml` supplies the required observable-state
boundary: `window.__unregisterFixture.getState()`.

On Chrome versions below 153, or against a target without the hook, the runner
records `fault.skipped` with a reason and continues instead of failing.
