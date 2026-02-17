# HIPAA Compliance Patterns

## Six Drift Detection Checks

All checks run concurrently via `asyncio.gather()`:

| Check | HIPAA Control | Severity | Remediation |
|-------|---------------|----------|-------------|
| Patching | §164.308(a)(5)(ii)(B) | High | `apply_system_updates` |
| AV/EDR | §164.308(a)(5)(ii)(B) | Critical | `restart_av_service` |
| Backup | §164.308(a)(7)(ii)(A) | Critical | `run_backup_job` |
| Logging | §164.312(b) | High | `restart_logging_services` |
| Firewall | §164.312(a)(1) | Critical | `restore_firewall_rules` |
| Encryption | §164.312(a)(2)(iv) | Critical | `enable_volume_encryption` |

### Drift Result Structure
```python
@dataclass
class DriftResult:
    check: str                    # patching, backup, etc.
    drifted: bool                 # True if non-compliant
    severity: str                 # critical, high, medium, low
    pre_state: Dict[str, Any]     # Current system state
    recommended_action: str       # Runbook action
    hipaa_controls: List[str]     # Mapped controls
```

## Evidence Bundle Generation

### Bundle Structure
```python
@dataclass
class EvidenceBundle:
    bundle_id: str                # UUID
    site_id: str
    host_id: str

    # Timestamps
    timestamp_start: datetime
    timestamp_end: datetime

    # Provenance
    policy_version: str
    nixos_revision: str
    ntp_offset_ms: int

    # Compliance
    check: str
    hipaa_controls: List[str]
    framework_mappings: Dict      # HIPAA, SOC2, CIS

    # State capture
    pre_state: Dict[str, Any]
    post_state: Dict[str, Any]

    # Actions
    action_taken: List[ActionTaken]
    outcome: str                  # success, failed, reverted

    # Integrity
    bundle_hash: str              # SHA256
    signature: str                # Ed25519
```

### Generation Pipeline
```python
# 1. Create bundle
bundle = EvidenceGenerator.create_evidence(
    check="backup",
    outcome="success",
    pre_state={"last_backup": "2025-01-20"},
    post_state={"last_backup": "2025-01-22"},
    hipaa_controls=["164.308(a)(7)(ii)(A)"]
)

# 2. Sign with Ed25519
signature = signer.sign(json.dumps(bundle).encode())

# 3. Store locally + WORM
/var/lib/compliance-agent/evidence/2025/01/22/<bundle_id>/
├── bundle.json
├── bundle.sig
└── bundle.ots.json  # OpenTimestamps (optional)
```

## Runbook YAML Format

```yaml
id: RB-BACKUP-001
name: "Backup Failure Remediation"
severity: critical
hipaa_controls:
  - "164.308(a)(7)(ii)(A)"
  - "164.310(d)(2)(iv)"

preconditions:
  - action: check_disk_space
    params:
      path: /var/backups
      min_free_gb: 10
    on_failure: abort

steps:
  - action: capture_state
    description: "Pre-remediation state"
    command: "df -h /var/backups"
    evidence_key: disk_before

  - action: run_command
    description: "Restart backup service"
    command: "systemctl restart backup.service"
    timeout: 30

  - action: trigger_backup
    backup_type: "incremental"
    wait_for_completion: true
    timeout: 660
    evidence_key: backup_result

  - action: verify_backup
    command: "sha256sum /var/backups/latest.tar.gz"
    evidence_key: backup_hash

rollback:
  - action: send_alert
    severity: critical
    message: "Backup remediation failed"

evidence_required:
  - disk_before
  - backup_result
  - backup_hash
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
  runbook_id: RB-WIN-FIREWALL-001
  confidence: 0.95
```

### Operators
| Operator | Description |
|----------|-------------|
| `eq` | Equals |
| `ne` | Not equals |
| `contains` | String contains |
| `regex` | Regex match |
| `gt` / `lt` | Greater/less than |
| `in` / `not_in` | List membership |

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
```python
ALLOWED_PATHS = [
    "/var/log/*",
    "/etc/*",
    "/nix/store/*"
]

PROHIBITED_PATHS = [
    "/data/*",
    "/ehr/*",
    "/patient/*"
]
```

## HIPAA Control Mapping

```yaml
164.308(a)(1)(ii)(D):  # Info system review → audit logging
164.308(a)(5)(ii)(B):  # Patching & malware → drift checks
164.308(a)(7)(ii)(A):  # Data backup → backup verification
164.308(a)(8):         # Change management → drift detection
164.310(d)(2)(iv):     # Backup storage → WORM evidence
164.312(a)(1):         # Network controls → firewall check
164.312(a)(2)(iv):     # Encryption → LUKS/TLS checks
164.312(b):            # Audit controls → logging check
164.312(c)(1):         # Integrity → Ed25519 signing
164.312(e)(1):         # Transmission security → TLS 1.2+
```

## Multi-Framework Support

Evidence bundles auto-map to:
- **HIPAA** - Security Rule (§164)
- **SOC 2** - Trust Service Criteria
- **CIS** - Critical Security Controls
- **NIST 800-53** - Security Controls

### Mapping File
```yaml
# frameworks/mappings/control_mappings.yaml
patching:
  hipaa: ["164.308(a)(5)(ii)(B)"]
  soc2: ["CC6.1", "CC7.1"]
  cis: ["3.4", "3.5"]
  nist: ["SI-2", "CM-3"]

backup:
  hipaa: ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
  soc2: ["A1.2", "A1.3"]
  cis: ["10.1", "10.2"]
  nist: ["CP-9", "CP-10"]
```

## Flap Detection (auto_healer.py)

Prevents infinite heal/recur loops when remediation "succeeds" but drift recurs (GPO override, false positive, external attacker).

**Thresholds:** 3 successful-heal recurrences within 120 minutes triggers permanent suppression (persisted to SQLite `flap_suppressions` table, survives restarts).

**Only counts successful heals:** `_track_flap()` is called AFTER successful L1/L2 healing, not on every `heal()` call. Incidents without matching rules do NOT increment the flap counter. This prevents false suppression of unhealed drift.

**Granular keys:** Flap key includes runbook_id when available: `incident_type:runbook_id` (e.g., `ssh_config:LIN-SSH-002`). Prevents runbooks sharing the same `check_type` from cross-triggering each other's flap counters.

**Synced rules override built-in rules.** Rules at `/var/lib/msp/rules/l1_rules.json` (synced hourly from Central Command) take precedence over built-in rules in `level1_deterministic.py`. Rule action format: `run_linux_runbook:LIN-SSH-001` (colon format from synced rules).

## L1 Rule Coverage (level1_deterministic.py)

**Linux L1 rules (24 built-in):** SSH-001..004, KERN-001, CRON-001, SUID-001, FW-001, AUDIT-001, SVC-001..002, LOG-001, PERM-001, NET-001, BANNER-001, CRYPTO-001, IR-001, plus cross-platform rules (PATCH, AV, BACKUP, LOG, FW, AUDIT, ENCRYPT, CERT, DISK, SERVICE).

**Windows L1 rules (10 built-in):** AV-001, FW-001, SVC-DNS, SEC-SMB, SVC-WUAUSERV, NET-PROFILE, SEC-SCREENLOCK, SEC-DEFENDER-EXCL, plus cross-platform rules. All route to existing Windows runbooks via `run_windows_runbook`.

**Total built-in rules:** 38 (Linux-specific + Windows-specific + cross-platform).

**Windows scan checks (appliance_agent.py):** windows_defender, firewall_status, password_policy, bitlocker_status, audit_policy, service_w32time, service_dns, service_spooler, service_wuauserv, smb_signing, network_profile, screen_lock_policy, defender_exclusions, backup_status, scheduled_task_persistence, registry_run_persistence.

## Parallel Scan Cycles (appliance_agent.py)

Linux, Windows, network posture, and workstation scans run in parallel via `asyncio.gather()`. Cycle timeout is 600s. Each scan method checks its own interval internally.

## Key Files
- `compliance_agent/drift.py` - 6 drift checks
- `compliance_agent/evidence.py` - Bundle generation
- `compliance_agent/phi_scrubber.py` - PHI patterns
- `compliance_agent/level1_deterministic.py` - Rule engine
- `compliance_agent/auto_healer.py` - Healing orchestrator + flap detection
- `compliance_agent/incident_db.py` - SQLite incident/flap tracking
- `/var/lib/msp/rules/l1_rules.json` - Synced L1 rules (override built-in)
- `baseline/hipaa-v1.yaml` - Compliance baseline
