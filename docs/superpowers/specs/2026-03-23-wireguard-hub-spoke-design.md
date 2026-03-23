# WireGuard Hub-Spoke Remote Management

**Date:** 2026-03-23
**Status:** Approved
**Approach:** Option C — appliance generates own keys, private key never leaves device

## Problem

Appliances deployed at client sites are behind NAT. No way to remotely SSH, troubleshoot, or manage them without being physically on-site. Self-healing failures (credential lookup, agent redeploy) cannot be diagnosed remotely.

## Architecture

```
VPS Hub (178.156.162.116)
  wg0: 10.100.0.1/24, UDP 51820
    |
    ├── Appliance 1 → 10.100.0.2 (PersistentKeepalive=25)
    ├── Appliance 2 → 10.100.0.3
    └── Appliance N → 10.100.0.N
```

- Hub: VPS, public IP, listens on UDP 51820
- Spokes: appliances connect outbound (NAT-friendly)
- Tunnel carries SSH management traffic only — no patient data
- NIST SP 800-207 aligned: Curve25519 keypair IS device identity

## Provisioning Flow

1. Appliance boots, generates WireGuard keypair at `/var/lib/msp/wireguard/`
2. `POST /api/provision/claim` includes `wg_pubkey` field
3. Backend assigns next 10.100.0.N IP, stores in DB
4. Backend adds peer to VPS hub via SSH + `wg syncconf`
5. Response includes `wg_hub_pubkey`, `wg_hub_endpoint`, `wg_ip`
6. Appliance configures wg0, tunnel comes up
7. Appliance reachable: `ssh root@10.100.0.N` from anywhere on VPS

## Components

### 1. VPS Hub Setup (one-time)
- Generate hub keypair
- Configure wg0 interface: 10.100.0.1/24, UDP 51820
- Firewall: allow UDP 51820 inbound
- Peer directory: `/opt/mcp-server/wireguard/peers/`

### 2. Appliance Spoke (baked into flake)
- systemd service generates keypair on first boot if missing
- WireGuard interface configured declaratively
- Reads VPN IP + hub pubkey from `/var/lib/msp/wireguard/config.json`
- PersistentKeepalive = 25

### 3. Backend Integration
- Migration 097: `wg_pubkey TEXT`, `wg_ip TEXT` on sites table
- Provision endpoint accepts wg_pubkey
- IP allocation: sequential from 10.100.0.2
- Hub update: SSH to VPS, write peer file, `wg syncconf wg0`

### 4. Dashboard UI
- Site detail shows WireGuard status (connected/disconnected)
- VPN IP displayed for admin reference

## Security

- Private keys never leave their host
- Public keys exchanged during TLS-encrypted provision flow
- Hub only accepts peers with registered public keys
- Tunnel encrypted with ChaCha20-Poly1305
- No routing between spokes — hub-to-spoke only

## Files Changed

| File | Change |
|------|--------|
| `modules/wireguard-hub.nix` | NEW: VPS hub NixOS module |
| `iso/configuration.nix` | Add WireGuard spoke service |
| `backend/provisioning.py` | Accept wg_pubkey, assign IP, update hub |
| `backend/migrations/097_wireguard.sql` | wg_pubkey + wg_ip columns |
