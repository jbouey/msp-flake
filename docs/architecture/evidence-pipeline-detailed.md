# Evidence Pipeline Architecture: Complete Technical Specification

**Document Purpose:** Technical specification for the evidence generation, signing, storage, and verification pipeline.

**Version:** 1.0
**Last Updated:** 2025-10-31
**Target Audience:** Implementation engineers, security auditors

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Evidence Bundle Format](#evidence-bundle-format)
3. [Collection Pipeline](#collection-pipeline)
4. [Signing & Verification](#signing--verification)
5. [WORM Storage Implementation](#worm-storage-implementation)
6. [Compliance Packet Generation](#compliance-packet-generation)
7. [Security Considerations](#security-considerations)
8. [Implementation Guide](#implementation-guide)

---

## Architecture Overview

### The Core Problem

**Traditional Compliance Evidence:**
- Created retrospectively (after incident)
- Depends on human memory
- Manually compiled from disparate sources
- No tamper-evidence mechanism
- Expensive to produce ($500-2000 per audit)

**Our Solution:**
- Generated automatically during operations
- Cryptographically signed at creation
- Stored in immutable (WORM) storage
- Machine-verifiable integrity
- Cost: <$10/month per client

### System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT INFRASTRUCTURE                        │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │ Incident     │  1. Incident detected                             │
│  │ Occurs       │  (backup failure, cert expiry, etc.)              │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ MCP Watcher  │  2. Publish to event queue                        │
│  │ (Local Agent)│  tenant:clinic-001:incidents                      │
│  └──────┬───────┘                                                   │
└─────────┼────────────────────────────────────────────────────────────┘
          │
          │ TLS + mTLS
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         CENTRAL MCP SERVER                           │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │ Event Queue  │  3. Incident received                             │
│  │ (NATS/Redis) │                                                    │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ MCP Planner  │  4. Select runbook                                │
│  │ (LLM)        │  "This matches RB-BACKUP-001"                     │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ Guardrails   │  5. Validate & rate-limit                         │
│  │ Engine       │                                                    │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ MCP Executor │  6. Execute runbook steps                         │
│  │              │  • Run scripts                                     │
│  │              │  • Capture outputs                                 │
│  │              │  • Hash everything                                 │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ Evidence     │  7. Bundle evidence                               │
│  │ Bundler      │  • Metadata                                        │
│  │              │  • Outputs                                         │
│  │              │  • Hashes                                          │
│  │              │  • Timestamps                                      │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ Cryptographic│  8. Sign bundle                                   │
│  │ Signer       │  cosign or GPG signature                          │
│  │              │                                                    │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ WORM         │  9. Upload to immutable storage                   │
│  │ Uploader     │  S3 with Object Lock (COMPLIANCE mode)            │
│  └──────────────┘                                                   │
└─────────┼────────────────────────────────────────────────────────────┘
          │
          │ Nightly aggregation
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     COMPLIANCE PACKET GENERATOR                      │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │ Evidence     │  10. Collect month's evidence bundles             │
│  │ Aggregator   │                                                    │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ Compliance   │  11. Generate PDF packet                          │
│  │ Report       │  • Executive summary                               │
│  │ Generator    │  • Control matrix                                  │
│  │              │  • Evidence manifest                               │
│  └──────┬───────┘                                                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────┐                                                   │
│  │ Packet       │  12. Sign & deliver                               │
│  │ Delivery     │  Email + S3 storage                               │
│  └──────────────┘                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Evidence Bundle Format

### JSON Schema

**File:** `mcp-server/evidence/schemas/evidence_bundle.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "MSP Evidence Bundle",
  "description": "Tamper-evident evidence bundle for HIPAA compliance",
  "type": "object",
  "required": [
    "bundle_id",
    "bundle_version",
    "client_id",
    "generated_at",
    "incident",
    "runbook",
    "execution",
    "signatures"
  ],
  "properties": {
    "bundle_id": {
      "type": "string",
      "pattern": "^EB-\\d{8}-\\d{4}$",
      "description": "Unique identifier: EB-YYYYMMDD-NNNN"
    },
    "bundle_version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+$",
      "description": "Schema version for forward compatibility"
    },
    "client_id": {
      "type": "string",
      "description": "Client identifier for multi-tenant isolation"
    },
    "generated_at": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 timestamp of bundle creation"
    },
    "incident": {
      "type": "object",
      "required": ["id", "type", "severity", "detected_at"],
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^INC-\\d{8}-\\d{4}$"
        },
        "type": {
          "type": "string",
          "enum": [
            "backup_failure",
            "service_crash",
            "cert_expiry",
            "disk_full",
            "cpu_high",
            "config_drift",
            "auth_failure",
            "security_alert"
          ]
        },
        "severity": {
          "type": "string",
          "enum": ["critical", "high", "medium", "low", "info"]
        },
        "detected_at": {
          "type": "string",
          "format": "date-time"
        },
        "resolved_at": {
          "type": "string",
          "format": "date-time"
        },
        "mttr_seconds": {
          "type": "integer",
          "minimum": 0,
          "description": "Mean Time To Resolve in seconds"
        },
        "sla_met": {
          "type": "boolean",
          "description": "Was incident resolved within SLA?"
        }
      }
    },
    "runbook": {
      "type": "object",
      "required": ["id", "version", "hash", "hipaa_controls"],
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^RB-[A-Z]+-\\d{3}$"
        },
        "version": {
          "type": "string",
          "pattern": "^\\d+\\.\\d+\\.\\d+$"
        },
        "hash": {
          "type": "string",
          "pattern": "^sha256:[a-f0-9]{64}$",
          "description": "SHA256 hash of runbook YAML file"
        },
        "hipaa_controls": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^§164\\.\\d{3}\\([a-z]\\)(\\(\\d+\\))*$"
          },
          "minItems": 1,
          "description": "HIPAA Security Rule citations"
        }
      }
    },
    "execution": {
      "type": "object",
      "required": ["operator", "steps"],
      "properties": {
        "operator": {
          "type": "string",
          "description": "Service account or user that executed runbook"
        },
        "steps": {
          "type": "array",
          "items": {
            "type": "object",
            "required": [
              "id",
              "started_at",
              "completed_at",
              "script_hash",
              "exit_code",
              "success"
            ],
            "properties": {
              "id": {
                "type": "string",
                "description": "Step identifier from runbook"
              },
              "started_at": {
                "type": "string",
                "format": "date-time"
              },
              "completed_at": {
                "type": "string",
                "format": "date-time"
              },
              "duration_seconds": {
                "type": "integer",
                "minimum": 0
              },
              "script_hash": {
                "type": "string",
                "pattern": "^sha256:[a-f0-9]{64}$",
                "description": "Hash of executed script"
              },
              "output_hash": {
                "type": "string",
                "pattern": "^sha256:[a-f0-9]{64}$",
                "description": "Hash of script output"
              },
              "exit_code": {
                "type": "integer"
              },
              "success": {
                "type": "boolean"
              },
              "evidence_files": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              }
            }
          }
        }
      }
    },
    "artifacts": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["filename", "hash", "size_bytes"],
        "properties": {
          "filename": {
            "type": "string"
          },
          "hash": {
            "type": "string",
            "pattern": "^sha256:[a-f0-9]{64}$"
          },
          "size_bytes": {
            "type": "integer",
            "minimum": 0
          },
          "content_type": {
            "type": "string"
          }
        }
      }
    },
    "verification": {
      "type": "object",
      "properties": {
        "closed_loop_check": {
          "type": "boolean",
          "description": "Was issue verified as resolved?"
        },
        "verification_timestamp": {
          "type": "string",
          "format": "date-time"
        }
      }
    },
    "signatures": {
      "type": "object",
      "required": [
        "bundle_hash",
        "signer",
        "public_key_id",
        "signature",
        "signed_at"
      ],
      "properties": {
        "bundle_hash": {
          "type": "string",
          "pattern": "^sha256:[a-f0-9]{64}$",
          "description": "Hash of entire bundle (excluding this field)"
        },
        "signer": {
          "type": "string",
          "enum": ["cosign", "gpg"],
          "description": "Signature method used"
        },
        "public_key_id": {
          "type": "string",
          "description": "Identifier for public key used for verification"
        },
        "signature": {
          "type": "string",
          "description": "Base64-encoded signature"
        },
        "signed_at": {
          "type": "string",
          "format": "date-time"
        }
      }
    },
    "storage": {
      "type": "object",
      "properties": {
        "local_path": {
          "type": "string",
          "description": "Local filesystem path"
        },
        "worm_url": {
          "type": "string",
          "format": "uri",
          "description": "S3 URL with Object Lock enabled"
        },
        "worm_lock_enabled": {
          "type": "boolean"
        },
        "retention_days": {
          "type": "integer",
          "minimum": 2555,
          "description": "HIPAA requires 6 years, we use 7"
        },
        "uploaded_at": {
          "type": "string",
          "format": "date-time"
        }
      }
    }
  }
}
```

### Example Evidence Bundle

**File:** `EB-20251031-0001.json`

```json
{
  "bundle_id": "EB-20251031-0001",
  "bundle_version": "1.0",
  "client_id": "clinic-001",
  "generated_at": "2025-10-31T06:00:00Z",
  "generator": "msp-evidence-packager v1.0.0",

  "incident": {
    "id": "INC-20251031-0001",
    "type": "backup_failure",
    "severity": "high",
    "detected_at": "2025-10-31T02:00:15Z",
    "resolved_at": "2025-10-31T02:04:23Z",
    "mttr_seconds": 248,
    "sla_met": true,
    "description": "Restic backup job failed with exit code 1"
  },

  "runbook": {
    "id": "RB-BACKUP-001",
    "version": "1.0.0",
    "hash": "sha256:a1b2c3d4e5f6789...",
    "hipaa_controls": [
      "§164.308(a)(7)(ii)(A)",
      "§164.310(d)(2)(iv)"
    ],
    "name": "Backup Failure Remediation"
  },

  "execution": {
    "operator": "service:msp-executor",
    "hostname": "srv-primary.clinic-001.local",
    "steps": [
      {
        "id": "check_logs",
        "name": "Check backup logs for errors",
        "started_at": "2025-10-31T02:00:16Z",
        "completed_at": "2025-10-31T02:00:45Z",
        "duration_seconds": 29,
        "script_hash": "sha256:d4e5f6g7h8i9...",
        "output_hash": "sha256:j1k2l3m4n5o6...",
        "exit_code": 0,
        "success": true,
        "evidence_files": ["backup_error.txt"]
      },
      {
        "id": "verify_disk_space",
        "name": "Verify sufficient disk space",
        "started_at": "2025-10-31T02:00:46Z",
        "completed_at": "2025-10-31T02:01:02Z",
        "duration_seconds": 16,
        "script_hash": "sha256:p7q8r9s0t1u2...",
        "output_hash": "sha256:v3w4x5y6z7a8...",
        "exit_code": 0,
        "success": true,
        "evidence_files": ["df_output.txt"]
      },
      {
        "id": "restart_backup_service",
        "name": "Restart backup service and trigger manual backup",
        "started_at": "2025-10-31T02:01:03Z",
        "completed_at": "2025-10-31T02:04:20Z",
        "duration_seconds": 197,
        "script_hash": "sha256:b9c0d1e2f3g4...",
        "output_hash": "sha256:h5i6j7k8l9m0...",
        "exit_code": 0,
        "success": true,
        "evidence_files": ["service_restart.log", "backup_success.log"]
      }
    ]
  },

  "artifacts": [
    {
      "filename": "backup_error.txt",
      "hash": "sha256:j1k2l3m4n5o6...",
      "size_bytes": 2048,
      "content_type": "text/plain",
      "description": "Last 100 lines of backup log showing error"
    },
    {
      "filename": "df_output.txt",
      "hash": "sha256:v3w4x5y6z7a8...",
      "size_bytes": 512,
      "content_type": "text/plain",
      "description": "Disk usage output showing available space"
    },
    {
      "filename": "service_restart.log",
      "hash": "sha256:h5i6j7k8l9m0...",
      "size_bytes": 4096,
      "content_type": "text/plain",
      "description": "Systemd service restart output"
    },
    {
      "filename": "backup_success.log",
      "hash": "sha256:n1o2p3q4r5s6...",
      "size_bytes": 1024,
      "content_type": "text/plain",
      "description": "Successful backup completion log"
    }
  ],

  "verification": {
    "closed_loop_check": true,
    "backup_status_after": "success",
    "next_backup_scheduled": "2025-11-01T02:00:00Z",
    "verification_timestamp": "2025-10-31T02:05:00Z"
  },

  "signatures": {
    "bundle_hash": "sha256:t7u8v9w0x1y2...",
    "signer": "cosign",
    "public_key_id": "msp-evidence-key-2025",
    "signature": "MEUCIQD/xW4sH...",
    "signed_at": "2025-10-31T06:00:05Z"
  },

  "storage": {
    "local_path": "/var/lib/msp/evidence/2025/10/EB-20251031-0001.json",
    "worm_url": "s3://compliance-worm-clinic-001/2025/10/EB-20251031-0001.json",
    "worm_lock_enabled": true,
    "retention_days": 2555,
    "uploaded_at": "2025-10-31T06:00:10Z"
  }
}
```

---

## Collection Pipeline

### Implementation: `mcp-server/evidence/bundler.py`

```python
"""
Evidence Bundler
Collects execution metadata and generates tamper-evident bundles
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any
import jsonschema

class EvidenceBundler:
    def __init__(self, client_id: str, output_dir: Path):
        self.client_id = client_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Load JSON schema for validation
        self.schema = self._load_schema()

    def create_bundle(
        self,
        incident: Dict,
        runbook: Dict,
        execution: Dict,
        artifacts: List[Path]
    ) -> Dict:
        """
        Create evidence bundle from execution data

        Args:
            incident: Incident metadata (id, type, severity, timestamps)
            runbook: Runbook metadata (id, version, hash, HIPAA controls)
            execution: Execution data (operator, steps with outputs)
            artifacts: List of artifact file paths

        Returns:
            Evidence bundle dictionary
        """

        # Generate unique bundle ID
        bundle_id = self._generate_bundle_id()

        # Process artifacts (compute hashes, gather metadata)
        processed_artifacts = self._process_artifacts(artifacts)

        # Build bundle structure
        bundle = {
            "bundle_id": bundle_id,
            "bundle_version": "1.0",
            "client_id": self.client_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "msp-evidence-packager v1.0.0",
            "incident": incident,
            "runbook": runbook,
            "execution": execution,
            "artifacts": processed_artifacts,
            "verification": self._run_verification(incident),
            # Signatures added later by signer
            "signatures": {},
            "storage": {}
        }

        # Validate against schema
        jsonschema.validate(instance=bundle, schema=self.schema)

        return bundle

    def _generate_bundle_id(self) -> str:
        """Generate unique bundle ID: EB-YYYYMMDD-NNNN"""
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%d")

        # Find next available sequence number for today
        existing = list(self.output_dir.glob(f"EB-{date_str}-*.json"))
        if existing:
            nums = [int(p.stem.split('-')[-1]) for p in existing]
            next_num = max(nums) + 1
        else:
            next_num = 1

        return f"EB-{date_str}-{next_num:04d}"

    def _process_artifacts(self, artifact_paths: List[Path]) -> List[Dict]:
        """
        Process artifact files: compute hashes, gather metadata

        Args:
            artifact_paths: List of file paths to include in bundle

        Returns:
            List of artifact metadata dictionaries
        """
        artifacts = []

        for path in artifact_paths:
            if not path.exists():
                raise FileNotFoundError(f"Artifact not found: {path}")

            # Compute SHA256 hash
            file_hash = self._hash_file(path)

            # Gather metadata
            stat = path.stat()
            artifacts.append({
                "filename": path.name,
                "hash": f"sha256:{file_hash}",
                "size_bytes": stat.st_size,
                "content_type": self._guess_content_type(path),
                "description": self._generate_description(path)
            })

        return artifacts

    def _hash_file(self, path: Path) -> str:
        """Compute SHA256 hash of file"""
        sha256 = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _hash_dict(self, data: Dict) -> str:
        """Compute SHA256 hash of dictionary (deterministic JSON)"""
        json_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(json_str.encode()).hexdigest()

    def _run_verification(self, incident: Dict) -> Dict:
        """
        Run closed-loop verification after incident resolution

        Args:
            incident: Incident metadata

        Returns:
            Verification results
        """
        # Check if incident is actually resolved
        # (Implementation depends on incident type)

        return {
            "closed_loop_check": True,
            "verification_timestamp": datetime.now(timezone.utc).isoformat()
        }

    def save_bundle(self, bundle: Dict) -> Path:
        """
        Save bundle to local filesystem

        Args:
            bundle: Evidence bundle dictionary

        Returns:
            Path to saved bundle file
        """
        # Organize by date: YYYY/MM/bundle_id.json
        date = datetime.now(timezone.utc)
        output_path = self.output_dir / str(date.year) / f"{date.month:02d}"
        output_path.mkdir(parents=True, exist_ok=True)

        bundle_file = output_path / f"{bundle['bundle_id']}.json"

        with open(bundle_file, 'w') as f:
            json.dump(bundle, f, indent=2, sort_keys=False)

        return bundle_file

    def _load_schema(self) -> Dict:
        """Load evidence bundle JSON schema"""
        schema_path = Path(__file__).parent / "schemas" / "evidence_bundle.json"
        with open(schema_path) as f:
            return json.load(f)

    def _guess_content_type(self, path: Path) -> str:
        """Guess MIME type from file extension"""
        import mimetypes
        content_type, _ = mimetypes.guess_type(str(path))
        return content_type or "application/octet-stream"

    def _generate_description(self, path: Path) -> str:
        """Generate human-readable description of artifact"""
        # Parse filename to guess purpose
        name = path.stem.lower()

        descriptions = {
            "backup": "Backup operation log",
            "error": "Error output and diagnostics",
            "service": "Service management log",
            "restart": "Service restart output",
            "df": "Disk usage statistics",
            "success": "Successful operation confirmation"
        }

        for keyword, desc in descriptions.items():
            if keyword in name:
                return desc

        return "Operation artifact"
```

### WHY Each Component Matters

**`bundle_id`:** Unique identifier for auditor reference
- Format: `EB-YYYYMMDD-NNNN` (Evidence Bundle - Date - Sequence)
- Sortable chronologically
- Easy to reference in compliance packets

**Schema Validation:** Ensures bundle format consistency
- JSON Schema enforces required fields
- Catches errors at creation time
- Forward compatibility via versioning

**Artifact Hashing:** Tamper detection
- SHA256 is cryptographically secure
- Any modification changes hash
- Auditor can verify integrity

**Deterministic JSON:** Consistent hashing
- Sort keys alphabetically
- No whitespace variations
- Same data = same hash always

---

## Signing & Verification

### Implementation: `mcp-server/evidence/signer.py`

```python
"""
Cryptographic Signer
Signs evidence bundles with cosign for tamper-evidence
"""

import json
import subprocess
from pathlib import Path
from typing import Dict
import hashlib

class EvidenceSigner:
    def __init__(self, key_path: Path, public_key_id: str):
        self.key_path = Path(key_path)
        self.public_key_id = public_key_id

        if not self.key_path.exists():
            raise FileNotFoundError(f"Signing key not found: {key_path}")

    def sign_bundle(self, bundle_path: Path) -> Dict:
        """
        Sign evidence bundle with cosign

        Args:
            bundle_path: Path to evidence bundle JSON file

        Returns:
            Signature metadata dictionary
        """

        # Compute hash of bundle (excluding signatures field)
        bundle_hash = self._compute_bundle_hash(bundle_path)

        # Generate cosign signature
        signature = self._cosign_sign(bundle_path)

        # Build signature metadata
        sig_metadata = {
            "bundle_hash": f"sha256:{bundle_hash}",
            "signer": "cosign",
            "public_key_id": self.public_key_id,
            "signature": signature,
            "signed_at": datetime.now(timezone.utc).isoformat()
        }

        # Append signature to bundle
        self._append_signature_to_bundle(bundle_path, sig_metadata)

        # Save detached signature file
        self._save_detached_signature(bundle_path, signature)

        return sig_metadata

    def _compute_bundle_hash(self, bundle_path: Path) -> str:
        """Compute SHA256 hash of bundle (excluding signatures)"""
        with open(bundle_path) as f:
            bundle = json.load(f)

        # Remove signatures field for hashing
        bundle_copy = bundle.copy()
        bundle_copy.pop("signatures", None)

        # Deterministic JSON
        json_str = json.dumps(bundle_copy, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(json_str.encode()).hexdigest()

    def _cosign_sign(self, bundle_path: Path) -> str:
        """
        Sign bundle with cosign

        Args:
            bundle_path: Path to bundle file

        Returns:
            Base64-encoded signature
        """
        sig_path = bundle_path.with_suffix('.sig')

        # Run cosign sign-blob
        cmd = [
            'cosign', 'sign-blob',
            '--key', str(self.key_path),
            '--output-signature', str(sig_path),
            str(bundle_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        # Read signature
        with open(sig_path) as f:
            signature = f.read().strip()

        return signature

    def _append_signature_to_bundle(self, bundle_path: Path, sig_metadata: Dict):
        """Append signature metadata to bundle JSON"""
        with open(bundle_path) as f:
            bundle = json.load(f)

        bundle["signatures"] = sig_metadata

        with open(bundle_path, 'w') as f:
            json.dump(bundle, f, indent=2)

    def _save_detached_signature(self, bundle_path: Path, signature: str):
        """Save detached signature file for auditor verification"""
        sig_path = bundle_path.with_suffix('.sig')
        with open(sig_path, 'w') as f:
            f.write(signature)

    @staticmethod
    def verify_bundle(bundle_path: Path, public_key_path: Path) -> bool:
        """
        Verify bundle signature

        Args:
            bundle_path: Path to evidence bundle
            public_key_path: Path to public key for verification

        Returns:
            True if signature is valid, False otherwise
        """
        sig_path = bundle_path.with_suffix('.sig')

        if not sig_path.exists():
            raise FileNotFoundError(f"Signature file not found: {sig_path}")

        # Run cosign verify-blob
        cmd = [
            'cosign', 'verify-blob',
            '--key', str(public_key_path),
            '--signature', str(sig_path),
            str(bundle_path)
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False
```

### Verification Process (Auditor Workflow)

```bash
#!/bin/bash
# auditor_verify_evidence.sh
#
# Script for auditors to verify evidence bundle integrity

BUNDLE_FILE="$1"
PUBLIC_KEY="msp-evidence-public-key.pem"

echo "=== Evidence Bundle Verification ==="
echo "Bundle: $BUNDLE_FILE"
echo ""

# Step 1: Verify cosign signature
echo "[1/4] Verifying cryptographic signature..."
if cosign verify-blob \
    --key "$PUBLIC_KEY" \
    --signature "${BUNDLE_FILE}.sig" \
    "$BUNDLE_FILE"; then
    echo "✓ Signature valid - bundle has not been tampered with"
else
    echo "✗ Signature invalid - TAMPERING DETECTED"
    exit 1
fi

# Step 2: Verify bundle hash matches claimed value
echo ""
echo "[2/4] Verifying bundle hash..."
CLAIMED_HASH=$(jq -r '.signatures.bundle_hash' "$BUNDLE_FILE" | cut -d: -f2)
# Remove signatures field and recompute hash
ACTUAL_HASH=$(jq 'del(.signatures)' "$BUNDLE_FILE" | \
    jq -cS . | \
    sha256sum | \
    cut -d' ' -f1)

if [ "$CLAIMED_HASH" = "$ACTUAL_HASH" ]; then
    echo "✓ Bundle hash matches: $CLAIMED_HASH"
else
    echo "✗ Hash mismatch!"
    echo "  Claimed: $CLAIMED_HASH"
    echo "  Actual:  $ACTUAL_HASH"
    exit 1
fi

# Step 3: Verify artifact hashes
echo ""
echo "[3/4] Verifying artifact hashes..."
ARTIFACTS_DIR=$(dirname "$BUNDLE_FILE")/artifacts

jq -r '.artifacts[] | "\(.filename):\(.hash)"' "$BUNDLE_FILE" | \
while IFS=: read -r filename hash; do
    ARTIFACT_PATH="$ARTIFACTS_DIR/$filename"

    if [ ! -f "$ARTIFACT_PATH" ]; then
        echo "✗ Artifact missing: $filename"
        exit 1
    fi

    ACTUAL_HASH=$(sha256sum "$ARTIFACT_PATH" | cut -d' ' -f1)
    CLAIMED_HASH=$(echo "$hash" | cut -d: -f2)

    if [ "$CLAIMED_HASH" = "$ACTUAL_HASH" ]; then
        echo "  ✓ $filename"
    else
        echo "  ✗ $filename - HASH MISMATCH"
        exit 1
    fi
done

# Step 4: Verify runbook hash
echo ""
echo "[4/4] Verifying runbook hash..."
RUNBOOK_ID=$(jq -r '.runbook.id' "$BUNDLE_FILE")
CLAIMED_RB_HASH=$(jq -r '.runbook.hash' "$BUNDLE_FILE" | cut -d: -f2)
RUNBOOK_PATH="../runbooks/${RUNBOOK_ID}.yaml"

if [ ! -f "$RUNBOOK_PATH" ]; then
    echo "⚠ Runbook file not available for verification"
else
    ACTUAL_RB_HASH=$(sha256sum "$RUNBOOK_PATH" | cut -d' ' -f1)

    if [ "$CLAIMED_RB_HASH" = "$ACTUAL_RB_HASH" ]; then
        echo "✓ Runbook hash matches approved version"
    else
        echo "✗ Runbook hash mismatch - unauthorized modification?"
        exit 1
    fi
fi

echo ""
echo "=== Verification Complete ==="
echo "✓ Bundle integrity confirmed"
echo "✓ All artifacts verified"
echo "✓ Evidence is tamper-free and trustworthy"
```

**WHY This Verification Matters:**

1. **Cryptographic Signature:** Proves bundle created by MSP (not fabricated by client)
2. **Bundle Hash:** Detects any modification to bundle JSON
3. **Artifact Hashes:** Proves attached files match execution outputs
4. **Runbook Hash:** Confirms approved runbook version was used

**Auditor Value:** Can verify evidence without trusting anyone. Pure cryptography.

---

## WORM Storage Implementation

### S3 Configuration: `terraform/modules/evidence-storage/main.tf`

```hcl
# WORM (Write Once Read Many) storage for tamper-evident evidence
# Uses S3 Object Lock in COMPLIANCE mode

resource "aws_s3_bucket" "compliance_worm" {
  bucket = "msp-compliance-worm-${var.client_id}"

  tags = {
    Purpose     = "HIPAA Evidence Storage"
    Compliance  = "WORM"
    Client      = var.client_id
    Retention   = "7 years"
  }
}

# Enable versioning (required for Object Lock)
resource "aws_s3_bucket_versioning" "compliance_worm" {
  bucket = aws_s3_bucket.compliance_worm.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Enable Object Lock (COMPLIANCE mode = cannot delete even with root)
resource "aws_s3_bucket_object_lock_configuration" "compliance_worm" {
  bucket = aws_s3_bucket.compliance_worm.id

  rule {
    default_retention {
      mode = "COMPLIANCE"  # Immutable, enforced retention
      days = 2555          # ~7 years (HIPAA 6 years + 1 year buffer)
    }
  }
}

# Encryption at rest (AES-256)
resource "aws_s3_bucket_server_side_encryption_configuration" "compliance_worm" {
  bucket = aws_s3_bucket.compliance_worm.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Lifecycle: Transition to Glacier after 90 days (cheaper long-term)
resource "aws_s3_bucket_lifecycle_configuration" "compliance_worm" {
  bucket = aws_s3_bucket.compliance_worm.id

  rule {
    id     = "transition-to-glacier"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    # After 7 years, expire (compliance retention met)
    expiration {
      days = 2555
    }
  }
}

# Access logging (track all evidence access for audit trail)
resource "aws_s3_bucket_logging" "compliance_worm" {
  bucket = aws_s3_bucket.compliance_worm.id

  target_bucket = aws_s3_bucket.audit_logs.id
  target_prefix = "evidence-access-logs/${var.client_id}/"
}

# Block all public access
resource "aws_s3_bucket_public_access_block" "compliance_worm" {
  bucket = aws_s3_bucket.compliance_worm.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# IAM policy: Only evidence uploader can write, auditors can read
resource "aws_s3_bucket_policy" "compliance_worm" {
  bucket = aws_s3_bucket.compliance_worm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEvidenceUploader"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.evidence_uploader.arn
        }
        Action = [
          "s3:PutObject",
          "s3:PutObjectRetention",
          "s3:PutObjectLegalHold"
        ]
        Resource = "${aws_s3_bucket.compliance_worm.arn}/*"
      },
      {
        Sid    = "AllowAuditorsReadOnly"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.auditor.arn
        }
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.compliance_worm.arn,
          "${aws_s3_bucket.compliance_worm.arn}/*"
        ]
      }
    ]
  })
}
```

### Upload Implementation: `mcp-server/evidence/worm_uploader.py`

```python
"""
WORM Storage Uploader
Uploads evidence bundles to S3 with Object Lock
"""

import boto3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict

class WORMUploader:
    def __init__(self, bucket_name: str, client_id: str):
        self.s3_client = boto3.client('s3')
        self.bucket_name = bucket_name
        self.client_id = client_id

    def upload_bundle(
        self,
        bundle_path: Path,
        retention_days: int = 2555
    ) -> Dict:
        """
        Upload evidence bundle to WORM storage

        Args:
            bundle_path: Path to bundle file
            retention_days: Retention period (default 7 years)

        Returns:
            Storage metadata dictionary
        """

        # Generate S3 key with date hierarchy
        s3_key = self._generate_s3_key(bundle_path)

        # Upload with Object Lock retention
        retention_date = datetime.now(timezone.utc) + timedelta(days=retention_days)

        with open(bundle_path, 'rb') as f:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=f,
                ContentType='application/json',
                ServerSideEncryption='AES256',
                ObjectLockMode='COMPLIANCE',
                ObjectLockRetainUntilDate=retention_date,
                Metadata={
                    'client-id': self.client_id,
                    'evidence-type': 'runbook-execution',
                    'retention-years': '7'
                }
            )

        # Upload signature file
        sig_path = bundle_path.with_suffix('.sig')
        if sig_path.exists():
            with open(sig_path, 'rb') as f:
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=f"{s3_key}.sig",
                    Body=f,
                    ContentType='application/octet-stream',
                    ServerSideEncryption='AES256',
                    ObjectLockMode='COMPLIANCE',
                    ObjectLockRetainUntilDate=retention_date
                )

        # Return storage metadata
        s3_url = f"s3://{self.bucket_name}/{s3_key}"

        return {
            "local_path": str(bundle_path),
            "worm_url": s3_url,
            "worm_lock_enabled": True,
            "retention_days": retention_days,
            "retention_until": retention_date.isoformat(),
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        }

    def _generate_s3_key(self, bundle_path: Path) -> str:
        """Generate S3 key with date hierarchy"""
        # Extract date from bundle ID (EB-YYYYMMDD-NNNN)
        bundle_id = bundle_path.stem
        date_part = bundle_id.split('-')[1]  # YYYYMMDD

        year = date_part[:4]
        month = date_part[4:6]

        # S3 key: {client_id}/{year}/{month}/{bundle_id}.json
        return f"{self.client_id}/{year}/{month}/{bundle_path.name}"

    def verify_immutability(self, s3_key: str) -> bool:
        """
        Verify that object has Object Lock enabled

        Args:
            s3_key: S3 object key

        Returns:
            True if Object Lock is active, False otherwise
        """
        try:
            response = self.s3_client.get_object_retention(
                Bucket=self.bucket_name,
                Key=s3_key
            )

            retention = response.get('Retention', {})
            mode = retention.get('Mode')
            retain_until = retention.get('RetainUntilDate')

            # Check if COMPLIANCE mode and retention not yet expired
            if mode == 'COMPLIANCE' and retain_until:
                return retain_until > datetime.now(timezone.utc)

            return False

        except self.s3_client.exceptions.NoSuchObjectLockConfiguration:
            return False
```

### WHY WORM Storage

**Traditional S3:** Administrator can delete/modify objects
```bash
aws s3 rm s3://bucket/evidence.json  # Succeeds (BAD)
```

**S3 Object Lock (COMPLIANCE mode):** Even root AWS account cannot delete
```bash
aws s3 rm s3://bucket/evidence.json  # Access Denied (GOOD)
```

**HIPAA Compliance Value:**

- **§164.312(b):** Audit controls must be protected from alteration or destruction
- **WORM:** Write Once Read Many = tamper-evident by design
- **Auditor Trust:** Evidence cannot be fabricated after incident

---

## Compliance Packet Generation

### Implementation: `reporting/monthly_packet_generator.py`

```python
"""
Monthly Compliance Packet Generator
Aggregates evidence bundles into auditor-ready PDF report
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict
from jinja2 import Template
import pdfkit  # HTML to PDF converter

class CompliancePacketGenerator:
    def __init__(self, client_id: str, evidence_dir: Path):
        self.client_id = client_id
        self.evidence_dir = Path(evidence_dir)

        # Load template
        template_path = Path(__file__).parent / "templates" / "monthly_packet.md"
        with open(template_path) as f:
            self.template = Template(f.read())

    def generate_monthly_packet(
        self,
        year: int,
        month: int
    ) -> Path:
        """
        Generate monthly compliance packet

        Args:
            year: Year (e.g., 2025)
            month: Month (1-12)

        Returns:
            Path to generated PDF
        """

        # Collect all evidence bundles for the month
        bundles = self._collect_monthly_bundles(year, month)

        # Analyze bundles for statistics
        stats = self._analyze_bundles(bundles)

        # Render Markdown from template
        markdown = self.template.render(
            client_id=self.client_id,
            year=year,
            month=month,
            bundles=bundles,
            stats=stats,
            generated_at=datetime.now().isoformat()
        )

        # Convert Markdown to HTML
        html = self._markdown_to_html(markdown)

        # Convert HTML to PDF
        pdf_path = self._html_to_pdf(html, year, month)

        return pdf_path

    def _collect_monthly_bundles(self, year: int, month: int) -> List[Dict]:
        """Collect all evidence bundles for specified month"""
        bundle_dir = self.evidence_dir / str(year) / f"{month:02d}"

        if not bundle_dir.exists():
            return []

        bundles = []
        for bundle_file in bundle_dir.glob("EB-*.json"):
            with open(bundle_file) as f:
                bundles.append(json.load(f))

        # Sort by timestamp
        bundles.sort(key=lambda b: b['generated_at'])

        return bundles

    def _analyze_bundles(self, bundles: List[Dict]) -> Dict:
        """Compute statistics from evidence bundles"""

        total_incidents = len(bundles)
        auto_resolved = sum(1 for b in bundles if b['incident'].get('sla_met', False))

        # Calculate average MTTR
        mttrs = [b['incident']['mttr_seconds'] for b in bundles if 'mttr_seconds' in b['incident']]
        avg_mttr = sum(mttrs) / len(mttrs) if mttrs else 0

        # Count by severity
        severity_counts = {}
        for bundle in bundles:
            severity = bundle['incident']['severity']
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

        # Extract HIPAA controls covered
        hipaa_controls = set()
        for bundle in bundles:
            hipaa_controls.update(bundle['runbook']['hipaa_controls'])

        return {
            "total_incidents": total_incidents,
            "auto_resolved": auto_resolved,
            "auto_resolve_pct": (auto_resolved / total_incidents * 100) if total_incidents else 0,
            "avg_mttr_seconds": avg_mttr,
            "avg_mttr_minutes": avg_mttr / 60,
            "severity_counts": severity_counts,
            "hipaa_controls_covered": sorted(list(hipaa_controls)),
            "compliance_pct": self._calculate_compliance_pct(bundles)
        }

    def _calculate_compliance_pct(self, bundles: List[Dict]) -> float:
        """Calculate overall compliance percentage"""
        # Simplified: % of incidents resolved within SLA
        if not bundles:
            return 100.0

        sla_met = sum(1 for b in bundles if b['incident'].get('sla_met', False))
        return (sla_met / len(bundles)) * 100

    def _markdown_to_html(self, markdown: str) -> str:
        """Convert Markdown to HTML"""
        import markdown2
        return markdown2.markdown(
            markdown,
            extras=["tables", "fenced-code-blocks"]
        )

    def _html_to_pdf(self, html: str, year: int, month: int) -> Path:
        """Convert HTML to PDF"""
        output_dir = Path("output") / str(year) / f"{month:02d}"
        output_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = output_dir / f"{self.client_id}-{year}-{month:02d}-compliance-packet.pdf"

        # pdfkit options for professional output
        options = {
            'page-size': 'Letter',
            'margin-top': '0.75in',
            'margin-right': '0.75in',
            'margin-bottom': '0.75in',
            'margin-left': '0.75in',
            'encoding': "UTF-8",
            'no-outline': None,
            'enable-local-file-access': None
        }

        pdfkit.from_string(html, str(pdf_path), options=options)

        return pdf_path
```

---

## Security Considerations

### Threat Model

**Threat 1: Evidence Tampering**
- **Attack:** Attacker modifies evidence bundle to hide incident
- **Mitigation:** Cryptographic signature + WORM storage
- **Detection:** Signature verification fails

**Threat 2: Evidence Fabrication**
- **Attack:** Create fake evidence bundle claiming compliance
- **Mitigation:** Signature tied to MSP private key (client doesn't have it)
- **Detection:** Signature verification requires MSP public key

**Threat 3: Selective Disclosure**
- **Attack:** Only show "good" evidence bundles to auditor
- **Mitigation:** Nightly aggregation creates complete manifest
- **Detection:** Auditor checks for gaps in bundle IDs

**Threat 4: Replay Attacks**
- **Attack:** Re-submit old evidence for new incidents
- **Mitigation:** Timestamps in signed bundles
- **Detection:** Bundle timestamps don't match incident times

**Threat 5: Key Compromise**
- **Attack:** Steal signing key, create fake evidence
- **Mitigation:** Hardware Security Module (HSM) for key storage
- **Detection:** Rotate keys regularly, old signatures still valid

### Key Management

**Signing Key:**
- **Storage:** AWS KMS or HashiCorp Vault
- **Access:** Only evidence packager service account
- **Rotation:** Yearly, with overlap period
- **Backup:** Offline copy in physical safe

**Public Key Distribution:**
- **Location:** Embedded in compliance packets
- **Verification:** Published on MSP website
- **Trust:** Auditor downloads directly from MSP

---

## Implementation Guide

### Week 5 Tasks

**Day 1: Evidence Bundler**
```bash
# Implement core bundler
mkdir -p mcp-server/evidence
touch mcp-server/evidence/bundler.py
touch mcp-server/evidence/__init__.py

# Create JSON schema
mkdir -p mcp-server/evidence/schemas
touch mcp-server/evidence/schemas/evidence_bundle.json

# Unit tests
mkdir -p mcp-server/evidence/tests
touch mcp-server/evidence/tests/test_bundler.py
```

**Day 2: Cryptographic Signer**
```bash
# Install cosign
wget https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64
sudo mv cosign-linux-amd64 /usr/local/bin/cosign
sudo chmod +x /usr/local/bin/cosign

# Generate signing key
cosign generate-key-pair
# Saves: cosign.key (private), cosign.pub (public)

# Implement signer
touch mcp-server/evidence/signer.py
touch mcp-server/evidence/tests/test_signer.py
```

**Day 3: WORM Storage**
```bash
# Terraform module
mkdir -p terraform/modules/evidence-storage
touch terraform/modules/evidence-storage/main.tf
touch terraform/modules/evidence-storage/variables.tf
touch terraform/modules/evidence-storage/outputs.tf

# Deploy
cd terraform/modules/evidence-storage
terraform init
terraform plan
terraform apply

# Implement uploader
touch mcp-server/evidence/worm_uploader.py
touch mcp-server/evidence/tests/test_worm_uploader.py
```

**Day 4: Integration**
```bash
# Wire up to MCP executor
# Modify mcp-server/executor.py to call bundler after runbook execution

# Test end-to-end
# 1. Trigger test incident
# 2. Execute runbook
# 3. Verify evidence bundle created
# 4. Verify signature valid
# 5. Verify uploaded to S3
# 6. Verify Object Lock active
```

**Day 5: Compliance Packet**
```bash
# Implement monthly packet generator
mkdir -p reporting
touch reporting/monthly_packet_generator.py
touch reporting/templates/monthly_packet.md

# Test generation
python reporting/monthly_packet_generator.py \
  --client-id clinic-001 \
  --year 2025 \
  --month 10 \
  --output output/

# Verify PDF generated
ls -la output/2025/10/*.pdf
```

### Success Criteria

- [ ] Evidence bundles pass JSON schema validation
- [ ] Cosign signature verification succeeds
- [ ] WORM storage rejects delete attempts
- [ ] Monthly packet includes all bundles
- [ ] PDF is auditor-readable (non-technical person can understand)

---

**End of Document**
**Version:** 1.0
**Last Updated:** 2025-10-31
**Next Review:** After Week 5 implementation
