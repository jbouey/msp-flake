# WireGuard Management Tunnel -- Compliance Documentation

## Purpose
OsirisCare appliances maintain a WireGuard management tunnel to Central Command
for remote administration. This document describes the security controls and
compliance posture of this subsystem.

## Scope
The tunnel carries exclusively management traffic:
- SSH remote administration sessions
- Fleet order delivery (backup path)
- Configuration updates

**The tunnel does NOT carry:**
- Protected Health Information (PHI)
- Compliance evidence or drift scan results
- Patient data of any kind

PHI-bearing traffic uses TLS 1.3 encrypted HTTPS via the appliance's
standard API connection to Central Command.

## Technical Controls

### Encryption
- Protocol: WireGuard (formally verified, peer-reviewed)
- Cipher: ChaCha20-Poly1305
- Key exchange: Curve25519 (Diffie-Hellman)
- Hash: BLAKE2s
- Equivalent to NSA Suite B / CNSA algorithms

### Device Identity (NIST SP 800-207 S3.2)
- Each appliance generates a unique Curve25519 keypair on first boot
- Private key never leaves the device (generated locally, stored at /var/lib/msp/wireguard/private.key with 0600 permissions)
- Public key is the device's cryptographic identity
- No passwords, no certificates, no tokens to rotate or expire

### Access Control (HIPAA S164.312(d))
- Hub accepts connections only from registered public keys
- No spoke-to-spoke routing (peers cannot reach each other)
- SSH access through tunnel requires separate SSH key authentication
- Access limited to OsirisCare operations personnel

### Audit Logging (HIPAA S164.312(b))
- WireGuard handshake events logged by appliance daemon
- SSH sessions through tunnel logged in appliance journald
- All logs shipped to Central Command via log aggregation pipeline
- Logs retained per HIPAA 6-year requirement

### Key Management
- Keys generated on-device using OS CSPRNG
- Key rotation available via fleet order (rotate_wg_key)
- Decommissioned sites have peers automatically removed from hub
- File integrity monitoring alerts on unexpected key file access

### Network Architecture
- Hub: VPS at Central Command, UDP 51820
- Spokes: Appliances connect outbound (NAT-friendly)
- PersistentKeepalive: 25 seconds (maintains NAT traversal)
- VPN subnet: 10.100.0.0/24 (management plane only)

## Regulatory Alignment

| Requirement | Control | Evidence |
|-------------|---------|----------|
| HIPAA S164.312(a)(1) -- Access Control | Cryptographic device identity, per-peer AllowedIPs | WireGuard peer config |
| HIPAA S164.312(b) -- Audit Controls | Handshake + SSH session logging | Central Command log pipeline |
| HIPAA S164.312(d) -- Person/Entity Auth | Curve25519 keypair + SSH key | Key files on device |
| HIPAA S164.312(e)(1) -- Transmission Security | ChaCha20-Poly1305 encryption | WireGuard protocol spec |
| NIST SP 800-207 S3.2 -- Device Identity | Cryptographic identity, no implicit trust | Zero Trust architecture |

## Incident Response
- Compromised key: Issue rotate_wg_key fleet order + remove old peer from hub
- Unauthorized access: Review SSH logs in Central Command, revoke peer
- Hub compromise: Regenerate hub keypair, redistribute to all appliances via fleet order

## Review Schedule
This document should be reviewed annually or when significant changes
are made to the WireGuard architecture.

Last reviewed: 2026-03-23
Next review: 2027-03-23
