# WebMCP Resilience agent guidance

Use the JSON CLI workflow for automation and evidence handling:

```bash
webmcp validate .webmcp/scenarios/example.yaml --json
webmcp run .webmcp/scenarios/example.yaml --ci --json
webmcp replay .webmcp/runs/<run-id>/bundle.json --json
```

Treat `.webmcp/runs/<run-id>/bundle.json` as the portable evidence and replay
contract. Do not delete saved failure artifacts (`failure.yaml`, `repro.yaml`,
trace, network evidence, screenshots, or bundles); they are needed for
reproducibility and incident analysis. Scenario YAML remains the executable
input to the CLI. The console must replay bundles through `webmcp replay`, and
must not bypass the CLI mutation guard.
