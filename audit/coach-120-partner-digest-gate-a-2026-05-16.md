# Gate A — #120 Partner-Aggregate Fleet Digest (Multi-Device P2-1)

**Date:** 2026-05-16 · **Fork:** fresh-context (general-purpose) · **Author:** sub-agent
**Reviewing:** design intent only — no code shipped yet
**Class:** Counsel Rule 4 ("no silent orphan coverage") + Rule 7 ("no unauth context") + Rule 2 ("no raw PHI")

## VERDICT: APPROVE-WITH-FIXES

Design intent is sound; substrate detection already exists (`offline_appliance_over_1h` sev2 at 1h + `appliance_offline_extended` sev2 at 24h+ in `assertions.py:142,4720`). Gap is genuinely **partner-aggregate visibility**, NOT detection. However the brief proposes a parallel system; **existing `send_partner_weekly_digest()` (email_alerts.py:1020, fired by `partner_weekly_digest_loop` in `background_tasks.py:2089`) is the canonical surface — extend it, do NOT create a sibling.** 4 P0s + 3 P1s before merge.

## RECOMMENDED DESIGN (single-page)

**Cadence:** TWO-track on the EXISTING `send_partner_weekly_digest` loop:
- **Floor** — weekly digest already runs (Friday). ADD a `fleet_health` block: `{offline_24h_count, offline_7d_count, chronic_unack_fleet_orders, baa_expiring_30d_count}`. Zero new cron.
- **Spike** — new `send_partner_fleet_spike_alert()` fires from **substrate invariant escalation hook** (Session 216 chain-gap pattern): when `offline_appliance_over_1h` count for ONE partner crosses **`max(3, ceil(0.05 * fleet_size))` in a 6h window**, emit ONE per-partner alert. **Anti-noise gate**: max 1 spike/partner/24h, suppressed if the most recent weekly digest already covered the offline appliances.

**Delivery:** email to `partners.contact_email` (already used by existing digest — same audience), Counsel Rule 7 opaque-mode:
- Subject: `[OsirisCare] Fleet health alert · <count> appliances need attention` (NO partner brand, NO clinic names, NO IPs)
- Body: aggregate counters + deep-link to `/partner/fleet?filter=offline_24h` behind auth. NO per-appliance MAC/IP/hostname/client-org names in SMTP channel.

**Aggregation SQL (skeleton):**
```sql
-- Per-partner fleet-health aggregate. RLS-by-partner_id at app layer.
SELECT
  p.id AS partner_id,
  p.contact_email,
  COUNT(*) FILTER (WHERE sa.deleted_at IS NULL AND last_hb.ts < now() - interval '24 hours') AS offline_24h,
  COUNT(*) FILTER (WHERE sa.deleted_at IS NULL AND last_hb.ts < now() - interval '7 days') AS offline_7d,
  COUNT(DISTINCT s.client_org_id) FILTER (WHERE co.baa_expires_at BETWEEN now() AND now() + interval '30 days') AS baa_expiring_30d,
  COUNT(*) FILTER (WHERE fo.status = 'pending' AND fo.created_at < now() - interval '6 hours') AS chronic_unack_orders
FROM partners p
JOIN sites s ON s.partner_id = p.id AND s.status != 'inactive'
LEFT JOIN site_appliances sa ON sa.site_id = s.site_id AND sa.deleted_at IS NULL
LEFT JOIN LATERAL (
  SELECT MAX(received_at) AS ts FROM appliance_heartbeats
  WHERE appliance_id = sa.appliance_id
) last_hb ON true
LEFT JOIN client_orgs co ON co.id = s.client_org_id
LEFT JOIN fleet_orders fo ON fo.target_appliance_id = sa.appliance_id
WHERE p.id = $1
GROUP BY p.id, p.contact_email;
```
**Pattern compliance:** `sa.deleted_at IS NULL` on JOIN line + `s.status != 'inactive'` (Session 218 RT33 P1 rule). Direct base-table query (NO MV — Session 218 RT33 P2 Steve veto).

**Email template skeleton (`templates/partner_fleet_spike_alert/`):** mirrors `partner_weekly_digest/` shape — Jinja2 (NOT in-source `.format()` per Session 218 lock-in). 2 files: `subject.j2`, `body.html.j2`. `StrictUndefined`. NO clinic names, NO MACs, NO IPs.

## PER-LENS VERDICT

- **Steve (architect):** APPROVE — extend, don't fork. Reuse `send_partner_weekly_digest` infra + `bg_heartbeat` cadence registration. Spike alerts go through `_send_smtp_with_retry` (CLAUDE.md canonical rule).
- **Maya (HIPAA counsel):** APPROVE-WITH-FIX — opaque-mode mandatory; subject string literal; NO `{client_name}` in body. Add `tests/test_email_opacity_harmonized.py` gate entry. NOT a §164.528 disclosure — disclaimer required.
- **Carol (security):** APPROVE-WITH-FIX — recipient MUST be `partners.contact_email` (NOT user-supplied); rate-limit per partner_id (Redis or in-process); aggregate counts ONLY, never per-row leak in email.
- **Coach (consistency):** APPROVE-WITH-FIX — register `partner_fleet_spike_alert` in `bg_heartbeat` if a new loop is added; otherwise extend existing weekly loop. Don't create a 3rd partner-email pathway.
- **Auditor (evidence):** APPROVE — audit row to `admin_audit_log` (`partner_fleet_spike_alert_sent`, `partner_id`, `aggregate_counts`, `recipient_email_redacted`). Standard hash-chain not required (advisory notification, not privileged action).
- **PM:** APPROVE — closes Counsel Rule 4 multi-device gap; ships in <1 week if scoped to extension.
- **Counsel (Rule filter):** APPROVE-WITH-FIX — Rule 4 closed; Rule 7 enforced via opaque-mode; Rule 2 enforced via aggregate-counts-only (no clinic/MAC/IP/hostname in SMTP). Rule 6 (BAA): trigger MUST suppress for partners whose BAA is expired (`baa_enforcement_ok()` check) — otherwise alerting an unauthorized counterparty.

## P0 (must close before merge)

1. **Opaque-mode parity** — subject string literal, body has zero clinic/MAC/IP/hostname/client-org-name. Add to `tests/test_email_opacity_harmonized.py` allowlist. (Session 218 lock-in.)
2. **BAA-expired suppression** — call `baa_enforcement_ok(partner_id)` before SMTP send; expired partners skip + log `partner_fleet_spike_alert_suppressed_baa_expired`. (Counsel Rule 6 + Session 220 #52 enforcement triad.)
3. **Recipient hard-pin** — `to_email` MUST come from `partners.contact_email` server-side query, NOT request-supplied. Pin via test.
4. **Jinja2 templates (not `.format()`)** — `StrictUndefined`, boot-smoke renders both templates. (Session 218 round-table lock-in — `.format()` banned for customer-facing artifacts.)

## P1 (in-commit OR named followup task)

1. **Substrate invariant `partner_fleet_spike_alert_not_sent_in_24h_when_threshold_crossed`** (sev2) — catches broken email pipeline when threshold met but no audit row. Self-monitoring per brief Q6.
2. **Rate-limit table or Redis key** — `partner_fleet_spike_alert_last_sent(partner_id, sent_at)` with 24h cap. Followup task acceptable if shipped within sprint.
3. **Frontend deep-link target** — `/partner/fleet?filter=offline_24h` view exists? If not, followup task — link MUST resolve else email is a dead-end.

## P2 (consider, don't block)

- Slack/PagerDuty webhook destination (post-MVP — email-only for v1).
- Per-partner threshold customization (`partners.fleet_spike_threshold_pct`).
- Roll-up of `appliance_offline_extended` (24h sev) into the SAME spike alert vs. separate.

## ANTI-SCOPE (do NOT add)

- Per-appliance detail in email (Counsel Rule 7 + Rule 2 violation).
- New `partner_fleet_health` materialized view (Session 218 RT33 P2 Steve veto — MVs bypass RLS).
- Cron-driven daily digest (existing weekly + spike is sufficient; 3rd cadence = noise).
- Forking `send_partner_weekly_digest` (extend, don't duplicate).
- SMS/voice channels (out of scope for this task).
- Operator-alert escalation if spike-alert send fails (operator already gets `offline_appliance_over_1h` substrate violations directly — double-alerting is noise).

## MIGRATION CLAIM

**Likely none required.** Implementation reuses `partners.contact_email`, `appliance_heartbeats`, `site_appliances`, `fleet_orders`, `client_orgs.baa_expires_at`. Audit row goes to existing `admin_audit_log`.

**If P1#2 implemented as table (not Redis):** claim **mig 326** for `partner_fleet_spike_alert_log(partner_id PK, last_sent_at, aggregate_counts_jsonb)`. Add to `RESERVED_MIGRATIONS.md` + `<!-- mig-claim:326 task:#120 -->` marker in design doc.

## Gate B PREREQ

Per Session 220 lock-in: Gate B MUST run full pre-push sweep (`bash .githooks/full-test-sweep.sh`) and cite pass/fail count. Diff-only review = automatic BLOCK.
