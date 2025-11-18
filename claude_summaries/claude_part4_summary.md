# Summary: claude_part4.md

**Main Topics:** Evidence Registry, SBOM Generation, Multi-Source Time Sync, Hash Chain Log Integrity, Blockchain Anchoring

**Key Ideas:**
- **Evidence Registry:** Append-only SQLite database with WORM constraints (cannot update/delete entries)
- **SBOM Generation:** Creates Software Bill of Materials in SPDX 2.3 format by parsing NixOS store paths
- **Multi-Source Time Synchronization:**
  - Essential tier: NTP only (3+ servers required)
  - Professional tier: NTP + GPS (Stratum 0 source)
  - Enterprise tier: NTP + GPS + Bitcoin blockchain time
- **Time Anomaly Detection:** Monitors drift >100ms, alerts via webhook, logs all adjustments
- **Hash Chain Log Integrity:** Links log snapshots cryptographically (blockchain-style), detects tampering
- **Blockchain Anchoring:** Bitcoin OP_RETURN transactions for external immutability proof (Enterprise tier only)

**Repeated Themes:**
- Append-only/WORM patterns for tamper-evident evidence
- Tier-based feature flags (Essential → Professional → Enterprise)
- Cryptographic proof of time and integrity
- Forensic-grade audit trails

**Code Examples:**
- EvidenceRegistry with SQLite triggers preventing updates/deletes
- SBOMGenerator parsing Nix store paths to SPDX JSON
- NixOS time-sync module with GPS/Bitcoin integration
- Hash chain service linking log snapshots
- BlockchainAnchor using Bitcoin OP_RETURN
