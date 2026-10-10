# Final recorded run

Recorded 2026-10-09 (UTC) after completing the planned Step 0, 2–8, and 10
work. Optional Steps 9 and 11 remain listed in
[spec tracking](spec-tracking.md).

## Environment

| Component | Version |
| --- | --- |
| Python | 3.12.3 |
| Playwright | 1.63.0 |
| Playwright Chromium | 153.0.8010.12 |
| Evidence bundle format | 8.0 |

## Commands and outcomes

| Command | Outcome |
| --- | --- |
| `.venv/bin/pytest -q` | 178 passed, 1 skipped |
| `.venv/bin/pytest -m e2e` | 20 passed, 1 skipped, 158 deselected |
| `.venv/bin/python -m compileall -q src` | Passed |

The skipped test requires a detected Chrome 155 or later to exercise native
structured arguments. Chromium 153 remains the recorded local baseline; the
Chrome-beta CI matrix exercises that capability when available and skips it
with a reason when it is not.
