#!/usr/bin/env bash
# Idempotent Prometheus + Alertmanager install for the Vault host.
#
# This box already runs HashiCorp Vault (Transit shadow). Prom + AM
# are the observability tier for the mcp-server fleet and MUST NOT
# touch the Vault dir, keys, or credentials.
#
# Run as root on 10.100.0.3:
#   PROMETHEUS_SCRAPE_TOKEN=... \
#   ALERTMANAGER_WEBHOOK_TOKEN=... \
#   sudo -E bash install.sh
#
# Re-running is safe — all state-setting steps use `install -D` or
# test-before-act. Upgrading to a newer Prom/AM release = bump the
# versions below and re-run; systemd restart picks it up.

set -euo pipefail

PROM_VERSION="${PROM_VERSION:-2.54.1}"
AM_VERSION="${AM_VERSION:-0.27.0}"

PROM_USER="prometheus"
AM_USER="alertmanager"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS="${REPO_DIR}"

if [[ "$EUID" -ne 0 ]]; then
  echo "run as root (sudo -E bash install.sh)" >&2
  exit 1
fi

: "${PROMETHEUS_SCRAPE_TOKEN:?PROMETHEUS_SCRAPE_TOKEN must be set}"
: "${ALERTMANAGER_WEBHOOK_TOKEN:?ALERTMANAGER_WEBHOOK_TOKEN must be set}"

ARCH="$(uname -m)"
case "$ARCH" in
  aarch64|arm64) GOARCH=arm64 ;;
  x86_64|amd64)  GOARCH=amd64 ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 1 ;;
esac

echo "==> users"
id -u "$PROM_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$PROM_USER"
id -u "$AM_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$AM_USER"

echo "==> dirs"
install -d -o root -g root -m 0755 \
  /opt/prometheus/bin /opt/prometheus/etc /opt/prometheus/rules
install -d -o "$PROM_USER" -g "$PROM_USER" -m 0700 \
  /opt/prometheus/data /opt/prometheus/secrets
install -d -o root -g root -m 0755 \
  /opt/alertmanager/bin /opt/alertmanager/etc
install -d -o "$AM_USER" -g "$AM_USER" -m 0700 \
  /opt/alertmanager/data /opt/alertmanager/secrets

echo "==> prometheus binary (${PROM_VERSION}, ${GOARCH})"
PROM_TAR="prometheus-${PROM_VERSION}.linux-${GOARCH}.tar.gz"
if [[ ! -x "/opt/prometheus/bin/prometheus" ]] \
   || ! /opt/prometheus/bin/prometheus --version 2>&1 | grep -q "${PROM_VERSION}"; then
  cd /tmp
  curl -fsSL -O "https://github.com/prometheus/prometheus/releases/download/v${PROM_VERSION}/${PROM_TAR}"
  tar xzf "${PROM_TAR}"
  install -m 0755 -o root -g root \
    "prometheus-${PROM_VERSION}.linux-${GOARCH}/prometheus" /opt/prometheus/bin/prometheus
  install -m 0755 -o root -g root \
    "prometheus-${PROM_VERSION}.linux-${GOARCH}/promtool"   /opt/prometheus/bin/promtool
  rm -rf "/tmp/prometheus-${PROM_VERSION}.linux-${GOARCH}" "/tmp/${PROM_TAR}"
fi

echo "==> alertmanager binary (${AM_VERSION}, ${GOARCH})"
AM_TAR="alertmanager-${AM_VERSION}.linux-${GOARCH}.tar.gz"
if [[ ! -x "/opt/alertmanager/bin/alertmanager" ]] \
   || ! /opt/alertmanager/bin/alertmanager --version 2>&1 | grep -q "${AM_VERSION}"; then
  cd /tmp
  curl -fsSL -O "https://github.com/prometheus/alertmanager/releases/download/v${AM_VERSION}/${AM_TAR}"
  tar xzf "${AM_TAR}"
  install -m 0755 -o root -g root \
    "alertmanager-${AM_VERSION}.linux-${GOARCH}/alertmanager" /opt/alertmanager/bin/alertmanager
  install -m 0755 -o root -g root \
    "alertmanager-${AM_VERSION}.linux-${GOARCH}/amtool"       /opt/alertmanager/bin/amtool
  rm -rf "/tmp/alertmanager-${AM_VERSION}.linux-${GOARCH}" "/tmp/${AM_TAR}"
fi

echo "==> configs"
install -m 0644 -o root -g root "${ARTIFACTS}/prometheus.yml" /opt/prometheus/etc/prometheus.yml
install -m 0644 -o root -g root "${ARTIFACTS}/alertmanager.yml" /opt/alertmanager/etc/alertmanager.yml

echo "==> alert rules"
RULES_SRC="${REPO_DIR}/../../mcp-server/prometheus/alert_rules.yml"
if [[ ! -f "$RULES_SRC" ]]; then
  # Fallback when install.sh was scp'd standalone — accept a path hint.
  RULES_SRC="${ALERT_RULES_PATH:-/tmp/alert_rules.yml}"
fi
if [[ ! -f "$RULES_SRC" ]]; then
  echo "alert_rules.yml not found; set ALERT_RULES_PATH or scp it to /tmp/alert_rules.yml" >&2
  exit 1
fi
install -m 0644 -o root -g root "$RULES_SRC" /opt/prometheus/rules/alert_rules.yml

echo "==> tokens"
# Tokens must exist before promtool/amtool check-config — Prom/AM
# validate that credentials_file paths resolve at config-check time.
umask 077
install -D -m 0400 -o "$PROM_USER" -g "$PROM_USER" /dev/null /opt/prometheus/secrets/scrape_token
printf '%s' "$PROMETHEUS_SCRAPE_TOKEN" > /opt/prometheus/secrets/scrape_token
chown "$PROM_USER:$PROM_USER" /opt/prometheus/secrets/scrape_token
chmod 0400 /opt/prometheus/secrets/scrape_token

install -D -m 0400 -o "$AM_USER" -g "$AM_USER" /dev/null /opt/alertmanager/secrets/webhook_token
printf '%s' "$ALERTMANAGER_WEBHOOK_TOKEN" > /opt/alertmanager/secrets/webhook_token
chown "$AM_USER:$AM_USER" /opt/alertmanager/secrets/webhook_token
chmod 0400 /opt/alertmanager/secrets/webhook_token

# promtool validates both files with credentials_file resolved.
/opt/prometheus/bin/promtool check config /opt/prometheus/etc/prometheus.yml
/opt/prometheus/bin/promtool check rules /opt/prometheus/rules/alert_rules.yml
/opt/alertmanager/bin/amtool check-config /opt/alertmanager/etc/alertmanager.yml

echo "==> systemd units"
install -m 0644 -o root -g root "${ARTIFACTS}/systemd/prometheus.service"   /etc/systemd/system/prometheus.service
install -m 0644 -o root -g root "${ARTIFACTS}/systemd/alertmanager.service" /etc/systemd/system/alertmanager.service
systemctl daemon-reload

echo "==> firewall (ufw) — WG-only bind, but add defence-in-depth allow"
if command -v ufw >/dev/null 2>&1; then
  ufw --force allow from 10.100.0.0/24 to any port 9090 proto tcp comment 'Prometheus UI via WG' || true
  ufw --force allow from 10.100.0.0/24 to any port 9093 proto tcp comment 'Alertmanager UI via WG' || true
fi

echo "==> starting services"
systemctl enable --now prometheus.service
systemctl enable --now alertmanager.service

sleep 3
systemctl --no-pager --lines=0 status prometheus.service | head -3
systemctl --no-pager --lines=0 status alertmanager.service | head -3

echo
echo "Done. Smoke check from the VPS (10.100.0.1):"
echo "  curl -fsS http://10.100.0.3:9090/-/healthy    # Prometheus"
echo "  curl -fsS http://10.100.0.3:9093/-/healthy    # Alertmanager"
echo "  curl -fsS http://10.100.0.3:9090/api/v1/targets | jq '.data.activeTargets[] | {job,health}'"
