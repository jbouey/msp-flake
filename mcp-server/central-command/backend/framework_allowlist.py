"""Single-source allowlist for end-to-end-bound compliance frameworks.

#41 Phase A Gate A P0-4 closure. Extracted as a tiny zero-dep
module so callers (frameworks.py, framework_templates.py,
compliance_frameworks.py, tests) can import without dragging
SQLAlchemy / asyncpg / FastAPI into their dependency graph.

To ADD a framework: each entry requires (a) question bank in
`{framework}_templates.py`, (b) ≥12 control_mappings.yaml checks,
(c) `evidence_framework_mappings` backfill via `framework_sync.py`.
Adding to this set without those preconditions = customer-facing
silent-zero on the framework's dashboard scoring (the #41 Gate A
root-cause issue).

CI gate `tests/test_framework_allowlist_lockstep.py` enforces that:
  - this constant is the only definition
  - frameworks.py binds to it (no literal valid_frameworks set)
  - framework_templates.py has a branch for every entry
  - silent-fallback shape is absent
"""
from __future__ import annotations


SUPPORTED_FRAMEWORKS: frozenset[str] = frozenset({"hipaa", "soc2", "glba"})
