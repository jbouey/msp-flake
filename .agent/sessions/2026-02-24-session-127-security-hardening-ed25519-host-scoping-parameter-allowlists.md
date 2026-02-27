# Session 127 - Security Hardening Ed25519 Host Scoping Parameter Allowlists

**Date:** 2026-02-24
**Started:** 14:23
**Previous Session:** 126
**Commit:** 0656088

---

## Goals

- [x] P1: Add host scoping (target_appliance_id) to order signature envelope
- [x] P1: Allowlist order parameters for dangerous order types (nixos_rebuild, update_agent, update_iso)
- [x] P1: Constrain sync_promoted_rule with YAML schema validation

---

## Progress

### Completed

**Host Scoping** — `order_signing.py` adds `target_appliance_id` to signed payload. All admin/healing order creation paths (sites.py, partners.py, main.py, fleet_updates.py rollouts) now include the target appliance. Go `processor.go` verifies host scope after Ed25519 check. Fleet orders exempt.

**Parameter Allowlists** — `nixos_rebuild` validates flake_ref against `github:jbouey/msp-flake#<output>` pattern. `update_agent`/`update_iso` validate URLs are HTTPS from allowlisted domains (github.com, objects.githubusercontent.com, VPS). Added `validateFlakeRef()` and `validateDownloadURL()` helpers.

**Schema Validation** — `sync_promoted_rule` validates YAML against L1 Rule schema: parses into struct, checks action in 10-action allowlist, verifies rule ID match, requires conditions with field+operator, enforces 8KB size limit.

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `appliance/internal/crypto/verify.go` | New: Ed25519 verification package (P0, prev session) |
| `appliance/internal/crypto/verify_test.go` | New: 4 crypto tests |
| `appliance/internal/checkin/db.go` | Fetch signature fields, server_public_key |
| `appliance/internal/checkin/models.go` | Added Nonce/Signature/SignedPayload/ServerPublicKey |
| `appliance/internal/daemon/daemon.go` | Pass server pubkey + appliance ID to processor/L1 |
| `appliance/internal/daemon/phonehome.go` | Added ServerPublicKey to response |
| `appliance/internal/healing/l1_engine.go` | Verify signed L1 rules bundles |
| `appliance/internal/orders/processor.go` | Host scoping, param allowlists, schema validation |
| `appliance/internal/orders/processor_test.go` | 15 new tests (37 total) |
| `backend/migrations/054_order_signatures.sql` | New: nonce/signature/signed_payload columns |
| `backend/order_signing.py` | New: shared signing helper with target_appliance_id |
| `backend/fleet_updates.py` | Host-scoped rollout orders |
| `backend/partners.py` | Host-scoped discovery orders |
| `backend/sites.py` | Host-scoped admin orders (3 paths) |
| `mcp-server/main.py` | Host-scoped healing orders, signed L1 rules |

---

## Next Session

1. Apply migration 054 on VPS (order signature columns)
2. Deploy and verify Ed25519 signatures working end-to-end on physical appliance
3. P2 security items: rate limiting on order execution, audit log for rejected orders
4. Transition unsigned order warnings to hard rejections after rollout confirmed
