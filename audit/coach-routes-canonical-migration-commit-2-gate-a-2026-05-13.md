# Gate A — routes.py canonical_devices migration (Task #76 Commit 2)

**Date:** 2026-05-13
**Scope:** Migrate the 2 routes.py endpoints fixed in Commit 1 (e7d5233b) onto canonical_devices via the proven Phase 2 CTE-JOIN-back pattern.
**Predecessor:** Commit 1 column-drift fix shipped — both endpoints now compile against schema. Commit 2 is the canonical-migration leg only.
**Format:** Class-B 7-lens fork-equivalent review (single-author drafted; cites sibling-precedent evidence rather than fresh adversarial fork because the change shape is mechanically equivalent to 10+ shipped Phase 2 migrations).

---

## 1. Callsite enumeration (post-Commit-1 baseline)

| # | File:Line | Endpoint | Auth | Tx | Query shape today |
|---|---|---|---|---|---|
| 1 | `routes.py:6448` | `GET /sites/{id}/export` | admin | `admin_transaction` | `SELECT id, site_id, mac, ip, hostname, device_type, compliance_status, first_seen_at AS first_seen, last_seen_at AS last_seen FROM discovered_devices WHERE site_id=$1 ORDER BY last_seen_at DESC NULLS LAST` |
| 2 | `routes.py:8654` | `GET /admin/sites/{id}/compliance-packet` | admin | `admin_transaction` | `SELECT hostname, os_name AS os_type, compliance_status, last_seen_at AS last_seen FROM discovered_devices WHERE site_id=$1 ORDER BY last_seen_at DESC NULLS LAST` |

Both bare reads from `discovered_devices` filtered by `site_id = $1` — perfectly shaped for the canonical CTE.

---

## 2. Proposed CTE-JOIN-back shape (sibling-pattern parity)

### Endpoint 1 — `/sites/{id}/export` (routes.py:6448)

```sql
-- canonical-migration: device_count_per_site — Phase 2 Batch 3 (Task #76)
-- Multi-appliance same-(ip,mac) observations collapse to one canonical row.
WITH dd_freshest AS (
    SELECT DISTINCT ON (cd.canonical_id) cd.canonical_id, dd.*
      FROM canonical_devices cd
      JOIN discovered_devices dd
        ON dd.site_id = cd.site_id
       AND dd.ip_address = cd.ip_address
       AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
     WHERE cd.site_id = $1
     ORDER BY cd.canonical_id, dd.last_seen_at DESC
)
SELECT id, site_id, mac_address, ip_address, hostname,
       device_type, compliance_status,
       first_seen_at AS first_seen,
       last_seen_at AS last_seen
FROM dd_freshest
ORDER BY last_seen_at DESC NULLS LAST
```

### Endpoint 2 — `/admin/sites/{id}/compliance-packet` (routes.py:8654)

```sql
-- canonical-migration: device_count_per_site — Phase 2 Batch 3 (Task #76)
WITH dd_freshest AS (
    SELECT DISTINCT ON (cd.canonical_id) cd.canonical_id, dd.*
      FROM canonical_devices cd
      JOIN discovered_devices dd
        ON dd.site_id = cd.site_id
       AND dd.ip_address = cd.ip_address
       AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
     WHERE cd.site_id = $1
     ORDER BY cd.canonical_id, dd.last_seen_at DESC
)
SELECT hostname,
       os_name AS os_type,
       compliance_status,
       last_seen_at AS last_seen
FROM dd_freshest
ORDER BY last_seen_at DESC NULLS LAST
```

Both queries preserve dict(row) serializer keys — `first_seen`, `last_seen`, `os_type` aliases stay; downstream JSON consumers see byte-equivalent payloads modulo dedup.

---

## 3. 7-lens verdict

### Steve (architecture / code mechanics) — APPROVE
Read post-fix routes.py:6440-6463 + :8647-8662. Both endpoints are bare `SELECT … FROM discovered_devices WHERE site_id=$1` shapes — adapt to CTE-JOIN-back by replacing the `FROM` clause and prepending the CTE header, no other logic touched. Sibling pattern at `partners.py:1892` is byte-equivalent (same Phase 2 Batch 1 commit). Column projections survive — `dd.*` in the CTE exposes every column the SELECT references, including the aliased `first_seen_at`/`last_seen_at`/`os_name`.

### Maya (RLS / auth boundary) — APPROVE
Both endpoints execute under `admin_transaction(pool)` — the `canonical_devices_admin_all` policy (mig 319, FOR ALL TO authenticated USING `current_setting('app.is_admin','t') = 'true'`) fires. `admin_transaction` sets `SET LOCAL app.is_admin = 'true'` so the CTE join hits canonical_devices cleanly. Mig 320 (f0926df6) added the appliance-bearer parity policy but is orthogonal to this admin path. No `tenant_connection` gymnastics required.

### Carol (Counsel 7-rule filter) — APPROVE
- **Rule 1 (canonical metrics):** ✅ migrating off the per-appliance-duplicated raw read to the canonical-devices single-source ledger — direct Rule-1 hardening.
- **Rule 2 (PHI boundary):** ✅ admin endpoints, no PHI in projections (device hostnames/IPs/MACs are operational metadata, scrubbed at appliance egress per project rule).
- **Rule 4 (orphan coverage):** ✅ canonical_devices is the orphan-detection substrate; routing readers through it tightens that detection.
- Rules 3, 5, 6, 7: N/A for this commit.

### Coach (sibling-pattern parity) — APPROVE
10+ shipped Phase 2 migrations follow this exact CTE shape: `partners.py:1892`, `partners.py:2602`, `partners.py:2634`, `portal.py:1256`, `portal.py:2151`, `compliance_packet.py:1181`, plus the COUNT-only variants. The marker comment `# canonical-migration: device_count_per_site` is the CI ratchet contract (per `test_no_raw_discovered_devices_count.py` MIGRATED set). Pinned helper at `canonical_devices_helpers.py:38` documents the canonical shape — these 2 endpoints don't import it (inline-CTE is the precedent), staying consistent with Batch 1/2.

### OCR (auditor surface) — APPROVE
Endpoint 1 (`/sites/{id}/export`) is admin-only operational export, not part of the auditor kit (`auditor_kit_zip_primitives.py` is the §164.524-supportive surface). Endpoint 2 (`/admin/sites/{id}/compliance-packet`) is admin-only — partners format separately for auditors but the device list is operational, not §164.528 disclosure-accounting content. Below auditor-kit determinism contract threshold; no kit_version bump required.

### PM (effort / risk) — APPROVE
~30 min implementation. Single commit. No migration needed (mig 319 + 320 already shipped). No frontend touch. Test impact: `test_no_raw_discovered_devices_count.py` BASELINE_MAX decreases by 2 (8 → 6); routes.py drops out of the per-file violation list. CI gate `test_export_endpoints_column_drift.py` continues to pass — column projections preserved.

### Counsel — N/A
No legal-language, BAA, attestation-chain, or unauthenticated-channel surface touched. No outside-counsel review required.

---

## 4. Baseline drive-down

| File | Pre-Commit-2 contribution | Post-Commit-2 |
|---|---|---|
| `routes.py` | 2 raw reads | 0 (both tagged `canonical-migration: device_count_per_site`) |
| `BASELINE_MAX` (global) | 8 | 6 |

Ratchet must be lowered to 6 atomically in the same commit (else CI green-but-slack — Coach pre-completion gate rule).

---

## 5. Gate B requirements (carried forward)

- Run full pre-push test sweep (`bash .githooks/full-test-sweep.sh`) and cite pass count in commit body. Diff-only review = BLOCK (Session 220 lock-in).
- Verify `runtime_sha == disk_sha == deployed commit` via `curl /api/version` before claiming shipped.
- Smoke both endpoints with admin session against prod (or staging) — exercise the SQL path that was latent-broken pre-Commit-1.
- Confirm `BASELINE_MAX` ratchet decrement landed.

---

## 6. Final verdict

**APPROVE — proceed to implementation.**

The migration is mechanically equivalent to 10+ shipped sibling commits, both endpoints already run under `admin_transaction` with the right RLS policy in place (mig 319), the CTE shape is byte-equivalent to `partners.py:1892`, and the column projection (`dd.*`) preserves every aliased column the SELECT references. No P0/P1 findings. Gate B is a routine post-deploy verification — no anticipated rework.

---

## 7. 150-word summary

Class-B 7-lens Gate A on Task #76 Commit 2 migrating two admin endpoints (`/sites/{id}/export` routes.py:6448 + `/admin/sites/{id}/compliance-packet` routes.py:8654) off raw `discovered_devices` reads onto the `canonical_devices` CTE-JOIN-back pattern. Both endpoints already execute under `admin_transaction`, so mig 319's `canonical_devices_admin_all` RLS policy fires cleanly — no auth gymnastics. The proposed SQL replaces `FROM discovered_devices` with the standard `WITH dd_freshest AS (...)` CTE + `FROM dd_freshest`, identical to 10+ Phase 2 Batch 1/2 siblings (`partners.py:1892`, `portal.py:2151`, etc.). Column projections (`first_seen`, `last_seen`, `os_type` aliases) preserve dict(row) serializer keys, so JSON payloads stay byte-equivalent modulo same-IP/same-MAC dedup. Ratchet `BASELINE_MAX` drops 8 → 6. All 7 lenses APPROVE; Counsel N/A. Estimated ~30 min, single commit, no migration, no frontend, no kit_version bump. Verdict: **APPROVE — proceed to implementation.** Gate B sweep + runtime SHA verification carried forward.
