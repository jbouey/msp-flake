# EV SSL Certificate Swap Instructions

When the EV cert arrives from ssl.com:

## 1. Upload cert files to VPS

```bash
scp osiriscare.net.crt root@178.156.162.116:/opt/mcp-server/certs/
scp osiriscare.net.key root@178.156.162.116:/opt/mcp-server/certs/
# If there's a CA bundle/chain file:
scp ca-bundle.crt root@178.156.162.116:/opt/mcp-server/certs/
```

## 2. Create combined cert (if chain provided separately)

```bash
ssh root@178.156.162.116
cat /opt/mcp-server/certs/osiriscare.net.crt /opt/mcp-server/certs/ca-bundle.crt > /opt/mcp-server/certs/fullchain.pem
chmod 600 /opt/mcp-server/certs/*.key
```

## 3. Update Caddyfile

For EACH site block (`api.osiriscare.net`, `dashboard.osiriscare.net`, `msp.osiriscare.net`, `portal.osiriscare.net`, `www.osiriscare.net`), add the `tls` directive as the FIRST line inside the block:

```
api.osiriscare.net {
    tls /opt/mcp-server/certs/fullchain.pem /opt/mcp-server/certs/osiriscare.net.key
    import security_headers
    ...
}
```

Repeat for all 5 site blocks. The `osiriscare.net` redirect block does NOT need TLS (Caddy will auto-cert that one, or add the same tls directive).

## 4. Mount certs into Caddy container

Edit docker-compose.yml to add the certs volume:

```yaml
caddy:
  volumes:
    - /opt/mcp-server/certs:/opt/mcp-server/certs:ro
```

Then restart: `docker compose up -d caddy`

## 5. Reload Caddy

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## 6. Verify

```bash
curl -vI https://api.osiriscare.net 2>&1 | grep -i "issuer\|subject\|SSL"
# Should show ssl.com as issuer and OsirisCare organization in subject
```

## Rollback

Remove the `tls` lines from Caddyfile and reload — Caddy reverts to Let's Encrypt auto.
