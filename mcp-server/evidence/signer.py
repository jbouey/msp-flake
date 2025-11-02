#!/usr/bin/env python3
"""
Evidence Signer - Cryptographically signs evidence bundles using cosign

Uses cosign for signing because:
- Industry standard for supply chain security
- Supports keyless signing with transparency logs
- Compatible with standard verification tools
- Well-documented and auditable

HIPAA Controls: §164.312(c)(1) - Integrity controls
"""

import subprocess
import logging
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
import json


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class EvidenceSigner:
    """
    Signs evidence bundles with cosign

    Supports two modes:
    1. Key-pair signing (production) - requires private key
    2. Keyless signing (future) - uses OIDC + transparency log

    Usage:
        signer = EvidenceSigner(private_key_path="/etc/msp/signing-keys/private-key.pem")
        signature_path = signer.sign_bundle("/var/lib/msp/evidence/EB-20251031-0042.json")
    """

    def __init__(
        self,
        private_key_path: str = "/etc/msp/signing-keys/private-key.key",
        public_key_path: str = "/etc/msp/signing-keys/private-key.pub"
    ):
        self.private_key = Path(private_key_path)
        self.public_key = Path(public_key_path)

        # Verify cosign is installed
        self._check_cosign_installed()

        # Verify key exists
        if not self.private_key.exists():
            raise FileNotFoundError(f"Private key not found: {self.private_key}")

        # Verify key permissions (should be 400 or 600)
        mode = self.private_key.stat().st_mode & 0o777
        if mode not in [0o400, 0o600]:
            logger.warning(f"Private key has insecure permissions: {oct(mode)}, should be 400")

        logger.info(f"Signer initialized with key: {self.private_key}")

    def _check_cosign_installed(self):
        """Verify cosign is available"""
        try:
            result = subprocess.run(
                ["cosign", "version"],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Cosign available: {result.stdout.strip()}")
        except FileNotFoundError:
            raise RuntimeError(
                "cosign not found. Install with: "
                "brew install cosign (macOS) or "
                "apt install cosign (Debian/Ubuntu)"
            )

    def sign_bundle(self, bundle_path: str) -> Path:
        """
        Sign evidence bundle and create detached signature

        Args:
            bundle_path: Path to evidence bundle JSON file

        Returns:
            Path to signature file (.sig)

        Raises:
            subprocess.CalledProcessError if signing fails
        """
        bundle_path = Path(bundle_path)

        if not bundle_path.exists():
            raise FileNotFoundError(f"Bundle not found: {bundle_path}")

        # Verify bundle is valid JSON before signing
        self._verify_json(bundle_path)

        # Signature will be written alongside bundle with .sig extension
        sig_path = bundle_path.with_suffix(bundle_path.suffix + '.sig')

        logger.info(f"Signing bundle: {bundle_path}")

        # Sign with cosign
        # Using --key flag for key-pair signing
        # Output goes to --output-signature file
        # --bundle flag required for cosign v3+ (stores transparency log info)
        bundle_path_obj = Path(bundle_path)
        bundle_file = bundle_path_obj.with_suffix(bundle_path_obj.suffix + '.bundle')

        try:
            subprocess.run(
                [
                    "cosign",
                    "sign-blob",
                    "--key", str(self.private_key),
                    "--output-signature", str(sig_path),
                    "--bundle", str(bundle_file),
                    "--yes",
                    str(bundle_path)
                ],
                check=True,
                capture_output=True,
                text=True
            )

            logger.info(f"Created signature bundle: {bundle_file}")

            # Verify signature immediately after creation
            self.verify_signature(bundle_path, bundle_file)

            return bundle_file

        except subprocess.CalledProcessError as e:
            logger.error(f"Signing failed: {e.stderr}")
            raise

    def verify_signature(
        self,
        bundle_path: str,
        sig_bundle: Optional[str] = None,
        public_key: Optional[str] = None
    ) -> bool:
        """
        Verify bundle signature using cosign bundle

        Args:
            bundle_path: Path to the data file that was signed
            sig_bundle: Path to signature bundle file (defaults to bundle_path + .bundle)
            public_key: Path to public key (defaults to self.public_key)

        Returns:
            True if verification succeeds

        Raises:
            subprocess.CalledProcessError if verification fails
        """
        bundle_path = Path(bundle_path)

        if sig_bundle is None:
            sig_bundle = bundle_path.with_suffix(bundle_path.suffix + '.bundle')
        else:
            sig_bundle = Path(sig_bundle)

        if public_key is None:
            public_key = self.public_key
        else:
            public_key = Path(public_key)

        if not sig_bundle.exists():
            raise FileNotFoundError(f"Signature bundle not found: {sig_bundle}")

        logger.info(f"Verifying signature: {sig_bundle}")

        try:
            result = subprocess.run(
                [
                    "cosign",
                    "verify-blob",
                    "--key", str(public_key),
                    "--bundle", str(sig_bundle),
                    str(bundle_path)
                ],
                check=True,
                capture_output=True,
                text=True
            )

            logger.info(f"✅ Signature verified: {bundle_path.name}")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Signature verification FAILED: {e.stderr}")
            raise

    def get_signature_info(self, sig_path: str) -> Dict[str, Any]:
        """
        Get information about signature file

        Returns basic metadata about the signature
        """
        sig_path = Path(sig_path)

        if not sig_path.exists():
            raise FileNotFoundError(f"Signature not found: {sig_path}")

        # Get file stats
        stat = sig_path.stat()

        # Compute signature hash (for verification it wasn't modified)
        with open(sig_path, 'rb') as f:
            sig_hash = hashlib.sha256(f.read()).hexdigest()

        return {
            "signature_file": str(sig_path),
            "signature_size_bytes": stat.st_size,
            "signature_hash": f"sha256:{sig_hash}",
            "created_at": stat.st_mtime,
            "permissions": oct(stat.st_mode & 0o777)
        }

    def _verify_json(self, bundle_path: Path):
        """Verify file is valid JSON before signing"""
        try:
            with open(bundle_path) as f:
                json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Bundle is not valid JSON: {e}")


class SigningKeyManager:
    """
    Manages signing key lifecycle (generation, rotation, archival)

    Usage:
        manager = SigningKeyManager()
        manager.generate_key_pair("/etc/msp/signing-keys/")
        manager.rotate_keys(old_key="/path/to/old", new_key="/path/to/new")
    """

    def generate_key_pair(
        self,
        output_dir: str,
        key_name: str = "private-key"
    ) -> tuple[Path, Path]:
        """
        Generate new cosign key pair

        Args:
            output_dir: Directory to write keys
            key_name: Base name for key files (without extension)

        Returns:
            Tuple of (private_key_path, public_key_path)
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        private_key = output_dir / f"{key_name}.key"
        public_key = output_dir / f"{key_name}.pub"

        logger.info(f"Generating cosign key pair in {output_dir}")

        # Generate key pair
        # Note: This will prompt for password in interactive mode
        # For automated generation, use COSIGN_PASSWORD env var
        try:
            subprocess.run(
                [
                    "cosign",
                    "generate-key-pair",
                    "--output-key-prefix", str(output_dir / key_name)
                ],
                check=True,
                capture_output=True,
                text=True
            )

            # Verify keys were created
            if not private_key.exists() or not public_key.exists():
                raise RuntimeError("Key generation failed - files not created")

            # Set secure permissions on private key
            private_key.chmod(0o400)

            logger.info(f"✅ Generated key pair:")
            logger.info(f"   Private: {private_key} (mode 400)")
            logger.info(f"   Public:  {public_key}")

            return private_key, public_key

        except subprocess.CalledProcessError as e:
            logger.error(f"Key generation failed: {e.stderr}")
            raise

    def archive_old_key(
        self,
        old_key_path: str,
        archive_dir: str,
        validity_period: str
    ):
        """
        Archive old signing key for historical verification

        Args:
            old_key_path: Path to old PUBLIC key (never archive private keys)
            archive_dir: Directory to store historical keys
            validity_period: Date range this key was valid (e.g., "2025-01-01-to-2025-12-31")
        """
        old_key = Path(old_key_path)
        archive_dir = Path(archive_dir)

        if "private" in old_key.name.lower():
            raise ValueError("Never archive private keys - only public keys")

        archive_dir.mkdir(parents=True, exist_ok=True)

        # Archive with validity period in filename
        archive_name = f"public-key-{validity_period}.pem"
        archive_path = archive_dir / archive_name

        # Copy (don't move - keep backup)
        import shutil
        shutil.copy2(old_key, archive_path)

        logger.info(f"Archived old public key: {archive_path}")

        # Compute hash for verification
        with open(archive_path, 'rb') as f:
            key_hash = hashlib.sha256(f.read()).hexdigest()

        # Write key info file
        info_path = archive_path.with_suffix('.info')
        with open(info_path, 'w') as f:
            f.write(f"Key Valid: {validity_period}\n")
            f.write(f"SHA256: {key_hash}\n")
            f.write(f"Archived: {Path(old_key).stat().st_mtime}\n")

        logger.info(f"Key hash: sha256:{key_hash}")


# Example usage
if __name__ == "__main__":
    import sys

    # Sign a bundle
    if len(sys.argv) < 2:
        print("Usage: python signer.py <bundle.json>")
        print("   or: python signer.py --generate-keys <output_dir>")
        sys.exit(1)

    if sys.argv[1] == "--generate-keys":
        # Generate new key pair
        output_dir = sys.argv[2] if len(sys.argv) > 2 else "/tmp/msp-keys"
        manager = SigningKeyManager()
        private, public = manager.generate_key_pair(output_dir)
        print(f"\n✅ Keys generated:")
        print(f"   Private: {private}")
        print(f"   Public:  {public}")
        print(f"\n⚠️  Store private key securely!")
        print(f"   Recommended: chmod 400 {private}")

    else:
        # Sign bundle
        bundle_path = sys.argv[1]

        # For testing, use keys from test directory or current directory if they exist
        if Path("/tmp/msp-test-keys/private-key.key").exists():
            signer = EvidenceSigner(
                private_key_path="/tmp/msp-test-keys/private-key.key",
                public_key_path="/tmp/msp-test-keys/private-key.pub"
            )
        elif Path("./private-key.key").exists():
            signer = EvidenceSigner(
                private_key_path="./private-key.key",
                public_key_path="./private-key.pub"
            )
        else:
            # Use default paths
            signer = EvidenceSigner()

        sig_path = signer.sign_bundle(bundle_path)
        print(f"\n✅ Bundle signed:")
        print(f"   Bundle: {bundle_path}")
        print(f"   Signature: {sig_path}")

        # Show signature info
        info = signer.get_signature_info(sig_path)
        print(f"\n   Signature hash: {info['signature_hash']}")
        print(f"   Size: {info['signature_size_bytes']} bytes")
