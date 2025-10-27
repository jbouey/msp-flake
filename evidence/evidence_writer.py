"""
Evidence Writer - Hash-chained audit log for HIPAA compliance
Creates tamper-evident evidence trail for all automated actions
"""
import json
import hashlib
import os
from typing import Dict, Optional
from datetime import datetime
from pathlib import Path


class EvidenceChain:
    """
    Blockchain-style hash chain for evidence integrity
    Each entry contains hash of previous entry, making tampering detectable
    """

    def __init__(self, chain_file: Path):
        """
        Initialize evidence chain

        Args:
            chain_file: Path to evidence chain file (.jsonl format)
        """
        self.chain_file = Path(chain_file)
        self.chain_file.parent.mkdir(parents=True, exist_ok=True)

        # Get last hash for chaining
        self.last_hash = self._get_last_hash()

        # Initialize chain if new
        if not self.chain_file.exists():
            self._initialize_chain()

    def append(self, evidence: Dict) -> str:
        """
        Append evidence to chain with hash linking

        Args:
            evidence: Evidence bundle to append

        Returns:
            Hash of appended entry
        """
        # Build chain entry
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "previous_hash": self.last_hash,
            "evidence": evidence
        }

        # Compute hash of this entry
        entry_hash = self._compute_hash(entry)
        entry["entry_hash"] = entry_hash

        # Append to chain file
        with open(self.chain_file, 'a') as f:
            f.write(json.dumps(entry) + "\n")

        # Update last hash
        self.last_hash = entry_hash

        return entry_hash

    def verify_chain(self) -> Dict:
        """
        Verify integrity of entire chain

        Returns:
            {
                "valid": bool,
                "entries_checked": int,
                "first_invalid_entry": int or None,
                "error": str or None
            }
        """
        if not self.chain_file.exists():
            return {
                "valid": True,
                "entries_checked": 0,
                "first_invalid_entry": None,
                "error": None
            }

        entries_checked = 0
        previous_hash = None

        with open(self.chain_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    entry = json.loads(line.strip())
                    entries_checked += 1

                    # Check hash linkage
                    if entry.get("previous_hash") != previous_hash:
                        return {
                            "valid": False,
                            "entries_checked": entries_checked,
                            "first_invalid_entry": line_num,
                            "error": f"Hash chain broken at entry {line_num}"
                        }

                    # Verify entry hash
                    stored_hash = entry.get("entry_hash")
                    entry_copy = entry.copy()
                    entry_copy.pop("entry_hash", None)

                    computed_hash = self._compute_hash(entry_copy)

                    if stored_hash != computed_hash:
                        return {
                            "valid": False,
                            "entries_checked": entries_checked,
                            "first_invalid_entry": line_num,
                            "error": f"Entry hash mismatch at entry {line_num}"
                        }

                    # Update for next iteration
                    previous_hash = stored_hash

                except json.JSONDecodeError as e:
                    return {
                        "valid": False,
                        "entries_checked": entries_checked,
                        "first_invalid_entry": line_num,
                        "error": f"JSON decode error at entry {line_num}: {e}"
                    }

        return {
            "valid": True,
            "entries_checked": entries_checked,
            "first_invalid_entry": None,
            "error": None
        }

    def _get_last_hash(self) -> Optional[str]:
        """Get hash of last entry in chain"""
        if not self.chain_file.exists():
            return None

        try:
            # Read last line
            with open(self.chain_file, 'rb') as f:
                # Seek to end
                f.seek(0, os.SEEK_END)
                file_size = f.tell()

                if file_size == 0:
                    return None

                # Read backwards to find last line
                buffer_size = 8192
                buffer = b""
                position = file_size

                while position > 0:
                    read_size = min(buffer_size, position)
                    position -= read_size
                    f.seek(position)
                    chunk = f.read(read_size)
                    buffer = chunk + buffer

                    # Look for newline
                    lines = buffer.split(b'\n')
                    if len(lines) > 1:
                        # Found a complete line
                        last_line = lines[-2] if lines[-1] == b'' else lines[-1]
                        last_entry = json.loads(last_line.decode())
                        return last_entry.get("entry_hash")

                # Fallback: read entire file if small
                if buffer:
                    last_line = buffer.strip().split(b'\n')[-1]
                    last_entry = json.loads(last_line.decode())
                    return last_entry.get("entry_hash")

        except Exception as e:
            print(f"[evidence_chain] Error getting last hash: {e}")

        return None

    def _initialize_chain(self):
        """Initialize new chain with genesis entry"""
        genesis = {
            "timestamp": datetime.utcnow().isoformat(),
            "previous_hash": None,
            "evidence": {
                "type": "chain_genesis",
                "platform": "MSP Automation Platform",
                "version": "1.0.0",
                "purpose": "HIPAA-compliant evidence chain",
                "created_by": "evidence_writer.py"
            }
        }

        genesis_hash = self._compute_hash(genesis)
        genesis["entry_hash"] = genesis_hash

        with open(self.chain_file, 'w') as f:
            f.write(json.dumps(genesis) + "\n")

        self.last_hash = genesis_hash
        print(f"[evidence_chain] Initialized new chain: {self.chain_file}")

    @staticmethod
    def _compute_hash(entry: Dict) -> str:
        """Compute SHA-256 hash of entry"""
        # Serialize with sorted keys for deterministic hashing
        entry_json = json.dumps(entry, sort_keys=True)
        return hashlib.sha256(entry_json.encode()).hexdigest()


class EvidenceWriter:
    """
    High-level evidence writer with multiple storage backends
    """

    def __init__(
        self,
        evidence_dir: Path,
        enable_chain: bool = True,
        enable_worm_storage: bool = False,
        worm_bucket: Optional[str] = None
    ):
        """
        Initialize evidence writer

        Args:
            evidence_dir: Local evidence directory
            enable_chain: Enable hash-chained audit log
            enable_worm_storage: Enable WORM (write-once-read-many) cloud storage
            worm_bucket: S3 bucket URL for WORM storage
        """
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        # Hash-chained audit log
        if enable_chain:
            chain_file = self.evidence_dir / "evidence_chain.jsonl"
            self.chain = EvidenceChain(chain_file)
        else:
            self.chain = None

        # WORM storage configuration
        self.enable_worm = enable_worm_storage
        self.worm_bucket = worm_bucket

    def write_evidence(self, bundle: Dict) -> Dict:
        """
        Write evidence bundle to all configured storage backends

        Args:
            bundle: Evidence bundle from executor

        Returns:
            Storage confirmation with locations and hashes
        """
        bundle_id = bundle.get("bundle_id", "unknown")
        storage_locations = []

        # 1. Write to local file
        local_path = self._write_local(bundle)
        storage_locations.append(f"local:{local_path}")

        # 2. Append to hash chain
        if self.chain:
            chain_hash = self.chain.append({
                "bundle_id": bundle_id,
                "runbook_id": bundle.get("runbook_id"),
                "timestamp": bundle.get("timestamp_start"),
                "status": bundle.get("status"),
                "bundle_hash": bundle.get("evidence_bundle_hash"),
                "hipaa_controls": bundle.get("hipaa_controls", [])
            })
            storage_locations.append(f"chain:{chain_hash[:16]}...")

        # 3. Upload to WORM storage (if enabled)
        if self.enable_worm and self.worm_bucket:
            worm_url = self._upload_to_worm(bundle)
            if worm_url:
                storage_locations.append(f"worm:{worm_url}")

        return {
            "bundle_id": bundle_id,
            "stored": True,
            "storage_locations": storage_locations,
            "bundle_hash": bundle.get("evidence_bundle_hash"),
            "timestamp": datetime.utcnow().isoformat()
        }

    def verify_integrity(self) -> Dict:
        """Verify integrity of evidence chain"""
        if not self.chain:
            return {
                "chain_enabled": False,
                "valid": None
            }

        result = self.chain.verify_chain()
        result["chain_enabled"] = True
        result["chain_file"] = str(self.chain.chain_file)

        return result

    def _write_local(self, bundle: Dict) -> Path:
        """Write bundle to local filesystem"""
        bundle_id = bundle.get("bundle_id")

        # Organize by date
        timestamp = bundle.get("timestamp_start", "")
        if timestamp:
            date_str = timestamp[:10]  # YYYY-MM-DD
            date_dir = self.evidence_dir / date_str
            date_dir.mkdir(exist_ok=True)
        else:
            date_dir = self.evidence_dir

        # Write bundle
        bundle_path = date_dir / f"{bundle_id}.json"

        with open(bundle_path, 'w') as f:
            json.dump(bundle, f, indent=2)

        return bundle_path

    def _upload_to_worm(self, bundle: Dict) -> Optional[str]:
        """
        Upload to WORM storage (S3 with object lock)

        In production, this would use boto3 to upload to S3 with:
        - Object Lock enabled
        - Retention period set
        - Legal hold (optional)

        For now, just a stub
        """
        # TODO: Implement S3 upload with object lock
        # import boto3
        # s3 = boto3.client('s3')
        # s3.put_object(
        #     Bucket=self.worm_bucket,
        #     Key=f"{bundle['bundle_id']}.json",
        #     Body=json.dumps(bundle),
        #     ObjectLockMode='COMPLIANCE',
        #     ObjectLockRetainUntilDate=datetime.now() + timedelta(days=730)
        # )

        print(f"[evidence_writer] WORM upload stub (not implemented)")
        return None


# Convenience functions
def write_evidence(bundle: Dict, evidence_dir: Path = Path("../evidence")) -> Dict:
    """Write evidence bundle"""
    writer = EvidenceWriter(evidence_dir)
    return writer.write_evidence(bundle)


def verify_evidence_chain(evidence_dir: Path = Path("../evidence")) -> Dict:
    """Verify evidence chain integrity"""
    chain_file = evidence_dir / "evidence_chain.jsonl"
    chain = EvidenceChain(chain_file)
    return chain.verify_chain()


# Testing
if __name__ == "__main__":
    print("Testing Evidence Writer\n")

    # Create test evidence directory
    test_dir = Path("./test_evidence")
    writer = EvidenceWriter(test_dir, enable_chain=True)

    # Write test evidence bundles
    for i in range(3):
        bundle = {
            "bundle_id": f"EB-TEST-{i:03d}",
            "runbook_id": "RB-BACKUP-001",
            "timestamp_start": datetime.utcnow().isoformat(),
            "status": "success",
            "evidence_bundle_hash": hashlib.sha256(f"test-{i}".encode()).hexdigest(),
            "hipaa_controls": ["164.308(a)(7)(ii)(A)"]
        }

        result = writer.write_evidence(bundle)
        print(f"✅ Wrote bundle {i+1}: {result['bundle_id']}")
        print(f"   Locations: {result['storage_locations']}")

    # Verify chain integrity
    print("\nVerifying evidence chain integrity...")
    verification = writer.verify_integrity()

    if verification["valid"]:
        print(f"✅ Chain is valid ({verification['entries_checked']} entries)")
    else:
        print(f"❌ Chain verification failed: {verification['error']}")

    print(f"\n📁 Evidence written to: {test_dir}")
    print(f"📄 Chain file: {test_dir}/evidence_chain.jsonl")

    # Cleanup test directory
    import shutil
    # shutil.rmtree(test_dir)  # Uncomment to auto-cleanup
