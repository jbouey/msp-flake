# Vault-host Prometheus + Alertmanager

Observability tier for the OsirisCare fleet. Runs on the Vault host
(Hetzner ARM64, 10.100.0.3) alongside HashiCorp Vault — **NOT** on
the customer-production VPS.

## Why here

- **Separate security/infra tier.** Vault host is already a
  restricted-access node; Prom belongs in the same tier.
- **Zero impact on paying-customer performance.** No TSDB disk, no
  scraper CPU, no extra containers on the mcp-server VPS.
- **WG-only bind.** 9090 (Prom) and 9093 (AM) are only reachable from
  the 10.100.0.0/24 WireGuard mesh — never public.
- **Scrapes over public TLS + bearer.** No VPS firewall change; the
  `/metrics` endpoint already serves through Caddy and is protected
  by `PROMETHEUS_SCRAPE_TOKEN` (Session 209).

## Layout

    /opt/prometheus/
      bin/{prometheus,promtool}     # binaries (owned by root, 0755)
      etc/prometheus.yml            # config
      rules/alert_rules.yml         # pulled from repo mcp-server/prometheus/
      secrets/scrape_token          # 0400, owned by prometheus user
      data/                         # TSDB, owned by prometheus user

    /opt/alertmanager/
      bin/{alertmanager,amtool}
      etc/alertmanager.yml
      secrets/webhook_token         # 0400, owned by alertmanager user
      data/

    /etc/systemd/system/
      prometheus.service            # hardened (ProtectSystem=strict etc.)
      alertmanager.service          # hardened (ProtectSystem=strict etc.)

## Install

One-shot from a laptop-side clone of the repo:

    # 1. Generate tokens (32 bytes each, URL-safe).
    PROMETHEUS_SCRAPE_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    ALERTMANAGER_WEBHOOK_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')

    # 2. Push both tokens into the VPS .env (mcp-server side).
    ssh root@178.156.162.116 "
      grep -q '^PROMETHEUS_SCRAPE_TOKEN=' /opt/mcp-server/.env \
        && sed -i 's|^PROMETHEUS_SCRAPE_TOKEN=.*|PROMETHEUS_SCRAPE_TOKEN=${PROMETHEUS_SCRAPE_TOKEN}|' /opt/mcp-server/.env \
        || echo 'PROMETHEUS_SCRAPE_TOKEN=${PROMETHEUS_SCRAPE_TOKEN}' >> /opt/mcp-server/.env
      grep -q '^ALERTMANAGER_WEBHOOK_TOKEN=' /opt/mcp-server/.env \
        && sed -i 's|^ALERTMANAGER_WEBHOOK_TOKEN=.*|ALERTMANAGER_WEBHOOK_TOKEN=${ALERTMANAGER_WEBHOOK_TOKEN}|' /opt/mcp-server/.env \
        || echo 'ALERTMANAGER_WEBHOOK_TOKEN=${ALERTMANAGER_WEBHOOK_TOKEN}' >> /opt/mcp-server/.env
      grep -q '^ALERTMANAGER_RECIPIENTS=' /opt/mcp-server/.env \
        || echo 'ALERTMANAGER_RECIPIENTS=jbouey@osiriscare.net' >> /opt/mcp-server/.env
      cd /opt/mcp-server && docker compose restart mcp-server
    "

    # 3. Ship the repo artifacts + alert rules to the Vault host.
    rsync -avz --exclude='.git' deploy/vault-prometheus/ osiris@89.167.76.203:/tmp/vault-prometheus/
    rsync -avz mcp-server/prometheus/alert_rules.yml osiris@89.167.76.203:/tmp/alert_rules.yml

    # 4. Run install.
    ssh osiris@89.167.76.203 "
      cd /tmp/vault-prometheus && \
      sudo PROMETHEUS_SCRAPE_TOKEN='${PROMETHEUS_SCRAPE_TOKEN}' \
           ALERTMANAGER_WEBHOOK_TOKEN='${ALERTMANAGER_WEBHOOK_TOKEN}' \
           ALERT_RULES_PATH=/tmp/alert_rules.yml \
           bash install.sh
    "

## Smoke test

From the VPS (already on WG 10.100.0.1):

    curl -fsS http://10.100.0.3:9090/-/healthy         # → Prometheus Server is Healthy.
    curl -fsS http://10.100.0.3:9093/-/healthy         # → OK
    curl -fsS http://10.100.0.3:9090/api/v1/targets | jq '.data.activeTargets[] | {job,health}'
    #  {"job":"mcp-server","health":"up"}
    #  {"job":"prometheus","health":"up"}

Fire a synthetic alert to exercise the email path:

    # Use amtool on the Vault host.
    sudo -u alertmanager /opt/alertmanager/bin/amtool --alertmanager.url=http://10.100.0.3:9093 \
        alert add 'SmokeTest' severity='sev1' \
            summary='Synthetic smoke test — safe to ignore' \
            --end=$(date -u -d '+5 minutes' +%Y-%m-%dT%H:%M:%SZ)
    # Expect an email at jbouey@osiriscare.net within ~30s.

## Upgrading

Bump `PROM_VERSION` or `AM_VERSION` at the top of `install.sh`
and re-run — the script replaces only the binaries; data/ and
secrets/ are preserved.

## Rotating tokens

Generate new values, push to both sides (VPS .env + Vault host
secrets files), then:

    sudo systemctl restart prometheus alertmanager   # on Vault host
    sudo docker compose restart mcp-server           # on VPS

Prom reads the scrape token file per-scrape (no restart needed in
newer Prom versions; restart regardless for the AM side).

## Security notes

- The Vault host already hosts an Ed25519 signing key. Prom and AM
  have **zero** access to `/opt/vault/*`; `ReadOnlyPaths` in the
  unit files enumerate what they can see, and the `prometheus`/
  `alertmanager` system users cannot read Vault's data dir (owned
  by `vault`).
- `MemoryDenyWriteExecute=true` + `RestrictNamespaces=true` on both
  units block JIT-and-escape attacks.
- Tokens are mode 0400, owned by their service user, never world- or
  group-readable. They never appear in `ps`, logs, or config dumps.
- If Prom is ever compromised, the blast radius is bounded to the
  scrape-token's narrow capability: reading `/metrics`. The token
  does not grant any mutation ability on mcp-server.
