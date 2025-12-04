# Software Provenance & Time Framework

## Overview

**Core Principle:** Every action, every build, every log entry must be cryptographically provable as authentic and temporally ordered.

In healthcare compliance, you need to prove:
- *What* happened
- *When* it happened
- *Who* did it
- That evidence hasn't been tampered with

This framework makes tampering mathematically impossible.

## What NixOS Gives You Free

NixOS's content-addressed store provides foundational provenance:

1. **Content Addressing:** Every derivation has unique hash based on ALL inputs
2. **Reproducible Builds:** Same inputs → identical binary → same hash
3. **Derivation Files:** Machine-readable record of every build
4. **Closure Tracking:** Complete dependency graph

```bash
# Query what built a package
$ nix-store --query --deriver /nix/store/abc123-nginx-1.24.0

# Get complete dependency graph
$ nix-store --query --requisites /nix/store/abc123-nginx-1.24.0

# Verify integrity
$ nix-store --verify --check-contents /nix/store/abc123-nginx-1.24.0
```

## What This Framework Adds

- Cryptographic signatures proving WHO authorized builds
- SBOM export in SPDX/CycloneDX formats
- Multi-source time attestation
- Hash chain linking evidence over time
- Blockchain anchoring for external verification

## Build Signing (Essential Tier)

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

    # Auto-sign all builds
    nix.settings.post-build-hook = pkgs.writeShellScript "sign-build" ''
      for path in $OUT_PATHS; do
        nix store sign --key-file ${cfg.signingKey} "$path"
      done
    '';
  };
}
```

## Evidence Signing (Professional Tier)

```python
class EvidenceSigner:
    """Sign evidence bundles with cosign"""

    def sign_bundle(self, bundle_path: Path) -> dict:
        sig_path = bundle_path.with_suffix('.sig')

        subprocess.run([
            'cosign', 'sign-blob',
            '--key', self.key_path,
            '--output-signature', str(sig_path),
            str(bundle_path)
        ], check=True)

        return {
            "bundle_path": str(bundle_path),
            "signature_path": str(sig_path),
            "signed_at": datetime.utcnow().isoformat(),
            "algorithm": "ECDSA-P256-SHA256",
            "bundle_hash": self._compute_hash(bundle_path)
        }

    def verify_bundle(self, bundle_path: Path, public_key: str) -> bool:
        subprocess.run([
            'cosign', 'verify-blob',
            '--key', public_key,
            '--signature', bundle_path.with_suffix('.sig'),
            str(bundle_path)
        ], check=True)
        return True
```

## Evidence Registry (WORM)

Append-only SQLite registry:

```python
class EvidenceRegistry:
    """WORM pattern - cannot delete or modify entries"""

    def _init_db(self):
        # Create append-only triggers
        conn.execute('''
            CREATE TRIGGER prevent_bundle_updates
            BEFORE UPDATE ON evidence_bundles
            BEGIN
                SELECT RAISE(ABORT, 'Registry is append-only');
            END
        ''')

    def register(self, bundle_id, client_id, bundle_hash, worm_url, tier):
        """Register new evidence bundle"""
        conn.execute('''
            INSERT INTO evidence_bundles
            (bundle_id, client_id, generated_at, bundle_hash, worm_url, tier)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', ...)
```

## SBOM Generation

Generate Software Bill of Materials in SPDX format:

```python
class SBOMGenerator:
    def generate_spdx(self, system_path: str) -> dict:
        # Query all dependencies
        result = subprocess.run([
            'nix-store', '--query', '--requisites', system_path
        ], capture_output=True, text=True)

        packages = []
        for path in result.stdout.strip().split('\n'):
            packages.append(self._parse_store_path(path))

        return {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "name": f"MSP-Client-System-{date}",
            "packages": packages
        }
```

## Multi-Source Time Synchronization

```nix
{ config, lib, pkgs, ... }: {
  services.msp.timeSync = {
    enable = true;
    tier = "professional";  # essential | professional | enterprise

    ntpServers = [
      "time.nist.gov"
      "time.cloudflare.com"
      "pool.ntp.org"
    ];

    gpsDevice = "/dev/ttyUSB0";  # Professional tier
    bitcoinEnabled = true;       # Enterprise tier
    maxDriftMs = 100;
  };

  # Base NTP configuration
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

```python
class HashChainLogger:
    """Blockchain-style hash chain for logs"""

    def add_link(self, log_snapshot: bytes) -> dict:
        current_hash = self._compute_hash(
            prev_hash.encode() + log_snapshot
        )

        link = {
            "timestamp": datetime.utcnow().isoformat(),
            "prev_hash": self.prev_hash,
            "hash": current_hash,
            "log_count": len(log_snapshot)
        }

        # Append to chain file (atomic write)
        with open(self.chain_file, 'a') as f:
            f.write(json.dumps(link) + "\n")

        self.prev_hash = current_hash
        return link

    def verify_chain(self) -> bool:
        """Verify chain integrity - detect tampering"""
        with open(self.chain_file) as f:
            links = [json.loads(line) for line in f]

        for i in range(1, len(links)):
            if links[i]['prev_hash'] != links[i-1]['hash']:
                return False  # Chain broken!
        return True
```

## Blockchain Anchoring (Enterprise Tier)

```python
class BlockchainAnchor:
    """Anchor evidence to Bitcoin blockchain"""

    def anchor_hash(self, evidence_hash: str) -> dict:
        # Create OP_RETURN with evidence hash
        op_return = f"MSP:{evidence_hash[:32]}"
        txid = self._broadcast_transaction(op_return)

        # Wait for 6 confirmations (~1 hour)
        while self._get_confirmations(txid) < 6:
            time.sleep(600)

        return {
            "txid": txid,
            "block_hash": self._get_block_hash(txid),
            "evidence_hash": evidence_hash,
            "blockchain": "bitcoin"
        }
```

## Compliance Tiers

| Feature | Essential | Professional | Enterprise |
|---------|-----------|--------------|------------|
| NTP time sync | Basic | Multi-source + GPS | + Bitcoin |
| Evidence bundles | Unsigned | Signed (cosign) | + Blockchain |
| Retention | 30 days | 90 days | 2 years |
| Hash chains | Local | + Remote backup | + 1-min intervals |
| SBOM | None | SPDX | + CycloneDX |

## MCP Tools

```python
# Time check tool
class TimeCheckTool:
    async def execute(self, params: Dict) -> Dict:
        tracking = await self._query_chrony()
        anomalies = []

        if abs(tracking['offset_seconds']) > 0.1:
            anomalies.append({
                "type": "time_drift",
                "severity": "high"
            })

        return {
            "status": "anomaly_detected" if anomalies else "ok",
            "hipaa_control": "164.312(b)"
        }

# Chain verification tool
class VerifyChainTool:
    async def execute(self, params: Dict) -> Dict:
        if not self._verify_chain_integrity():
            return {
                "status": "tampered",
                "error": "Chain integrity compromised"
            }
        return {"status": "verified"}
```

## Implementation Checklist

### Sprint 1: Foundation (Week 6)
- [ ] Build signing module
- [ ] Generate signing keys
- [ ] Configure clients to verify

### Sprint 2: Evidence Registry (Week 7)
- [ ] Implement EvidenceRegistry with SQLite
- [ ] Add append-only triggers
- [ ] Integrate EvidenceSigner with cosign

### Sprint 3: Time Framework (Week 8)
- [ ] Implement time-sync module (Essential)
- [ ] Add GPS support (Professional)
- [ ] Implement anomaly detector

### Sprint 4: Hash Chains (Week 9)
- [ ] Implement log-integrity module
- [ ] Start hash chain service
- [ ] Add verification tool

### Sprint 5: Enterprise (Week 10)
- [ ] SBOM generation
- [ ] Bitcoin anchoring module
- [ ] Tier-based feature flags

## Success Criteria

- All builds cryptographically signed
- Evidence bundles signed and registered
- Multi-source time sync with anomaly detection
- Hash chain proving log integrity
- SBOM for every deployment
- Enterprise tier with blockchain anchoring
