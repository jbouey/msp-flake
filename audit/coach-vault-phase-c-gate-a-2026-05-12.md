# Gate A Verdict — Vault Transit Phase C Cutover
Reviewer: Gate A fork (Opus 4.7, fresh context, 4-lens adversarial)
Date: 2026-05-12
Scope: `SIGNING_BACKEND=shadow` → `SIGNING_BACKEND=vault` on `/opt/mcp-server/.env`

## Verdict: **BLOCK**

Four of the four open decisions resolve to "not ready". Multiple P0s in code path. Three of the four lenses raise P0. The Phase C plan as documented references mechanisms that do not exist (rotate_server_pubkey order, `INV-SIGNING-BACKEND-VAULT`, signing_method write-path). Shipping today would either (a) succeed silently and leave the chain unobservable, or (b) succeed silently and lock the fleet out of trust on any future Vault key rotation. Re-design + reconvene Gate A.

---

## Open-decision recommendations

**Decision A (key identity): NEW KEY (per plan recommendation).**
Probe verification — the parent could not run `b._shadow.public_key()` because of Vault lazy-init under docker exec, BUT the static evidence is conclusive: `signing_backend.VaultSigningBackend.public_key()` resolves to the *latest* version of `osiriscare-signing` in Vault, and that key was minted fresh (memory file pins `fsGvdHYB8mK2QFj6ZsDlB16YWSOPJUH8wdpT4aeHAJ4=` as the Vault pubkey). The disk pubkey is whatever PyNaCl produces from `/app/secrets/signing.key`. They are different by construction (different keypair generation events). Importing the disk key into Vault as a same-pubkey transit key is **not supported by Vault Transit's API** without restoring a backup — and we have no backup of the disk key in a Transit-importable form. NEW KEY is the only reachable option AND the correct one (Maya: "key has lived on disk" is the entire blast radius we're trying to close).

**Decision B (rotation ceremony): WAIT — daemon support not implemented.**
There is NO `rotate_server_pubkey` order type registered in `appliance/internal/orders/processor.go`. Grep across the entire `appliance/` tree returns zero hits. The trust transition works through a different mechanism that the plan misdescribes: the daemon picks up the trust set from `resp.ServerPublicKeys[]` on every `/api/appliances/checkin` (see `daemon.go:1124-1134` calling `orderProc.SetPublicKeys(current, previous[])`). The backend already supplies `public_keys_all()` from the ShadowSigningBackend which returns BOTH file + Vault pubkeys deduped (signing_backend.py:334-353). This means rotation is **passive on checkin** rather than active via an order. That's actually safer (no signed order to fail-verify under the very rotation it's trying to do), but the plan's Phase C step 5 "Run a `rotate_server_pubkey` fleet order" is wrong. Coach P0.

**Decision C (Vault availability): Mitigations insufficient.**
No circuit breaker on the Vault path (grep `gobreaker`/`circuit.*breaker` returns L2 + WinRM but no Vault). The `VaultSigningBackend` has a 5-second httpx timeout and re-raises on failure. The plan acknowledges this but defers the "local signing-fallback policy" as a POLICY decision — it must be decided BEFORE cutover, not after. Steve P0.

**Decision D (startup invariant): NOT WRITTEN.**
Grep `INV-SIGNING-BACKEND-VAULT` returns zero hits. `startup_invariants.py` has `INV-SIGNING-KEY` (file-only). The DB has no `known_good_vault_key_version` table. The plan says "Add a startup invariant" but no migration, no code, no table. Coach P0.

---

## Findings by lens

### Steve (correctness) — 3 P0, 4 P1

**P0-STEVE-1: 30-second restart window has no documented behavior for in-flight orders.** The container restart drops all uncommitted signing operations. Any HTTP handler holding a `get_signing_backend()` reference mid-sign loses it. Worse: `_BACKEND_SINGLETON` rebuilds on first call, and the first call to the rebuilt VaultSigningBackend hits AppRole login (network round-trip over WG), so the first sign after restart is 200-500ms slower than steady-state. Plan does not document this. *Pre-fix:* Drain in-flight HTTP requests via `docker compose stop -t 30 mcp-server` before `up -d`, or document the SLO impact. Verify no fleet_order INSERT is mid-sign (admin pause flag would help).

**P0-STEVE-2: WG MTU + cross-DC latency unverified.** mcp-server is on a Hetzner US region (per memory file naming convention) and Vault is on `ubuntu-4gb-hel1-1` which is **Helsinki**. RTT not measured. WireGuard MTU 1420 over an MTU-1500 underlay is fine, but Helsinki↔US is 100-150ms RTT. Every Vault sign is one HTTPS round trip (or two on first call: AppRole login + sign). This adds 100-300ms to every signed-order endpoint. Plan claims `httpx.Client(timeout=5.0)` is enough — true for liveness, but the response-time SLO of synchronous endpoints (privileged_access POST = sign + insert in same request) may regress 3-10x. *Pre-fix:* `ping -c 10 10.100.0.3` from mcp-server, measure WG RTT, document expected p95 sign latency.

**P0-STEVE-3: No safe-rollback procedure documented.** If cutover fails (Vault unreachable, divergent pubkey, etc.), the rollback is: edit `.env`, flip `SIGNING_BACKEND=shadow`, restart. But during the broken-state window, every privileged_access / runbook_consent / partner attestation / quarterly summary / client letter sign call **raises SigningBackendError**, which the call sites generally let propagate to HTTP 500. The customer-facing impact is: every customer-facing artifact-issuance endpoint 500s until rollback completes. Estimated window: 90 seconds restart + diagnosis time. *Pre-fix:* Write the rollback runbook with named `cd /opt/mcp-server && sed -i s/vault/shadow/ .env && docker compose restart mcp-server` and pin the assertion that this is faster than 5 minutes. Better: ship a `SIGNING_BACKEND_FALLBACK=file` env var that the VaultSigningBackend catches in its `.sign()` exception path and degrades-gracefully with an audit-log entry (Carol's "policy decision" from the plan).

**P1-STEVE-4:** The 0.4.9 daemon at north-valley-branch-2 — verify it supports the `server_public_keys` (plural) array in checkin response. The daemon.go:1125 code uses `len(resp.ServerPublicKeys) > 1`, which is a feature that may have been added between 0.4.9 and 0.4.13. If 0.4.9 only reads `resp.ServerPublicKey` (singular), it will trust whichever one we send and orders signed by the other key will fail-verify on that one appliance. **Action: upgrade 0.4.9 → 0.4.13 BEFORE the env flip.**

**P1-STEVE-5:** The plan's Phase C step 5 mentions issuing `rotate_server_pubkey` orders — that order type doesn't exist. Either implement it (substantial work, requires new handler + signed-payload schema + daemon receipt + ack-back) or rewrite Phase C step 5 to say "the trust set transitions passively on next checkin (~10 min worst case across the fleet); verify via `SELECT site_id, agent_pubkey_seen FROM site_appliances` after a 15-minute soak."

**P1-STEVE-6:** Shadow-mode primary-failure semantics: today `SIGNING_BACKEND_PRIMARY=file` means file-primary, vault-shadow. After flip, `SIGNING_BACKEND=vault` removes the shadow entirely. We lose the divergence-counter early warning. *Action:* run `SIGNING_BACKEND=shadow SIGNING_BACKEND_PRIMARY=vault` for 24-48h as Phase C-1 BEFORE Phase C-2 (`SIGNING_BACKEND=vault`). Reverse-shadow proves Vault-primary works while file is still verifying.

**P1-STEVE-7:** Lazy-init under docker exec: the probe hung. This will also hang on the first real signing request after restart. The 5-sec timeout means it'll bubble up as a SigningBackendError on the first customer-touching sign in production. *Action:* warm the singleton in `lifespan` startup so the first request doesn't pay the cold-start tax.

### Maya (HIPAA / §164.528) — 1 P0, 2 P1

**P0-MAYA-1: No SECURITY_ADVISORY drafted for Phase C; plan defers to Phase D — wrong order.** The disk-key blast radius existed for ~12 months. Customers have a right (under their BAA + HIPAA's §164.404 breach-notice analysis-not-breach standard) to know that the signing material moved off-disk. Disclosure-after-mitigation is the correct sequence. *Pre-fix:* Draft `SECURITY_ADVISORY_2026-05-12_SIGNING_KEY_ISOLATION.md` today; publish at Phase C cut, NOT 30 days later. Not a §164.408 breach (no PHI access), but customers signed for "evidence-grade" signing — saying nothing for 30 days after a substantial posture change attacks the trust position.

**P1-MAYA-2:** Auditor-kit embeds the public key (`pubkeys.json`) but NOT the `signing_method` per bundle. After cutover, both file-signed historical bundles and vault-signed new bundles will appear identical in the kit. This is arguably FINE for auditors (the signature verifies; provenance is implicit in the chain), but the auditor-kit determinism contract pins `kit_version=2.1`. Adding a per-entry `signing_method` field would bump to `2.2` and require all 4 surfaces to update in lockstep. Recommend NOT changing kit format pre-cutover; carry as TaskCreate for Phase E.

**P1-MAYA-3:** The `kit_version` bump (if any) is a customer-facing artifact change. The plan does not analyze this. Even if we don't bump the version, the disk-vs-vault distinction is an architectural disclosure the auditor would expect in `README.md` of the kit. Carry as TaskCreate.

### Carol (DBA) — 2 P0, 2 P1

**P0-CAROL-1: `signing_method` column is never written.** Grep `signing_method.*=\|signing_method.*INSERT\|UPDATE fleet_orders.*signing_method` returns ZERO hits in production code (migrations + test fixtures only). After cutover, every row will still say `'file'` because migration 177 defines `NOT NULL DEFAULT 'file'`. We will have ZERO database evidence that we cut over. *Pre-fix:* Implement the write path: every call site that inserts a fleet_order MUST set `signing_method = backend.name` (file/vault), and the SignResult.backend_name already carries this. Add to fleet_cli.py sign_admin_order + sign_fleet_order. Carry CI gate to verify (a new test).

**P0-CAROL-2: No `known_good_vault_key_version` table / row for the proposed INV-SIGNING-BACKEND-VAULT invariant.** Cannot ship the invariant without the DB substrate. Need a migration adding:
```sql
CREATE TABLE IF NOT EXISTS vault_signing_key_known_good (
  id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  key_name TEXT NOT NULL,
  pubkey_b64 TEXT NOT NULL,
  key_version INT NOT NULL,
  pinned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  pinned_by TEXT NOT NULL
);
```
And a startup_invariants.py block reading Vault's transit key + comparing to the pinned row. Without this, an attacker who compromises Vault root can `vault write -force transit/keys/osiriscare-signing/config min_decryption_version=2` and silently swap the signing key — and we have no detection.

**P1-CAROL-3:** Index `idx_fleet_orders_signing_method` is `WHERE signing_method <> 'file'` — partial index assumes 'file' is the common case. After cutover the common case is 'vault'. The partial WHERE clause flips, and the index becomes effectively a full table index. Low-volume (10 orders/day), so the storage cost is trivial, but the assumption is wrong. *Carry as TaskCreate:* drop and recreate the index `WHERE signing_method <> 'vault'` AFTER 30-day Phase D retirement of file-mode.

**P1-CAROL-4:** PgBouncer keeps connections warm to mcp-server; signing happens IN those request connections (not in the DB). No DB-side impact from the signing path. Verified.

### Coach (lockstep / banned shapes / consistency) — 3 P0, 2 P1

**P0-COACH-1: `INV-SIGNING-BACKEND-VAULT` is referenced in the plan but does not exist in code.** Banned shape: "design references an invariant that the implementation does not have." Per Session 220 lock-in, Gate B will catch this if we ship — Gate A should catch it now. The plan is unimplementable as written. *Pre-fix:* Write the invariant, the DB table, the migration, the test. Verify the invariant fails-startup correctly via a CI test that flips the pinned pubkey to a wrong value and asserts container start fails.

**P0-COACH-2: Plan's Phase C step 5 mechanism does not exist.** `rotate_server_pubkey` is not an order type. Per Session 219 #121 directive ("Gate A claims of project convention need ≥2 producers + ≥1 consumer grep"), this is a banned-shape: a Gate A approval that would have referenced a vapor-mechanism. The actual mechanism (passive trust transition via checkin response) IS implemented and works — the plan needs to be rewritten to match reality.

**P0-COACH-3: No substrate invariant `signing_backend_drifted_from_vault` proposed.** Once Phase C ships, the substrate engine should assert `SIGNING_BACKEND=='vault'` continuously — environment variable accessible via `os.getenv()` inside assertions.py. Detects a partial-rollback or an operator forgetting they cleared the env in a redeploy. Severity sev2 (chain attestable but key blast radius wider during the drift). *Carry as P0:* add to assertions.py before cutover so Day 1 has the safety net.

**P1-COACH-4:** Lockstep check: the three privileged-chain lists do NOT need an entry for signing-method (privileged-chain is attestation_bundle_id-keyed, not signing-key-keyed). Verified — no churn there. Note for the record.

**P1-COACH-5:** Substrate operator-alert chain-gap pattern (Session 216): if a privileged-access bundle signing fails at the new Vault step, the existing operator-alert hook should fire `[ATTESTATION-MISSING]` — but the failure mode is broader (whole sign() raises, not just an attestation step). *Carry as TaskCreate:* wrap every `get_signing_backend().sign()` call site in try/except that fires `P0-CHAIN-GAP` operator alert AND queues the bundle for retry. Today the failure is a bare HTTP 500.

---

## Required pre-execution closures (P0) — 8 items

1. **Implement `INV-SIGNING-BACKEND-VAULT`** + migration for `vault_signing_key_known_good` table + populate with the current Vault pubkey + version.
2. **Implement `signing_method` write path** in every fleet_order INSERT call site (fleet_cli, runbook_consent, privileged_access_attestation, partner_portfolio_attestation, client_attestation_letter, client_quarterly_summary, partner_ba_compliance, shared.py:sign_payload). CI gate to enforce.
3. **Add substrate invariant** `signing_backend_drifted_from_vault` (sev2) in assertions.py.
4. **Upgrade north-valley-branch-2 0.4.9 daemon → 0.4.13** (or verify 0.4.9 reads `server_public_keys` plural array).
5. **Rewrite Phase C step 5** in vault-transit-migration.md to reflect actual mechanism (passive trust transition via checkin response, NOT `rotate_server_pubkey` order).
6. **Run Phase C-1 reverse-shadow** (`SIGNING_BACKEND=shadow SIGNING_BACKEND_PRIMARY=vault`) for 24-48h before Phase C-2 (vault-only).
7. **Document rollback runbook** with named one-liner + tested in a non-prod check + named owner.
8. **Draft + publish SECURITY_ADVISORY_2026-05-12_SIGNING_KEY_ISOLATION.md** AT cutover, not 30 days later.

## Carry-as-followup (P1) — 6 items

- P1-STEVE-4 verify (likely closes via daemon upgrade in P0 #4)
- P1-STEVE-7 warm singleton in lifespan
- P1-MAYA-2 + P1-MAYA-3 auditor-kit signing_method exposure (Phase E)
- P1-CAROL-3 partial-index flip post Phase D
- P1-COACH-5 sign-failure operator-alert + retry queue wrapper

## Recommended execution sequence

1. **Sprint 0 (today):** Land P0 #1, #2, #3, #5, #6 plan-edit, #7 rollback runbook in one commit batch. Gate B required.
2. **Sprint 1 (next 24h):** Upgrade .9 daemon (P0 #4) — fleet order, verify via checkin telemetry that 3/3 appliances report 0.4.13.
3. **Sprint 2 (Phase C-1):** Flip env to `SIGNING_BACKEND=shadow SIGNING_BACKEND_PRIMARY=vault`, restart, soak 24-48h, monitor `osiriscare_signing_backend_divergence_total`. Gate B before advancing.
4. **Sprint 3 (Phase C-2 — the cutover):** Draft + publish SECURITY_ADVISORY (P0 #8) in same commit as env-flip to `SIGNING_BACKEND=vault`. Verify `INV-SIGNING-BACKEND-VAULT` PASSES on startup. Verify substrate invariant `signing_backend_drifted_from_vault` reports OK. Verify `SELECT DISTINCT signing_method FROM fleet_orders WHERE created_at > NOW()-INTERVAL '5min'` returns `{'vault'}`. Gate B required.
5. **Sprint 4 (30 days):** Phase D — retire disk key + flip partial index + ship `__init__` removal of FileSigningBackend code path. Separate Gate A.

**Re-fork Gate A** after Sprint 0 commits land. Do not advance to Sprint 2 (env-flip) without a re-fork pass — the implementation drift between plan and code is wide enough that this Gate A's approval would not transfer.
