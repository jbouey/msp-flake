# Software Provenance & Time Framework

> **Last verified:** 2026-05-16 (Session 220 doc refresh).
>
> **Canonical current authority:** `~/Downloads/OsirisCare_Owners_
> Manual_and_Auditor_Packet.pdf` Part 2 §2.2 (cryptographic chain).
> Counsel's 7 Hard Rules (CLAUDE.md, 2026-05-13) are gold authority
> over this doc where they conflict — in particular **Rule 9:
> determinism and provenance are not decoration.**

<!-- updated 2026-05-16 — Session-220 doc refresh -->

## Overview

**Core Principle:** Every action, every build, every log entry must be cryptographically provable as authentic and temporally ordered.

In healthcare compliance, you need to prove:
- *What* happened
- *When* it happened
- *Who* did it
- That evidence hasn't been tampered with

This framework makes tampering mathematically detectable. Per Counsel's framing 2026-05-06 the **§164.528 disclosure-accounting standard is substantive completeness + retrievability**, NOT cryptographic immutability — the chain is integrity *hardening* on top of the substantive record.

## Current Production State (2026-05-16)

| Component | State |
|-----------|-------|
| Evidence table | `compliance_bundles` — ~245K+ entries, partitioned monthly (mig 138). NOT `evidence_bundles` (legacy 1-row table). |
| Signing | Ed25519, per-appliance (`site_appliances.agent_public_key` — NEVER `sites.agent_public_key`). Multi-appliance sites use per-appliance keys, not site-level. |
| Heartbeat signing | D1 — Ed25519-signed at daemon, server-verified (mig 313, Session 220). Substrate invariants: `daemon_heartbeat_unsigned` / `_signature_invalid` / `_signature_unverified`. |
| Time anchor | OpenTimestamps (OTS). Merkle-batched, Bitcoin-anchored. `merkle_batch_stalled` sev1 invariant. `ots_proofs.status` CHECK locks out `'verified'` (mig 307). |
| Chain on partitioned table | `ON CONFLICT (bundle_id)` is INCOMPATIBLE — use DELETE+INSERT upsert pattern. |
| Vault Transit | LIVE in shadow mode. Hetzner `89.167.76.203` / WG `10.100.0.3`. Ed25519 non-exportable. 1Password owns unseal shares. Root token revoked. Dual-write byte-identical signatures; hot-cutover pending. |
| Site rename | `rename_site()` SQL function only (mig 257). Direct `UPDATE … SET site_id =` outside the per-line `# noqa: rename-site-gate` allowlist fails CI (`tests/test_no_direct_site_id_update.py`). |
| Canonical site | `canonical_site_id()` for telemetry/operational aggregations (mig 256). **NEVER for compliance_bundles** — Ed25519 + OTS bind to original site_id forever; CI gate `tests/test_canonical_not_used_for_compliance_bundles.py`. |
| Cross-org chain crossing | RT21 cross-org relocate (2026-05-06): chain stays anchored at original `site_id`; `sites.prior_client_org_id` provides the lookup pointer. Substrate invariant `cross_org_relocate_chain_orphan` (sev1) catches bypass-path. |
| Auditor kit | Deterministic ZIP (byte-identical re-downloads), `kit_version='2.1'` pinned across 4 surfaces. See "Auditor Kit Determinism" below. |
| Migration ledger | `RESERVED_MIGRATIONS.md` pre-claim required (Task #59). |

## What NixOS Gives You Free

NixOS's content-addressed store provides foundational provenance:

1. **Content Addressing** — every derivation has unique hash based on ALL inputs.
2. **Reproducible Builds** — same inputs → identical binary → same hash.
3. **Derivation Files** — machine-readable record of every build.
4. **Closure Tracking** — complete dependency graph.

```bash
# Query what built a package
nix-store --query --deriver /nix/store/abc123-nginx-1.24.0

# Get complete dependency graph
nix-store --query --requisites /nix/store/abc123-nginx-1.24.0

# Verify integrity
nix-store --verify --check-contents /nix/store/abc123-nginx-1.24.0
```

## What This Framework Adds

- Cryptographic signatures proving WHO authorized builds (per-appliance Ed25519).
- SBOM export in SPDX/CycloneDX formats.
- Multi-source time attestation + OTS Bitcoin anchoring.
- Hash chain linking evidence over time (unified across drift + remediation + privileged events).
- 4-element Privileged-Access Chain of Custody (CLI → API → DB-trigger).
- Substrate Integrity Engine — ~60 invariants every 60s assert chain progress + gap detection.
- Canonical metric registry (Counsel Rule 1) — every customer-facing metric has a declared canonical source.

## Privileged-Access Chain of Custody (INVIOLABLE)

`client identity → policy approval → execution → attestation` is an unbroken cryptographically verifiable chain. Any privileged action carries the chain end-to-end. Enforced at three layers:

- **CLI** (`backend/fleet_cli.py`) — refuses privileged orders without `--actor-email` + `--reason ≥20ch` + successful `create_privileged_access_attestation()`. Rate-limited 3/site/week.
- **API** (`backend/privileged_access_api.py`) — partner-initiated + client-approved request flow. Each state transition writes a chained attestation bundle.
- **DB** (migration 175 `trg_enforce_privileged_chain`) — REJECTS any `fleet_orders` INSERT of a privileged type unless `parameters->>'attestation_bundle_id'` matches a real `compliance_bundles WHERE check_type='privileged_access'` row for the same site.

The attestation is Ed25519-signed by server, hash-chained to the site's prior evidence bundle, OTS-anchored, and published into `/api/evidence/sites/{id}/auditor-kit` ZIP + the client portal evidence view.

**Three lists in lockstep** (any gap = chain violation):
- `fleet_cli.PRIVILEGED_ORDER_TYPES`
- `privileged_access_attestation.ALLOWED_EVENTS`
- mig 175 `v_privileged_types` in `enforce_privileged_order_attestation()`

**Mig 305 (Session 220) added `delegate_signing_key`** as the 5th privileged type. Weekly audit cadence found `appliance_delegation.py:258 POST /delegate-key` was zero-auth — anyone could mint an Ed25519 signing key bound to any caller-supplied appliance_id. Functionally equivalent to `signing_key_rotation`. All 3 lists updated in lockstep + Python-only allowlist entry in `tests/test_privileged_order_four_list_lockstep.py::PYTHON_ONLY`. Prod audit at fix time: 1 historical row, synthetic test data, already expired — zero customer exposure.

**Trigger functions are ADDITIVE-ONLY** (lockstep checker proves LIST parity but NOT body parity). NEVER rewrite `enforce_privileged_order_attestation` or `enforce_privileged_order_immutability` from scratch when extending `v_privileged_types` — copy prior migration's function body verbatim and append only the new array entry.

## Build Signing

```nix
{ config, lib, pkgs, ... }: {
  options.services.msp.buildSigning = {
    enable = mkEnableOption "MSP build signing";
    signingKey = mkOption { type = types.path; };
    publicKeys = mkOption { type = types.listOf types.str; };
  };

  config = mkIf cfg.enable {
    nix.settings = {
      require-sigs = true;
      trusted-public-keys = cfg.publicKeys;
      secret-key-files = mkIf (cfg.signingKey != null) [ cfg.signingKey ];
    };

    nix.settings.post-build-hook = pkgs.writeShellScript "sign-build" ''
      for path in $OUT_PATHS; do
        nix store sign --key-file ${cfg.signingKey} "$path"
      done
    '';
  };
}
```

## Evidence Signing (Production)

Production uses per-appliance Ed25519 (not cosign). Ed25519 keys are stored in `site_appliances.agent_public_key`. Vault Transit shadow-mode dual-writes byte-identical signatures (file-key + Vault-key) — hot-cutover pending.

```python
# Conceptual — actual implementation is in appliance/internal/evidence/
# and uses asyncpg + DELETE+INSERT (partitioned table — no ON CONFLICT).

class EvidenceSigner:
    def sign_bundle(self, bundle_path: Path) -> dict:
        # Ed25519 sign using per-appliance key
        sig = ed25519_sign(self.appliance_priv_key, bundle_hash)
        return {
            "bundle_path": str(bundle_path),
            "signature": sig,
            "signed_at": now_utc().isoformat(),
            "algorithm": "Ed25519",
            "bundle_hash": self._compute_hash(bundle_path),
            "signer_key_id": self.appliance_pub_key_fingerprint,
        }
```

## Evidence Registry (WORM, Partitioned)

`compliance_bundles` is partitioned by month (mig 138, Session 200). Default partition catches overflow. **ON CONFLICT is incompatible with partitioned tables** — use DELETE+INSERT pattern (Session 201).

`baa_signatures` is append-only (mig 312 adds `acknowledgment_only` flag). `feature_flags` is append-only (mig 281+282 — flag-flip events live here, INTENTIONALLY ABSENT from `ALLOWED_EVENTS` because the flag has no site anchor).

## SBOM Generation

Generate Software Bill of Materials in SPDX format from the NixOS store closure (unchanged from earlier revision).

## Multi-Source Time Synchronization

Multi-source NTP + OTS Bitcoin anchoring on every evidence bundle.

```nix
{ config, lib, pkgs, ... }: {
  services.msp.timeSync = {
    enable = true;
    tier = "professional";

    ntpServers = [
      "time.nist.gov"
      "time.cloudflare.com"
      "pool.ntp.org"
    ];

    gpsDevice = "/dev/ttyUSB0";  # Professional tier
    bitcoinEnabled = true;       # via OpenTimestamps
    maxDriftMs = 100;
  };

  services.chrony = {
    enable = true;
    servers = cfg.ntpServers;
    extraConfig = ''
      minsources 2
      maxdrift ${toString cfg.maxDriftMs}
    '';
  };
}
```

## Hash Chain Log Integrity

Unified chain across drift + remediation + privileged events. Chain is anchored at the site's primary `site_id`; client-org events use the org's first-created site or `client_org:<id>` synthetic fallback. Partner-org events use `partner_org:<partner_id>` synthetic. NEVER use `canonical_site_id()` for these anchors — chain is immutable, mapping is read-only.

## Auditor Kit Determinism Contract (Session 218 round-table 2026-05-06)

Two consecutive downloads of the auditor kit with no chain progression, no OTS pending→anchored transitions, no presenter-brand edits, and no advisory-set changes MUST produce **byte-identical ZIPs**. This is the load-bearing tamper-evidence promise — auditors hash the kit and compare across downloads to detect substitution.

**Implementation:**
- `auditor_kit_zip_primitives.py::_kit_zwrite` — pinned `date_time` + `compress_type=ZIP_DEFLATED` + `external_attr=0o644<<16`.
- `_KIT_COMPRESSLEVEL=6` — pinned zlib level for cross-CPython byte-identity.
- `sort_keys=True` on every JSON dump (chain.json, pubkeys.json, identity_chain.json, iso_ca_bundle.json, bundles.jsonl per-line).
- Sorted entry order + sorted OTS files + `ORDER BY iso_release_sha`.
- `generated_at` derives from chain-head `latest.created_at` (NOT wall-clock); wall-clock `download_at` is ONLY for audit-log + (deterministic) Content-Disposition filename.
- `kit_version='2.1'` pinned across 4 surfaces (X-Kit-Version header, chain_metadata, pubkeys_payload, identity_chain_payload, iso_ca_payload).

**Bans:** `datetime.now()` for kit-internal timestamps; skipping `sort_keys=True` on a kit JSON dump; bypassing `_kit_zwrite` for a ZIP entry; advancing `kit_version` on one surface without all four.

**Pinned by:** `tests/test_auditor_kit_integration.py` (10 tests open the actual ZIP) + `tests/test_auditor_kit_deterministic.py` (source-shape gates).

**Banned shape:** in-source `.format()` templates for customer-facing artifacts. Today's recurring kit 500s traced to literal `{bundle_id}` and JSON-example `{...}` blocks in `_AUDITOR_KIT_README` being interpreted as `.format()` placeholders → `KeyError`. ANY `{`/`}` in prose/JSON examples MUST be escaped as `{{`/`}}` until migration to Jinja2 (`backend/templates/auditor_kit/*.j2` with `StrictUndefined`).

## Auditor Kit Auth (5 branches, Session 218)

`require_evidence_view_access` accepts:
1. Admin session
2. `osiris_client_session` cookie + org owns site
3. `osiris_partner_session` cookie + `sites.partner_id` matches + role IN `{admin, tech}`
4. Legacy `portal_session` cookie
5. Legacy `?token=` query param (deprecation telemetry warns on each use)

**Billing-role partner_users MUST NOT pull evidence** (RT31 site-state class). Per-(site, caller) rate limit isolates buckets per identity. Every download writes a structured `auditor_kit_download` row to `admin_audit_log` (best-effort — failure logs at ERROR but does NOT block the §164.524 access right).

**BAA enforcement:** auditor-kit downloads are runtime-gated via `check_baa_for_evidence_export(_auth, site_id)` (BAA Workflow List 1: `evidence_export`).

**Framing:** the auditor kit is **audit-supportive technical evidence**, NOT a §164.528 disclosure accounting. README + ClientReports + PracticeHomeCard ship IDENTICAL §164.528 disclaimer copy.

## Blockchain Anchoring (Production via OTS)

Production uses OpenTimestamps (OTS), not direct Bitcoin OP_RETURN. Merkle-batched. `merkle_batch_stalled` (sev1) invariant catches stalls. `ots_proofs.status` CHECK constraint (mig 307) locks out the `'verified'` literal (correctness gate).

## Substrate Chain-Progress Invariants

Substrate Integrity Engine (`assertions.py`, 60s tick, per-assertion `admin_transaction`) asserts chain progress:

| Invariant | Sev | What it catches |
|-----------|-----|-----------------|
| `evidence_chain_stalled` | sev1 | No new bundles per site within window |
| `merkle_batch_stalled` | sev1 | OTS batch worker dry |
| `compliance_packets_stalled` | sev1 | Customer-visible packet generation halted |
| `compliance_bundles_trigger_disabled` | sev1 | Append-only trigger removed |
| `claim_event_unchained` | sev1 | Claim event with no chain ancestor |
| `signature_verification_failures` | sev1 | Evidence Ed25519 fails verification |
| `sigauth_crypto_failures` | sev1 | Sigauth bundle crypto failure |
| `daemon_heartbeat_unsigned` / `_signature_invalid` / `_signature_unverified` | sev1 | D1 heartbeat verification (mig 313) |
| `cross_org_relocate_chain_orphan` | sev1 | RT21 bypass detector |
| `pre_mig175_privileged_unattested` | sev1 | Pre-mig-175 privileged orders without attestation |
| `partition_maintainer_dry` | sev2 | Monthly partition not created |
| `schema_fixture_drift` | sev2 | Schema sidecars diverge from prod |
| `canonical_compliance_score_drift` | sev2 | Surface diverges from canonical helper |

## Compliance Tiers

| Feature | Essential | Professional | Enterprise |
|---------|-----------|--------------|------------|
| NTP time sync | Basic | Multi-source + GPS | + Bitcoin anchoring (via OTS) |
| Evidence bundles | Unsigned | Ed25519 signed (per-appliance) | + OTS anchored |
| Retention | 30 days | 90 days | 2 years |
| Hash chains | Local | + Remote backup | + 1-min intervals + Vault Transit (planned hot-cutover) |
| SBOM | None | SPDX | + CycloneDX |
| Heartbeat signing | n/a | D1 (signed + verified per mig 313) | D1 + per-event verification (gated on PRE-1 soak, MASTER_BAA v2.0 target) |

## MCP Tools

```python
# Chain verification tool
class VerifyChainTool:
    async def execute(self, params: Dict) -> Dict:
        # Production verification uses the auditor kit deterministic
        # ZIP + sha256 + OTS attestation. _verify_chain_integrity walks
        # the unified chain (drift + remediation + privileged).
        if not self._verify_chain_integrity():
            return {"status": "tampered",
                    "error": "Chain integrity compromised"}
        return {"status": "verified"}
```

## Adversarial Two-Gate Review

Every new system / migration / soak / load test affecting provenance receives a **fork-based 4-lens adversarial review** (Steve / Maya / Carol / Coach) at BOTH Gate A (pre-execution) AND Gate B (pre-completion). Both gates run via `Agent(subagent_type="general-purpose")` with a fresh context window — author-written counter-arguments do NOT count. **Gate B must run the full pre-push test sweep** (`bash .githooks/full-test-sweep.sh` ~92s).

Three Session 220 deploy outages (39c31ade, 94339410, eea92d6c) traced directly to diff-scoped Gate B reviews missing things not in the diff. Class rule: diff-only review = automatic BLOCK pending sweep verification.

## Success Criteria (Current Production)

- All builds cryptographically signed (NixOS `require-sigs = true`).
- Evidence bundles Ed25519-signed per-appliance + OTS-anchored (~245K bundles).
- Multi-source time sync with anomaly detection + OTS Bitcoin anchoring.
- Unified hash chain across drift + remediation + privileged events.
- SBOM for every deployment.
- 60+ substrate invariants asserting chain progress + gap detection every 60s.
- Privileged-Access Chain of Custody enforced at CLI + API + DB layers (mig 175 + 305).
- Deterministic auditor kit (byte-identical re-downloads, `kit_version='2.1'`).
- Vault Transit live in shadow mode; hot-cutover pending.
