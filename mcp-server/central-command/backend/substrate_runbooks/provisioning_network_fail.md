# provisioning_network_fail

**Severity:** sev2
**Display name:** Install stuck — installed system can't reach origin

## What this means (plain English)

A freshly-installed appliance booted from its internal disk, started the
msp-auto-provision service, and has been retrying for at least 3 minutes —
but the installed system has not made a single successful outbound HTTPS
connection to the Central Command origin (`api.osiriscare.net` →
`178.156.162.116:443`). The 4-stage network gate (DNS → TCP/443 → TLS →
HTTP `/health`) is failing at one or more stages. The installer ITSELF
talked to the origin fine — it's the installed system that can't.

This is the exact class of failure that bit `84:3A:5B:1D:0F:E5` on
2026-04-23: Cloudflare IP rotation combined with a firewall that pinned
IPs at boot. The installer phoned home, the appliance installed cleanly,
then the installed system's firewall pinned a dead Cloudflare IP and went
silently deaf. v40 FIX-9 + FIX-10 fix this on the ISO side; this invariant
is the outcome-layer signal that catches any future variant of the same
class (DNS filter, egress ACL, TLS-intercept proxy, upstream outage).

## Root cause categories

- **DNS filter at site egress** — Pi-hole, Umbrella, Fortinet, Sophos, or
  Barracuda is blocking `api.osiriscare.net`. Stage 1 (DNS) fails with
  NXDOMAIN or NOERROR+no-A. Read `install_gate_status.json` on the beacon
  at `http://<appliance-ip>:8443/` — `dns.ok=false` confirms.
- **Egress ACL / port block** — DNS resolves but `tcp_443.ok=false`.
  Firewall is allowing DNS but not outbound HTTPS to the origin IP.
  Common on heavily-locked-down dental clinic networks.
- **TLS-intercept proxy** — `tls.ok=false` while TCP connected.
  Corporate proxy is MITMing HTTPS and the cert doesn't validate.
  Rare in healthcare SMB but possible with a Fortinet SSL-inspect policy.
- **Origin health** — `health.ok=false` but earlier stages green means
  Central Command's `/health` is down. Cross-check VPS status; this is
  NOT a customer issue.
- **Appliance firewall regression** — If all four stages are green on the
  LAN beacon but the invariant still fires, `MSP_EGRESS` may be dropping
  the daemon's outbound. Check journald `MSP_EGRESS_DROP` lines.

## Immediate action

- LAN-scan the appliance: `curl -s http://<appliance-ip>:8443/` returns
  a JSON blob with `state` and the full `install_gate_status` object.
  The `last_stage_failed` field names exactly which of DNS / TCP / TLS /
  HEALTH is broken.
- If `last_stage_failed=dns`:

  ```
  Whitelist api.osiriscare.net on the site's DNS filter. MAC-specific
  whitelist rules are easy to miss — ensure the appliance MAC is covered.
  ```

- If `last_stage_failed=tcp_443`:

  ```
  Add egress rule: allow TCP/443 from the appliance IP to
  178.156.162.116. Some firewalls require BOTH an outbound allow AND a
  stateful inbound-established allow.
  ```

- If `last_stage_failed=tls`:

  ```
  Exempt api.osiriscare.net from SSL inspection. TLS-intercept proxies
  break cert chain of trust for certificate-pinned clients. If exemption
  is not possible, the appliance is not deployable at this site.
  ```

- If `last_stage_failed=health`: contact OsirisCare on-call — origin
  health endpoint is down; this is not a customer fix.

## Verification

- Panel: invariant row should clear on next 60s tick after a successful
  net-ready POST (the installed system calls `run_network_gate_check`
  every 5 min during Phase 3).
- Live: `curl -sk -H "X-Install-Token: ${INSTALL_TOKEN}" \
  https://api.osiriscare.net/api/install/report/net-ready` returning
  `{"status":"recorded","dedup":false}` confirms the endpoint is alive.
- CLI: `SELECT mac_address, first_outbound_success_at FROM install_sessions
  WHERE mac_address = '<MAC>';` — non-NULL means the gate passed at least once.
- Beacon state: `curl http://<appliance-ip>:8443/ | jq '.state'` should
  transition from `network_gate_failing` → `online` once the underlying
  issue is resolved.

## Escalation

Do NOT auto-remediate this one. There are no privileged actions that can
cross the customer's firewall on behalf of the appliance — any fix must
happen on the customer's network gear. If the invariant stays open > 30
minutes while the customer confirms they've whitelisted `api.osiriscare.net`,
escalate to the on-call CCIE — it's either an asymmetric-routing issue
(whitelist not taking effect) or a second firewall hop the customer wasn't
aware of. If the appliance's install_sessions shows `checkin_count > 20`
with `first_outbound_success_at IS NULL`, consider full reflash with the
latest ISO — something at the installed-system layer is wedged (generic
state corruption on MSP-DATA, not a network problem).

## Related runbooks

- `provisioning_stalled` — sibling invariant fires AFTER 15 min; this one
  fires EARLIER (within 3 installer checkins, ~90s) so the customer
  doesn't wait 20 minutes for the first signal.
- `installed_but_silent` — fires latest; the installer has stopped
  retrying entirely.
- `install_loop` — BIOS-layer problem; install never completes.
- `fleet_order_url_resolvable` — DNS failure at the VPS side (our
  fleet_cli orders point at a dead hostname). Inverse scope: this
  invariant fires for customer egress failing, that one fires for our
  issuance failing.

## Change log

- 2026-04-23 — v40 FIX-14 (round-table, Session 209 continued) — created
  alongside `install_sessions.first_outbound_success_at` column and
  `/api/install/report/net-ready` endpoint. Paired with ISO-side FIX-11
  (4-stage gate) as the outcome-layer signal.
