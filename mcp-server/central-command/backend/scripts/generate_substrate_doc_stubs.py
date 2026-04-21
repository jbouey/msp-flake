"""Generate one docs/substrate/<invariant>.md stub per entry in
assertions.ALL_ASSERTIONS.

Re-run is safe — never overwrites a populated file (skips if file
exceeds template size + 100 bytes of prose, i.e. an operator has
started filling it in).

Usage (from backend/):
    python3 scripts/generate_substrate_doc_stubs.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

# scripts/ sits under backend/; add backend/ to sys.path so the
# assertions module imports cleanly regardless of invoker cwd.
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from assertions import ALL_ASSERTIONS, _DISPLAY_METADATA  # noqa: E402

# backend/ is 3 levels under repo root:
#   mcp-server/central-command/backend/scripts/
# Walk up 4 parents from this file to reach repo root.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
DOCS_ROOT = REPO_ROOT / "docs" / "substrate"
TEMPLATE_PATH = DOCS_ROOT / "_TEMPLATE.md"


def main() -> int:
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_PATH.exists():
        print(
            f"ERROR: template missing at {TEMPLATE_PATH}. "
            "Check docs/substrate/_TEMPLATE.md was created by Task 8 Step 1.",
            file=sys.stderr,
        )
        return 1

    template = TEMPLATE_PATH.read_text()
    today = dt.date.today().isoformat()
    created = 0
    skipped = 0
    for assertion in ALL_ASSERTIONS:
        name = assertion.name
        target = DOCS_ROOT / f"{name}.md"
        meta = _DISPLAY_METADATA.get(name, {})
        if target.exists() and target.stat().st_size > len(template) + 100:
            skipped += 1
            continue
        body = (
            template
            .replace("{{invariant}}", name)
            # Assertion.severity is already "sev1"/"sev2"/"sev3" — don't prefix.
            .replace("{{severity}}", assertion.severity)
            .replace("{{display_name}}", meta.get("display_name", name))
            .replace("{{today}}", today)
        )
        target.write_text(body)
        created += 1

    total = len(ALL_ASSERTIONS)
    print(
        f"Created/updated {created} stub(s), preserved {skipped} populated "
        f"stub(s) across {total} invariants."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
