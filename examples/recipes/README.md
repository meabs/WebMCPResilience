# Scenario recipes

These are deliberately small, declarative starting points. Rename tools,
selectors, arguments, paths, and invariant fields to match your app, then run
`webmcp validate` before allowing mutations.

- `cart-double-add.yaml` — repeated cart mutation through a tool.
- `double-place-order.yaml` — simultaneous tool and human order placement.
- `book-form-hang.yaml` — a per-action timeout for a form completion hang.
- `declarative-navigate.yaml` — a browser navigation with a final-state check.

Configure an explicit `state_script` in `.webmcp/config.yaml` for every recipe
that has state or final invariants.
