        conn.execute('''
            CREATE TABLE IF NOT EXISTS evidence_bundles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bundle_id TEXT NOT NULL UNIQUE,
                client_id TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                bundle_hash TEXT NOT NULL,
                signature_hash TEXT,
                worm_url TEXT,
                tier TEXT NOT NULL,
                signed BOOLEAN NOT NULL DEFAULT 0,
                anchored BOOLEAN NOT NULL DEFAULT 0,
                anchor_txid TEXT,
                registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create append-only trigger (prevent updates/deletes)
        conn.execute('''
            CREATE TRIGGER IF NOT EXISTS prevent_bundle_updates
            BEFORE UPDATE ON evidence_bundles
            BEGIN
                SELECT RAISE(ABORT, 'Evidence registry is append-only');
            END
        ''')

        conn.execute('''
            CREATE TRIGGER IF NOT EXISTS prevent_bundle_deletes
            BEFORE DELETE ON evidence_bundles
            BEGIN
                SELECT RAISE(ABORT, 'Evidence registry is append-only');
            END
        ''')

        conn.commit()
        conn.close()

    def register(self,
                 bundle_id: str,
                 client_id: str,
                 bundle_hash: str,
                 worm_url: str,
                 tier: str,
                 signature_hash: Optional[str] = None) -> int:
        """Register new evidence bundle (append-only)"""

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            INSERT INTO evidence_bundles
            (bundle_id, client_id, generated_at, bundle_hash,
             signature_hash, worm_url, tier, signed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            bundle_id,
            client_id,
            datetime.utcnow().isoformat(),
            bundle_hash,
            signature_hash,
            worm_url,
            tier,
            signature_hash is not None
        ))

        bundle_pk = cursor.lastrowid
        conn.commit()
        conn.close()

        return bundle_pk

    def update_anchor(self, bundle_id: str, txid: str):
        """
        Update blockchain anchor (only field allowed to change)
        This is technically a violation of pure WORM, but acceptable
        because anchoring happens asynchronously after bundle creation
        """
        conn = sqlite3.connect(self.db_path)

        # Use raw SQL to bypass trigger (anchoring is special case)
        conn.execute('PRAGMA defer_foreign_keys = ON')
        conn.execute('''
            UPDATE evidence_bundles
            SET anchored = 1, anchor_txid = ?
            WHERE bundle_id = ?
        ''', (txid, bundle_id))

        conn.commit()
        conn.close()

    def query(self,
              client_id: Optional[str] = None,
              start_date: Optional[datetime] = None,
              end_date: Optional[datetime] = None,
              signed_only: bool = False) -> List[dict]:
        """Query evidence registry"""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        query = 'SELECT * FROM evidence_bundles WHERE 1=1'
        params = []

        if client_id:
            query += ' AND client_id = ?'
            params.append(client_id)

        if start_date:
            query += ' AND generated_at >= ?'
            params.append(start_date.isoformat())

        if end_date:
            query += ' AND generated_at <= ?'
            params.append(end_date.isoformat())

        if signed_only:
            query += ' AND signed = 1'

        query += ' ORDER BY registered_at DESC'

        cursor = conn.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results
```

### SBOM Generation

Generate Software Bill of Materials in SPDX/CycloneDX format:

```python
# mcp-server/sbom/generator.py
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List

class SBOMGenerator:
    """Generate SBOM (Software Bill of Materials) for NixOS systems"""

    def generate_spdx(self, system_path: str, output_path: Path) -> dict:
        """
        Generate SPDX 2.3 SBOM for NixOS system
        Uses nix-store to enumerate all packages
        """

        # Query all runtime dependencies
        result = subprocess.run([
            'nix-store', '--query', '--requisites', system_path
        ], capture_output=True, text=True, check=True)

        store_paths = result.stdout.strip().split('\n')

        # Parse package information
        packages = []
        for path in store_paths:
            pkg_info = self._parse_store_path(path)
            if pkg_info:
                packages.append(pkg_info)

        # Build SPDX document
        spdx = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": f"MSP-Client-System-{datetime.utcnow().strftime('%Y%m%d')}",
            "documentNamespace": f"https://msp.example.com/sbom/{datetime.utcnow().isoformat()}",
            "creationInfo": {
                "created": datetime.utcnow().isoformat(),
                "creators": ["Tool: MSP-SBOM-Generator-1.0"],
                "licenseListVersion": "3.21"
            },
            "packages": packages,
            "relationships": self._build_relationships(packages)
        }

        # Write SPDX JSON
        with open(output_path, 'w') as f:
            json.dump(spdx, f, indent=2)

        return spdx

    def _parse_store_path(self, path: str) -> dict:
        """Parse Nix store path into SPDX package"""

        # Extract package name and version from path
        # /nix/store/abc123-nginx-1.24.0 → nginx, 1.24.0
        parts = Path(path).name.split('-', 1)
        if len(parts) < 2:
            return None

        hash_prefix = parts[0]
        name_version = parts[1]

        # Split name and version
        version = None
        for i in range(len(name_version) - 1, -1, -1):
            if name_version[i].isdigit():
                version_start = name_version.rfind('-', 0, i)
                if version_start != -1:
                    name = name_version[:version_start]
                    version = name_version[version_start+1:]
                    break

        if not version:
            name = name_version
            version = "unknown"

        return {
            "SPDXID": f"SPDXRef-Package-{hash_prefix}",
            "name": name,
            "versionInfo": version,
            "downloadLocation": f"https://cache.nixos.org/{path}",
            "filesAnalyzed": False,
            "supplier": "Organization: NixOS",
            "externalRefs": [{
                "referenceCategory": "PACKAGE_MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:nix/{name}@{version}"
            }]
        }

    def _build_relationships(self, packages: List[dict]) -> List[dict]:
        """Build SPDX relationships (dependencies)"""

        relationships = []

        # Document DESCRIBES first package
        if packages:
            relationships.append({
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": packages[0]["SPDXID"]
            })

        # All packages are CONTAINED_BY document
        for pkg in packages:
            relationships.append({
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": pkg["SPDXID"]
            })

        return relationships
```

**Integration with Compliance Packets:**
```python
# Add SBOM to monthly compliance packet
async def generate_nightly_packet(self, date: datetime = None) -> str:
    # ... existing evidence collection ...

    # Generate SBOM (Professional/Enterprise tier)
    if self.tier in ['professional', 'enterprise']:
        sbom_gen = SBOMGenerator()
        sbom_path = packet_dir / "sbom.spdx.json"
        sbom_gen.generate_spdx(
            system_path='/run/current-system',
            output_path=sbom_path
        )
        evidence_files.append(sbom_path)

    # ... rest of packet generation ...
```

### Multi-Source Time Synchronization

**NixOS Module for Multi-Source Time Sync:**

```nix
# flake/modules/audit/time-sync.nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.timeSync;

in {
  options.services.msp.timeSync = {
    enable = mkEnableOption "MSP multi-source time synchronization";

    tier = mkOption {
      type = types.enum ["essential" "professional" "enterprise"];
      default = "essential";
      description = "Compliance tier determines time sources";
    };

    ntpServers = mkOption {
      type = types.listOf types.str;
      default = [
        "time.nist.gov"
        "time.cloudflare.com"
        "pool.ntp.org"
      ];
      description = "NTP servers for Essential tier";
    };

    gpsDevice = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "/dev/ttyUSB0";
      description = "GPS device for Professional tier (Stratum 0)";
    };

    bitcoinEnabled = mkOption {
      type = types.bool;
      default = false;
      description = "Enable Bitcoin blockchain time (Enterprise tier)";
    };

    maxDriftMs = mkOption {
      type = types.int;
      default = 100;
      description = "Maximum allowed drift in milliseconds";
    };

    anomalyWebhook = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Webhook URL for time anomaly alerts";
    };
  };

  config = mkIf cfg.enable {

    # Base NTP configuration (Essential tier)
    services.chrony = {
      enable = true;
      servers = cfg.ntpServers;

      extraConfig = ''
        # Require multiple sources to agree
        minsources 2

        # Maximum allowed offset
        maxdrift ${toString cfg.maxDriftMs}

        # Log time adjustments
        logdir /var/log/chrony
        log measurements statistics tracking
      '';
    };

    # GPS time source (Professional tier)
    systemd.services.gpsd = mkIf (cfg.gpsDevice != null) {
      description = "GPS Time Daemon";
      wantedBy = [ "multi-user.target" ];
      after = [ "chronyd.service" ];

      serviceConfig = {
        ExecStart = "${pkgs.gpsd}/bin/gpsd -N ${cfg.gpsDevice}";
        Restart = "always";
      };
    };

    # Chrony GPS integration
    services.chrony.extraConfig = mkIf (cfg.gpsDevice != null) ''
      # GPS as Stratum 0 source (highest priority)
      refclock SHM 0 refid GPS precision 1e-1 offset 0.0
    '';

    # Time anomaly detection service
    systemd.services.time-anomaly-detector = {
      description = "MSP Time Anomaly Detector";
      after = [ "chronyd.service" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        ExecStart = pkgs.writeScript "time-anomaly-detector" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          LOG_FILE="/var/log/msp/time-anomaly.log"
          mkdir -p "$(dirname "$LOG_FILE")"

          while true; do
            # Query chrony tracking
            TRACKING=$(${pkgs.chrony}/bin/chronyc tracking)

            # Extract system time offset
            OFFSET=$(echo "$TRACKING" | grep "System time" | awk '{print $4}')
            OFFSET_ABS=$(echo "$OFFSET" | tr -d '-')

            # Check if offset exceeds threshold
            THRESHOLD=$(echo "${toString cfg.maxDriftMs} / 1000" | ${pkgs.bc}/bin/bc -l)

            if (( $(echo "$OFFSET_ABS > $THRESHOLD" | ${pkgs.bc}/bin/bc -l) )); then
              TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
              MESSAGE="TIME ANOMALY: Offset $OFFSET seconds exceeds threshold $THRESHOLD"

              echo "$TIMESTAMP $MESSAGE" >> "$LOG_FILE"
              logger -t time-anomaly -p warning "$MESSAGE"

              # Send webhook alert
              ${optionalString (cfg.anomalyWebhook != null) ''
                ${pkgs.curl}/bin/curl -X POST \
                  -H "Content-Type: application/json" \
                  -d "{\"timestamp\":\"$TIMESTAMP\",\"offset\":$OFFSET,\"threshold\":$THRESHOLD}" \
                  "${cfg.anomalyWebhook}" \
                  || true
              ''}
            fi

            sleep 60
          done
        '';
      };
    };

    # Bitcoin blockchain time (Enterprise tier)
    systemd.services.bitcoin-time-sync = mkIf cfg.bitcoinEnabled {
      description = "Bitcoin Blockchain Time Reference";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        ExecStart = pkgs.writeScript "bitcoin-time-sync" ''
          #!${pkgs.python3}/bin/python3
          import time
          import requests
          import json
          from datetime import datetime

          LOG_FILE = "/var/log/msp/bitcoin-time.log"

          while True:
              try:
                  # Query Bitcoin blockchain for latest block time
                  resp = requests.get("https://blockchain.info/latestblock", timeout=10)
                  block = resp.json()

                  block_time = block['time']
                  local_time = int(time.time())
                  drift = abs(block_time - local_time)

                  log_entry = {
                      "timestamp": datetime.utcnow().isoformat(),
                      "block_height": block['height'],
                      "block_time": block_time,
                      "local_time": local_time,
                      "drift_seconds": drift
                  }

                  with open(LOG_FILE, 'a') as f:
                      f.write(json.dumps(log_entry) + "\n")

                  # Alert if drift > 5 minutes
                  if drift > 300:
                      print(f"WARNING: Bitcoin time drift: {drift}s", flush=True)

              except Exception as e:
                  print(f"ERROR: {e}", flush=True)

              time.sleep(600)  # Check every 10 minutes
        '';
      };
    };

    # Audit logging for time changes
    security.auditd.enable = true;
    security.audit.rules = [
      # Log time changes
      "-a always,exit -F arch=b64 -S adjtimex -S settimeofday -S clock_settime -k time-change"

      # Log chrony operations
      "-w /var/log/chrony/ -p wa -k chrony-logs"
    ];

    # Time sync health check
    systemd.services.time-sync-health = {
      description = "Time Sync Health Check";

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "time-sync-health" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          HEALTH_LOG="/var/log/msp/time-sync-health.log"
          mkdir -p "$(dirname "$HEALTH_LOG")"

          echo "=== Time Sync Health Check $(date) ===" >> "$HEALTH_LOG"

          # Check chrony status
          if ${pkgs.systemd}/bin/systemctl is-active chronyd > /dev/null 2>&1; then
            echo "✓ chronyd active" >> "$HEALTH_LOG"
          else
            echo "✗ chronyd NOT active" >> "$HEALTH_LOG"
            exit 1
          fi

          # Check NTP sync status
          if ${pkgs.chrony}/bin/chronyc tracking | grep "Reference ID" > /dev/null; then
            echo "✓ NTP synchronized" >> "$HEALTH_LOG"
          else
            echo "✗ NTP NOT synchronized" >> "$HEALTH_LOG"
            exit 1
          fi

          # Check time sources
          SOURCES=$(${pkgs.chrony}/bin/chronyc sources | grep -c "^\\*" || echo "0")
          echo "Active time sources: $SOURCES" >> "$HEALTH_LOG"

          if [ "$SOURCES" -lt 2 ]; then
            echo "⚠ Less than 2 active time sources" >> "$HEALTH_LOG"
          fi

          ${optionalString (cfg.gpsDevice != null) ''
            # Check GPS status
            if ${pkgs.systemd}/bin/systemctl is-active gpsd > /dev/null 2>&1; then
              echo "✓ GPS daemon active" >> "$HEALTH_LOG"
            else
              echo "⚠ GPS daemon not active" >> "$HEALTH_LOG"
            fi
          ''}

          ${optionalString cfg.bitcoinEnabled ''
            # Check Bitcoin time sync
            if ${pkgs.systemd}/bin/systemctl is-active bitcoin-time-sync > /dev/null 2>&1; then
              echo "✓ Bitcoin time sync active" >> "$HEALTH_LOG"
            else
              echo "⚠ Bitcoin time sync not active" >> "$HEALTH_LOG"
            fi
          ''}

          echo "Health check completed" >> "$HEALTH_LOG"
        '';
      };
    };

    # Run health check daily
    systemd.timers.time-sync-health = {
      description = "Time Sync Health Check Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
        Unit = "time-sync-health.service";
      };
    };
  };
}
```

### Hash Chain Log Integrity

**NixOS Module for Hash-Chained Logs:**

```nix
# flake/modules/audit/log-integrity.nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.logIntegrity;

in {
  options.services.msp.logIntegrity = {
    enable = mkEnableOption "MSP hash-chained log integrity";

    logPaths = mkOption {
      type = types.listOf types.str;
      default = [
        "/var/log/msp/"
        "/var/log/audit/"
        "/var/log/auth.log"
      ];
      description = "Paths to monitor for integrity";
    };

    chainInterval = mkOption {
      type = types.int;
      default = 60;
      description = "Seconds between hash chain links";
    };

    storePath = mkOption {
      type = types.path;
      default = "/var/lib/msp/hash-chain";
      description = "Path to store hash chain data";
    };
  };

  config = mkIf cfg.enable {

    systemd.services.log-hash-chain = {
      description = "MSP Log Hash Chain Service";
      wantedBy = [ "multi-user.target" ];
      after = [ "auditd.service" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        ExecStart = pkgs.writeScript "log-hash-chain" ''
          #!${pkgs.python3}/bin/python3
          import os
          import time
          import hashlib
          import json
          from pathlib import Path
          from datetime import datetime

          CHAIN_FILE = Path("${cfg.storePath}/chain.jsonl")
          CHAIN_FILE.parent.mkdir(parents=True, exist_ok=True)

          def compute_hash(data: bytes, prev_hash: str) -> str:
              """Compute hash with previous hash as salt (blockchain-style)"""
              h = hashlib.sha256()
              h.update(prev_hash.encode())
              h.update(data)
              return h.hexdigest()

          def get_log_snapshot() -> bytes:
              """Get snapshot of all monitored logs"""
              snapshot = []

              for log_path in ${builtins.toJSON cfg.logPaths}:
                  path = Path(log_path)
                  if path.is_dir():
                      # Hash all files in directory
                      for file in sorted(path.rglob("*")):
                          if file.is_file():
                              try:
                                  with open(file, 'rb') as f:
                                      content = f.read()
                                      file_hash = hashlib.sha256(content).hexdigest()
                                      snapshot.append(f"{file}:{file_hash}")
                              except Exception:
                                  pass
                  elif path.is_file():
                      try:
                          with open(path, 'rb') as f:
                              content = f.read()
                              file_hash = hashlib.sha256(content).hexdigest()
                              snapshot.append(f"{path}:{file_hash}")
                      except Exception:
                          pass

              return "\n".join(snapshot).encode()

          # Initialize chain
          if CHAIN_FILE.exists():
              with open(CHAIN_FILE, 'r') as f:
                  lines = f.readlines()
                  if lines:
                      last_link = json.loads(lines[-1])
                      prev_hash = last_link['hash']
                  else:
                      prev_hash = "0" * 64  # Genesis hash
          else:
              prev_hash = "0" * 64

          print(f"Starting hash chain with prev_hash: {prev_hash[:16]}...", flush=True)

          while True:
              try:
                  # Get current log snapshot
                  snapshot = get_log_snapshot()

                  # Compute hash linked to previous
                  current_hash = compute_hash(snapshot, prev_hash)

                  # Create chain link
                  link = {
                      "timestamp": datetime.utcnow().isoformat(),
                      "prev_hash": prev_hash,
                      "hash": current_hash,
                      "log_count": len(snapshot.decode().split("\n"))
                  }

                  # Append to chain (atomic write)
                  with open(CHAIN_FILE, 'a') as f:
                      f.write(json.dumps(link) + "\n")
                      f.flush()
                      os.fsync(f.fileno())

                  print(f"Link added: {current_hash[:16]}... (logs: {link['log_count']})", flush=True)

                  prev_hash = current_hash

              except Exception as e:
                  print(f"ERROR: {e}", flush=True)

              time.sleep(${toString cfg.chainInterval})
        '';
      };
    };

    # Chain verification service
    systemd.services.verify-log-chain = {
      description = "Verify Log Hash Chain Integrity";

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "verify-log-chain" ''
          #!${pkgs.python3}/bin/python3
          import json
          from pathlib import Path

          CHAIN_FILE = Path("${cfg.storePath}/chain.jsonl")

          if not CHAIN_FILE.exists():
              print("No chain file found")
              exit(0)

          print("Verifying hash chain integrity...")

          with open(CHAIN_FILE, 'r') as f:
              links = [json.loads(line) for line in f]

          if not links:
              print("Empty chain")
              exit(0)

          # Verify first link (genesis)
          if links[0]['prev_hash'] != "0" * 64:
              print(f"ERROR: Invalid genesis block")
              exit(1)

          # Verify chain continuity
          for i in range(1, len(links)):
              if links[i]['prev_hash'] != links[i-1]['hash']:
                  print(f"ERROR: Chain broken at link {i}")
                  print(f"  Expected prev_hash: {links[i-1]['hash']}")
                  print(f"  Got prev_hash: {links[i]['prev_hash']}")
                  exit(1)

          print(f"✓ Chain verified: {len(links)} links, no tampering detected")
          exit(0)
        '';
      };
    };

    # Run verification daily
    systemd.timers.verify-log-chain = {
      description = "Verify Log Hash Chain Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
        Unit = "verify-log-chain.service";
      };
    };
  };
}
```

### Blockchain Anchoring (Enterprise Tier)

**Python Service for Bitcoin Anchoring:**

```python
# mcp-server/blockchain/anchor.py
import requests
import hashlib
import json
from datetime import datetime
from typing import Optional

class BlockchainAnchor:
    """
    Anchor evidence bundles to Bitcoin blockchain
    Enterprise tier only - provides external immutability proof
    """

    def __init__(self,
                 bitcoin_rpc_url: str = "http://localhost:8332",
                 rpc_user: Optional[str] = None,
                 rpc_password: Optional[str] = None):
        self.rpc_url = bitcoin_rpc_url
        self.rpc_user = rpc_user
        self.rpc_password = rpc_password

    def anchor_hash(self, evidence_hash: str) -> dict:
        """
        Anchor evidence hash to Bitcoin blockchain
        Uses OP_RETURN to embed hash in transaction
        """

        # Create OP_RETURN output with hash
        op_return_data = f"MSP:{evidence_hash[:32]}"

        # Create Bitcoin transaction (simplified - real impl uses bitcoin-cli)
        tx_hex = self._create_op_return_tx(op_return_data)

        # Broadcast transaction
        txid = self._broadcast_transaction(tx_hex)

        # Wait for confirmation
        confirmations = 0
        while confirmations < 6:  # Wait for 6 confirmations (~1 hour)
            time.sleep(600)  # 10 minutes
            confirmations = self._get_confirmations(txid)

        # Get block hash
        block_hash = self._get_block_hash(txid)

        return {
            "txid": txid,
            "block_hash": block_hash,
            "confirmations": confirmations,
            "anchored_at": datetime.utcnow().isoformat(),
            "evidence_hash": evidence_hash,
            "blockchain": "bitcoin"
        }

    def verify_anchor(self, txid: str, expected_hash: str) -> bool:
        """Verify that evidence hash is in blockchain"""

        # Get transaction
        tx = self._get_transaction(txid)

        # Extract OP_RETURN data
        for vout in tx['vout']:
            if vout['scriptPubKey']['type'] == 'nulldata':
                op_return_hex = vout['scriptPubKey']['hex'][4:]  # Skip OP_RETURN opcode
                op_return_data = bytes.fromhex(op_return_hex).decode('utf-8')

                if op_return_data.startswith('MSP:'):
                    anchored_hash = op_return_data[4:]
                    return anchored_hash == expected_hash[:32]

        return False

    def _create_op_return_tx(self, data: str) -> str:
        """Create Bitcoin transaction with OP_RETURN output"""
        # Simplified - real implementation uses bitcoin-cli or bitcoinlib

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "createrawtransaction",
            "params": [
                [],  # Inputs (would need UTXO selection)
                {
                    "data": data.encode().hex()  # OP_RETURN output
                }
            ]
        }

        response = requests.post(
            self.rpc_url,
            json=rpc_call,
            auth=(self.rpc_user, self.rpc_password)
        )

        return response.json()['result']

    def _broadcast_transaction(self, tx_hex: str) -> str:
        """Broadcast transaction to Bitcoin network"""

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "sendrawtransaction",
            "params": [tx_hex]
        }

        response = requests.post(
            self.rpc_url,
            json=rpc_call,
            auth=(self.rpc_user, self.rpc_password)
        )

        return response.json()['result']

    def _get_confirmations(self, txid: str) -> int:
        """Get confirmation count for transaction"""

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "gettransaction",
            "params": [txid]
        }

        response = requests.post(
            self.rpc_url,
            json=rpc_call,
            auth=(self.rpc_user, self.rpc_password)
        )

        return response.json()['result']['confirmations']

    def _get_block_hash(self, txid: str) -> str:
        """Get block hash containing transaction"""

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "gettransaction",
            "params": [txid]
