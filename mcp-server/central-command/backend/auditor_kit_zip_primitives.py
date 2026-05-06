"""Auditor-kit ZIP determinism primitives (round-table 2026-05-06).

Extracted from evidence_chain.py so the determinism contract test
can import the EXACT helpers the production endpoint uses without
pulling in FastAPI / asyncpg / pydantic at test collection time.

The contract these primitives enforce:

* Every ZIP entry has a pinned `date_time` (caller-supplied via
  `mtime`), pinned `compress_type=ZIP_DEFLATED`, and pinned
  `external_attr=0o644 << 16` (file mode bits).
* The `compresslevel` is fixed at zlib level 6 across CPython
  builds (some platforms otherwise default to a different level
  via their bundled zlib, which would break byte-identity).
* Callers are responsible for sorting entry order and using
  `sort_keys=True` on JSON dumps.

If you change ANYTHING in this file, the determinism contract
test (`tests/test_auditor_kit_deterministic.py`) AND the
source-shape gate (`test_zwrite_helper_pins_date_time_and_compress`)
must both stay green. Two consecutive downloads of an unchanged
chain MUST produce byte-identical ZIPs.

This module has zero non-stdlib imports by design.
"""
from __future__ import annotations

import zipfile
from typing import Tuple, Union

# zlib level 6 — pin explicitly so byte-identity holds across
# CPython builds with different bundled zlib defaults (Coach P1-3,
# 2026-05-06).
_KIT_COMPRESSLEVEL: int = 6

# ZIP minimum legal date — used as a safe fallback when the chain
# head bundle has no `created_at`. Real production kits derive
# zip_mtime from `latest.created_at`; this default is only hit by
# tests / pathological data.
_KIT_DEFAULT_MTIME: Tuple[int, int, int, int, int, int] = (
    1980, 1, 1, 0, 0, 0,
)


def _kit_zwrite(
    zf: zipfile.ZipFile,
    name: str,
    data: Union[str, bytes],
    mtime: Tuple[int, int, int, int, int, int],
) -> None:
    """Deterministic ZIP write helper.

    Pins `date_time` (caller-supplied), `compress_type=ZIP_DEFLATED`,
    and `external_attr=0o644 << 16`. The `compresslevel` is pinned
    on the surrounding ``zipfile.ZipFile(..., compresslevel=...)``
    constructor — see `_KIT_COMPRESSLEVEL`. Together these eliminate
    every per-entry source of non-determinism in the ZIP local
    headers and central directory.

    `data` may be ``str`` or ``bytes``; ``ZipInfo``+``writestr``
    handles both. ``mtime`` is a 6-tuple ``(Y, M, D, h, m, s)``;
    callers should derive it deterministically (e.g. from the
    chain-head bundle's ``created_at``), never from wall-clock.
    """
    zi = zipfile.ZipInfo(filename=name, date_time=mtime)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    zf.writestr(zi, data)
