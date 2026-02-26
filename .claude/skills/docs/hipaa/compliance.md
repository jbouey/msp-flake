# HIPAA Compliance Patterns

## Six Drift Detection Checks

All checks run concurrently via `asyncio.gather()`:

| Check | HIPAA Controls | Severity | Remediation |
|-------|---------------|----------|-------------|
| Patching | §164.308(a)(5)(ii)(B) | High | `apply_system_updates` |
| AV/EDR | §164.308(a)(5)(ii)(B), §164.312(b) | Critical | `restart_av_service` |
| Backup | §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv) | Critical | `run_backup_job` |
| Logging | §164.312(b), §164.308(a)(1)(ii)(D) | High | `restart_logging_services` |
| Firewall | §164.312(a)(1), §164.312(e)(1) | Critical | `restore_firewall_rules` |
| Encryption | §164.312(a)(2)(iv), §164.312(e)(2)(ii) | Critical | `enable_volume_encryption` |

### Drift Result Structure
```python
# Pydantic BaseModel in compliance_agent/models.py
class DriftResult(BaseModel):
    check: str                                    # patching, backup, etc.
    drifted: bool                                 # True if non-compliant
    severity: Literal["low","medium","high","critical"]
    pre_state: Dict[str, Any]                     # Current system state
    recommended_action: Optional[str]             # Runbook action
    hipaa_controls: Optional[List[str]]           # Mapped controls
```

## Evidence Bundle Generation

### Bundle Structure
```python
# Pydantic BaseModel in compliance_agent/models.py (40+ fields)
class EvidenceBundle(BaseModel):
    bundle_id: str                # UUID v4
    site_id: str
    host_id: str

    # Timestamps
    timestamp_start: datetime
    timestamp_end: datetime

    # Provenance
    policy_version: str
    nixos_revision: str
    ntp_offset_ms: int
    ntp_verification: Dict        # Multi-source NTP verification
    derivation_digest: str        # SHA256 of NixOS derivation
    ruleset_hash: str             # SHA256 of compliance ruleset

    # Compliance
    check: str
    hipaa_controls: List[str]     # Legacy single-framework
    framework_mappings: Dict      # Multi-framework: HIPAA, SOC2, CIS, NIST

    # State capture
    pre_state: Dict[str, Any]
    post_state: Dict[str, Any]

    # Actions
    action_taken: List[ActionTaken]
    outcome: Literal["success", "failed", "reverted", "deferred", "alert", "rejected", "expired"]
    rollback_available: bool
    rollback_generation: Optional[int]

    # Metadata
    version: str = "1.0"
    deployment_mode: Literal["reseller", "direct"]
    order_id: Optional[str]       # MCP order trigger
    runbook_id: Optional[str]

# Note: bundle_hash and Ed25519 signature are NOT model fields.
# Signing happens at storage time — stored as separate files:
#   bundle.json, bundle.sig, bundle.ots.json
```

### Generation Pipeline
```python
# 1. Create bundle (evidence.py — also accepts enabled_frameworks, check_id)
bundle = EvidenceGenerator.create_evidence(
    check="backup",
    outcome="success",
    pre_state={"last_backup": "2025-01-20"},
    post_state={"last_backup": "2025-01-22"},
    hipaa_controls=["164.308(a)(7)(ii)(A)"]
)

# 2. Store + sign + optional OTS + optional WORM upload
bundle_path, sig_path, worm_uri, ots_status = await generator.store_evidence(
    bundle, sign=True
)
# Ed25519 signature stored as detached bundle.sig

# 3. On-disk layout
/var/lib/compliance-agent/evidence/2025/01/22/<bundle_id>/
├── bundle.json       # Full evidence payload
├── bundle.sig        # Ed25519 detached signature
└── bundle.ots.json   # OpenTimestamps proof (optional)
# Also uploaded to S3 with Object Lock (WORM) if configured
```

## Runbook YAML Format

```yaml
id: RB-BACKUP-001
name: "Backup Failure Remediation"
version: "2.0"
severity: critical
hipaa_controls:
  - "164.308(a)(7)(ii)(A)"
  - "164.310(d)(2)(iv)"

constraints:
  max_retries: 2
  retry_delay_seconds: 30
  requires_maintenance_window: false

preconditions:
  - action: check_disk_space
    description: "Ensure sufficient disk space"
    params:
      path: /var/backups
      min_free_gb: 10
    on_failure: abort

steps:
  - action: capture_state
    description: "Pre-remediation state"
    params:
      command: "df -h /var/backups"
    timeout: 15
    evidence_key: disk_usage_before

  - action: run_command
    description: "Restart backup service"
    params:
      command: "systemctl restart backup.service"
    timeout: 30
    continue_on_failure: true

  - action: trigger_backup
    params:
      backup_type: "incremental"
      wait_for_completion: true
    timeout: 660
    evidence_key: backup_job_result

rollback:
  - action: run_command
    description: "Log failure"
    params:
      command: logger
      args: ["-t", "msp-healer", "-p", "err", "Backup remediation failed"]

  - action: send_alert
    params:
      severity: critical
      message: "Backup remediation failed on {{host_id}}"
      hipaa_control: "164.308(a)(7)(ii)(A)"

evidence_required:
  - disk_usage_before
  - backup_job_result
  - backup_verification
  - backup_completion_hash
```

## L1 Deterministic Rules

### Rule Format
```yaml
- id: L1-FIREWALL-001
  conditions:
    - field: check_type
      operator: eq
      value: firewall_status
    - field: platform
      operator: ne
      value: nixos
    - field: status
      operator: eq
      value: fail
  action: execute_runbook
  action_params:
    runbook_id: RB-WIN-FIREWALL-001
  severity_filter: null          # Optional severity constraint
  cooldown_seconds: 0            # Min time between re-executions
  gpo_managed: false             # Skip flap tracking if GPO-managed
```

### Operators (MatchOperator enum)
| Operator | Description |
|----------|-------------|
| `eq` | Equals |
| `ne` | Not equals |
| `contains` | String contains |
| `regex` | Regex match |
| `gt` / `lt` | Greater/less than |
| `in` / `not_in` | List membership |
| `exists` | Field exists (truthy check) |

### Nested Field Access
```yaml
# Access nested fields with dot notation
- field: pre_state.disk_usage
  operator: gt
  value: 90
```

## PHI Scrubber

### 14 Detection Patterns
```python
PHI_PATTERNS = {
    'ssn': r'\b\d{3}-\d{2}-\d{4}\b',
    'mrn': r'\bMRN\s*[:#]?\s*\d+\b',
    'patient_id': r'\bpatient[_-]?id\s*[=:]\s*\w+',
    'phone': r'\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
    'email': r'\b[\w.-]+@[\w.-]+\.\w+\b',
    'credit_card': r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
    'dob': r'\bDOB\s*[=:]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',
    'address': r'\b\d+\s+\w+\s+(St|Ave|Blvd|Dr|Rd|Ln|Way)\b',
    'zip': r'\b\d{5}(-\d{4})?\b',
    'ip_address': r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
    'account_number': r'\baccount\s*#?\s*\d{6,}\b',
    'insurance_id': r'\b(ins|insurance)\s*(id|#)\s*[:=]?\s*\w+',
    'medicare': r'\b\d{1}[A-Z]{1,2}\d{1,4}[A-Z]?\d?\b',
    'drivers_license': r'\b[A-Z]{1,2}\d{5,8}\b',
}
```

### Usage
```python
scrubber = PHIScrubber(
    patterns=['ssn', 'mrn', 'patient_id', 'email'],
    hash_redacted=True  # [SSN-REDACTED-a1b2c3]
)

scrubbed, result = scrubber.scrub(log_line)
# result.patterns_matched: 3
# result.phi_scrubbed: True
```

## Data Boundary Zones

### Zone 1: SYSTEM (Allowed)
- syslog, journald, auditd
- Package hashes, service status
- PHI Risk: Very low

### Zone 2: APPLICATION (Scrubbed)
- EHR audit events, access logs
- PHI Risk: Moderate
- Mitigation: Tokenize IDs, redact payload

### Zone 3: DATA (Prohibited)
- Patient demographics, clinical notes
- Never collected or stored

### Access Restrictions

Boundary enforcement is implemented at the collection layer (sensor + PHI scrubber), not via explicit path allowlists. The agent only collects structured metadata from system sources — no direct file content access.

**Allowed sources:** syslog, journald, auditd, systemctl status, package hashes, nftables/iptables rulesets, LUKS status
**Prohibited:** EHR data mounts, patient directories, clinical databases — never accessed by sensors

## HIPAA Control Mapping

```yaml
# Administrative Safeguards (§164.308)
164.308(a)(1)(ii)(B):  # Risk assessment → device_inventory check
164.308(a)(1)(ii)(D):  # Info system review → audit logging, logging drift
164.308(a)(5)(ii)(B):  # Patching & malware → patching + AV/EDR drift
164.308(a)(7)(ii)(A):  # Data backup → backup verification drift
164.308(a)(8):         # Change management → drift detection

# Physical Safeguards (§164.310)
164.310(d)(2)(iv):     # Backup storage → WORM evidence

# Technical Safeguards (§164.312)
164.312(a)(1):         # Access control → firewall drift, prohibited_ports, database_exposure, rdp_exposure
164.312(a)(2)(i):      # Unique user ID → snmp_security check
164.312(a)(2)(iv):     # Encryption → LUKS drift, tls_web_services check
164.312(b):            # Audit controls → logging drift, AV/EDR drift
164.312(c)(1):         # Integrity → Ed25519 signing
164.312(e)(1):         # Transmission security → encrypted_services check, firewall drift
164.312(e)(2)(ii):     # Encryption mechanism → encryption drift
```

## Multi-Framework Support

Evidence bundles auto-map to:
- **HIPAA** - Security Rule (§164)
- **SOC 2** - Trust Service Criteria
- **CIS** - Critical Security Controls
- **NIST 800-53** - Security Controls

### Mapping File
```yaml
# frameworks/mappings/control_mappings.yaml (CIS v8 numbering)
patching:
  hipaa: ["164.308(a)(5)(ii)(B)"]
  soc2: ["CC6.1", "CC7.1"]
  cis: ["CIS.7.1"]              # Vulnerability Management Process
  nist_csf: ["PR.IP-1", "PR.IP-12"]

backup:
  hipaa: ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
  soc2: ["A1.2", "A1.3"]
  cis: ["CIS.11.1"]             # Data Recovery Process
  nist_csf: ["PR.IP-4", "RC.RP-1"]
```

## Flap Detection (auto_healer.py)

Prevents infinite heal/recur loops when remediation "succeeds" but drift recurs (GPO override, false positive, external attacker).

**Thresholds:** 3 successful-heal recurrences within 120 minutes triggers permanent suppression (persisted to SQLite `flap_suppressions` table, survives restarts).

**Only counts successful heals:** `_track_flap()` is called AFTER successful L1/L2 healing, not on every `heal()` call. Incidents without matching rules do NOT increment the flap counter. This prevents false suppression of unhealed drift.

**Granular keys:** Flap key includes runbook_id when available: `incident_type:runbook_id` (e.g., `ssh_config:LIN-SSH-002`). Prevents runbooks sharing the same `check_type` from cross-triggering each other's flap counters.

**Synced rules override built-in rules.** Rules at `/var/lib/msp/rules/l1_rules.json` (synced hourly from Central Command) take precedence over built-in rules in `level1_deterministic.py`. Rule action format: `run_linux_runbook:LIN-SSH-001` (colon format from synced rules).

## L1 Rule Coverage (level1_deterministic.py)

**Cross-platform rules (12 built-in):** L1-PATCH-001, L1-AV-001, L1-BACKUP-001..002, L1-LOG-001, L1-FW-001..002, L1-AUDIT-002, L1-ENCRYPT-001, L1-CERT-001, L1-DISK-001, L1-SERVICE-001.

**Linux L1 rules (13 built-in):** L1-SSH-001, L1-KERN-001, L1-CRON-001, L1-SUID-001, L1-LIN-FW-001, L1-LIN-AUDIT-001, L1-LIN-SVC-001, L1-LIN-LOG-001, L1-LIN-PERM-001, L1-LIN-NET-001, L1-LIN-BANNER-001, L1-LIN-CRYPTO-001, L1-LIN-IR-001.

**Windows L1 rules (13 built-in):** L1-WIN-SVC-DNS, L1-WIN-SEC-SMB, L1-WIN-SVC-WUAUSERV, L1-WIN-NET-PROFILE, L1-WIN-SEC-SCREENLOCK, L1-WIN-SEC-BITLOCKER, L1-WIN-SVC-NETLOGON, L1-WIN-DNS-HIJACK, L1-WIN-SEC-DEFENDER-EXCL, L1-PERSIST-TASK-001, L1-PERSIST-REG-001, L1-WIN-SEC-SMB1, L1-PERSIST-WMI-001. GPO-managed rules skip flap tracking.

**Total built-in rules:** 38 (12 cross-platform + 13 Linux + 13 Windows).

**Windows scan checks (workstation_checks.py, 12 checks):** check_bitlocker, check_defender, check_patches, check_firewall, check_screen_lock, check_audit_policy, check_account_lockout, check_smb_signing, check_guest_account, check_firewall_logging, check_scheduled_tasks, check_registry_persistence.

## Parallel Scan Cycles (appliance_agent.py)

Linux, Windows, network posture, and workstation scans run in parallel via `asyncio.gather()`. Cycle timeout is 600s. Each scan method checks its own interval internally.

## Network Compliance Checks (7 checks)

Port-based checks run after each nmap scan cycle. Results stored in local SQLite `device_compliance` table, synced to Central Command `device_compliance_details` (migration 060).

| Check Class | check_type | HIPAA Control | Applies To | Logic |
|---|---|---|---|---|
| `ProhibitedPortsCheck` | `prohibited_ports` | §164.312(a)(1) | All | FTP(21), Telnet(23), TFTP(69), rsh(512-514) → fail |
| `EncryptedServicesCheck` | `encrypted_services` | §164.312(e)(1) | WS/Server/Unknown | HTTP(80) w/o HTTPS(443) → fail; both → warn |
| `TLSWebServicesCheck` | `tls_web_services` | §164.312(a)(2)(iv) | Server | 8080 w/o 8443 → warn |
| `DatabaseExposureCheck` | `database_exposure` | §164.312(a)(1) | Non-servers | MySQL/PG/MSSQL/Mongo/Redis/Cassandra → fail |
| `SNMPSecurityCheck` | `snmp_security` | §164.312(a)(2)(i) | All | SNMP 161/162 → warn |
| `RDPExposureCheck` | `rdp_exposure` | §164.312(a)(1) | Non-workstations | RDP(3389) on server/network/printer → warn |
| `DeviceInventoryCheck` | `device_inventory` | §164.308(a)(1)(ii)(B) | All | No ports (ARP-only) → warn; has ports → pass |

### Data Flow
```
Nmap scan → ports in SQLite → compliance runner (7 checks per device)
→ device_compliance table → compliance_status updated → auto-sync to Central Command
→ dashboard Device Inventory shows compliance rate + per-device drill-down
```

### Key Files (Network Scanner)
- `network_scanner/compliance/network_checks.py` - 7 check classes
- `network_scanner/compliance/runner.py` - Check orchestrator
- `network_scanner/compliance/base.py` - ComplianceCheck ABC + ComplianceResult
- `network_scanner/scanner_service.py` - Wires runner after scan (line ~302)
- `network_scanner/device_db.py` - store_compliance_results() + get_devices_for_scanning()

## Key Files
- `compliance_agent/drift.py` - 6 drift checks (asyncio.gather)
- `compliance_agent/evidence.py` - Bundle generation + Ed25519 signing + WORM upload + OTS
- `compliance_agent/phi_scrubber.py` - 14 PHI patterns + ScrubResult
- `compliance_agent/crypto.py` - Ed25519Signer class
- `compliance_agent/level1_deterministic.py` - Rule engine (38 built-in rules, 9 operators)
- `compliance_agent/auto_healer.py` - L1/L2/L3 orchestrator + flap detection
- `compliance_agent/incident_db.py` - SQLite incident/flap tracking (flap_suppressions table)
- `compliance_agent/workstation_checks.py` - 12 Windows compliance checks via WinRM/PowerShell
- `compliance_agent/learning_loop.py` - L2-to-L1 promotion engine
- `compliance_agent/models.py` - DriftResult, EvidenceBundle (Pydantic BaseModels)
- `/etc/msp/rules/*.json` - Synced L1 rules (override built-in, hourly from Central Command)
- `/etc/msp/rules/promoted/` - Auto-promoted rules from learning loop
- `baseline/hipaa-v1.yaml` - Compliance baseline

## Resilience & Offline Operation

Go appliance daemon (`appliance/`) resilience features:

| Feature | Implementation | HIPAA Relevance |
|---------|---------------|-----------------|
| Crash-loop protection | `StartLimitBurst=5/IntervalSec=300` on 3 services | Prevents runaway restarts from exhausting system |
| Systemd watchdog | `WatchdogSec=120s`, `sd_notify WATCHDOG=1` each cycle | Detects frozen daemon, auto-restarts |
| sd_notify integration | `appliance/internal/sdnotify/sdnotify.go` (zero-cgo) | Type=notify ensures systemd knows daemon is ready |
| State persistence | `/var/lib/msp/daemon_state.json` (atomic write) | Linux targets, L2 mode, subscription survive restarts |
| Subscription gating | `isSubscriptionActive()` — active/trialing only | Healing suppressed on expiry; drift detection+evidence continue |
| Connectivity classification | `classifyConnectivityError()` — dns/server_down/timeout | Better diagnostics for offline operation |
| Rebuild watchdog | `nixos-rebuild test` first, `switch` only on success | 10-minute rollback on failure |
| Evidence queue | SQLite WAL, 10 retries, exponential backoff | Offline evidence survives network outages |
