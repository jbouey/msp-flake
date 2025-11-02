#!/usr/bin/env python3
"""
Evidence Bundle Uploader

Uploads signed evidence bundles to AWS S3 with Object Lock (WORM storage).
Implements retry logic for network failures and validates uploads.

HIPAA Controls:
- §164.310(d)(2)(iv) - Data Backup and Storage
- §164.312(c)(1) - Integrity Controls

Author: MSP Compliance Platform
Version: 1.0.0
"""

import os
import sys
import time
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import ClientError, BotoCoreError


class EvidenceUploader:
    """
    Uploads evidence bundles to S3 with Object Lock.

    Features:
    - Automatic retry on network failures
    - SHA256 verification after upload
    - Object Lock validation
    - Lifecycle policy enforcement
    """

    def __init__(
        self,
        bucket_name: str,
        aws_region: str = 'us-east-1',
        retention_days: int = 90,
        max_retries: int = 3,
        retry_delay_seconds: int = 5
    ):
        """
        Initialize uploader.

        Args:
            bucket_name: S3 bucket name (must have Object Lock enabled)
            aws_region: AWS region for bucket
            retention_days: How long to retain evidence (minimum 90 days for HIPAA)
            max_retries: Maximum upload attempts on failure
            retry_delay_seconds: Delay between retry attempts
        """
        self.bucket_name = bucket_name
        self.aws_region = aws_region
        self.retention_days = retention_days
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

        # Initialize S3 client
        self.s3_client = boto3.client('s3', region_name=aws_region)

        # Validate bucket exists and has Object Lock
        self._validate_bucket()

    def _validate_bucket(self) -> None:
        """
        Validate that bucket exists and has Object Lock enabled.

        Raises:
            ValueError: If bucket doesn't exist or Object Lock is not enabled
        """
        try:
            # Check bucket exists
            self.s3_client.head_bucket(Bucket=self.bucket_name)

            # Check Object Lock configuration
            response = self.s3_client.get_object_lock_configuration(Bucket=self.bucket_name)

            if response['ObjectLockConfiguration']['ObjectLockEnabled'] != 'Enabled':
                raise ValueError(f"Bucket {self.bucket_name} does not have Object Lock enabled")

            print(f"✓ Bucket validated: {self.bucket_name} (Object Lock enabled)")

        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                raise ValueError(f"Bucket {self.bucket_name} does not exist")
            elif error_code == 'ObjectLockConfigurationNotFoundError':
                raise ValueError(f"Bucket {self.bucket_name} does not have Object Lock enabled")
            else:
                raise ValueError(f"Failed to validate bucket: {e}")

    def upload_bundle(
        self,
        bundle_path: Path,
        signature_path: Path,
        client_id: str
    ) -> Tuple[str, str]:
        """
        Upload evidence bundle and signature to S3 with Object Lock.

        Args:
            bundle_path: Path to evidence bundle JSON file
            signature_path: Path to cosign signature bundle file
            client_id: Client identifier for S3 key prefix

        Returns:
            Tuple of (bundle_s3_uri, signature_s3_uri)

        Raises:
            Exception: If upload fails after max_retries
        """
        # Generate S3 keys with date-based prefix
        # Format: evidence/{client_id}/{year}/{month}/{bundle_id}.json
        bundle_id = bundle_path.stem  # EB-YYYYMMDD-NNNN
        date_parts = bundle_id.split('-')
        year = date_parts[1][:4]
        month = date_parts[1][4:6]

        bundle_key = f"evidence/{client_id}/{year}/{month}/{bundle_path.name}"
        signature_key = f"evidence/{client_id}/{year}/{month}/{signature_path.name}"

        # Upload bundle
        bundle_uri = self._upload_file_with_retry(bundle_path, bundle_key)

        # Upload signature
        signature_uri = self._upload_file_with_retry(signature_path, signature_key)

        # Verify uploads
        self._verify_upload(bundle_path, bundle_key)
        self._verify_upload(signature_path, signature_key)

        # Verify Object Lock is applied
        self._verify_object_lock(bundle_key)
        self._verify_object_lock(signature_key)

        print(f"✓ Upload complete: {bundle_uri}")
        print(f"✓ Signature uploaded: {signature_uri}")

        return bundle_uri, signature_uri

    def _upload_file_with_retry(
        self,
        file_path: Path,
        s3_key: str
    ) -> str:
        """
        Upload file to S3 with retry logic.

        Args:
            file_path: Path to file to upload
            s3_key: S3 object key

        Returns:
            S3 URI of uploaded object

        Raises:
            Exception: If upload fails after max_retries
        """
        # Calculate retention until date
        retention_until = datetime.utcnow() + timedelta(days=self.retention_days)

        for attempt in range(1, self.max_retries + 1):
            try:
                print(f"  Uploading {file_path.name} (attempt {attempt}/{self.max_retries})...")

                # Upload with Object Lock retention
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=file_path.read_bytes(),
                    ObjectLockMode='COMPLIANCE',  # COMPLIANCE mode cannot be removed by anyone
                    ObjectLockRetainUntilDate=retention_until,
                    ContentType='application/json' if file_path.suffix == '.json' else 'application/octet-stream',
                    Metadata={
                        'uploaded_at': datetime.utcnow().isoformat(),
                        'retention_days': str(self.retention_days),
                        'original_path': str(file_path)
                    }
                )

                s3_uri = f"s3://{self.bucket_name}/{s3_key}"
                return s3_uri

            except (ClientError, BotoCoreError) as e:
                if attempt < self.max_retries:
                    print(f"  Upload failed: {e}, retrying in {self.retry_delay_seconds}s...")
                    time.sleep(self.retry_delay_seconds)
                else:
                    raise Exception(f"Upload failed after {self.max_retries} attempts: {e}")

    def _verify_upload(self, local_path: Path, s3_key: str) -> None:
        """
        Verify uploaded file matches local file (SHA256).

        Args:
            local_path: Path to local file
            s3_key: S3 object key

        Raises:
            ValueError: If checksums don't match
        """
        # Calculate local file checksum
        local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()

        # Download and calculate S3 object checksum
        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
        s3_hash = hashlib.sha256(response['Body'].read()).hexdigest()

        if local_hash != s3_hash:
            raise ValueError(
                f"Upload verification failed: checksums don't match\n"
                f"  Local:  {local_hash}\n"
                f"  S3:     {s3_hash}"
            )

        print(f"  ✓ Verified: {local_path.name} (SHA256: {local_hash[:16]}...)")

    def _verify_object_lock(self, s3_key: str) -> None:
        """
        Verify Object Lock is applied to uploaded object.

        Args:
            s3_key: S3 object key

        Raises:
            ValueError: If Object Lock is not properly configured
        """
        response = self.s3_client.get_object_retention(
            Bucket=self.bucket_name,
            Key=s3_key
        )

        retention = response['Retention']

        if retention['Mode'] != 'COMPLIANCE':
            raise ValueError(f"Object Lock mode is {retention['Mode']}, expected COMPLIANCE")

        retain_until = retention['RetainUntilDate']
        days_remaining = (retain_until - datetime.now(retain_until.tzinfo)).days

        print(f"  ✓ Object Lock verified: COMPLIANCE mode, {days_remaining} days remaining")

    def download_bundle(
        self,
        s3_uri: str,
        output_path: Path
    ) -> Path:
        """
        Download evidence bundle from S3.

        Args:
            s3_uri: S3 URI of bundle (s3://bucket/key)
            output_path: Local path to save bundle

        Returns:
            Path to downloaded file
        """
        # Parse S3 URI
        if not s3_uri.startswith('s3://'):
            raise ValueError(f"Invalid S3 URI: {s3_uri}")

        parts = s3_uri[5:].split('/', 1)
        bucket = parts[0]
        key = parts[1]

        print(f"Downloading {key}...")

        # Download file
        self.s3_client.download_file(bucket, key, str(output_path))

        print(f"✓ Downloaded to {output_path}")
        return output_path

    def list_bundles(
        self,
        client_id: str,
        date_prefix: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        List evidence bundles for a client.

        Args:
            client_id: Client identifier
            date_prefix: Optional date prefix (YYYYMM or YYYYMMDD)

        Returns:
            List of dicts with bundle metadata
        """
        # Build S3 prefix
        prefix = f"evidence/{client_id}/"
        if date_prefix:
            year = date_prefix[:4]
            month = date_prefix[4:6] if len(date_prefix) >= 6 else ""
            prefix += f"{year}/{month}/"

        print(f"Listing bundles: {prefix}")

        # List objects
        paginator = self.s3_client.get_paginator('list_objects_v2')
        bundles = []

        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
            if 'Contents' not in page:
                continue

            for obj in page['Contents']:
                key = obj['Key']

                # Only include .json files (not .bundle signature files)
                if not key.endswith('.json'):
                    continue

                bundles.append({
                    'bundle_id': Path(key).stem,
                    's3_uri': f"s3://{self.bucket_name}/{key}",
                    'size_bytes': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat()
                })

        print(f"Found {len(bundles)} bundles")
        return bundles


def main():
    """Test uploader with sample bundle."""

    # Check for required environment variables
    bucket_name = os.getenv('MSP_WORM_BUCKET')
    if not bucket_name:
        print("Error: MSP_WORM_BUCKET environment variable not set")
        print("\nUsage:")
        print("  export MSP_WORM_BUCKET=msp-compliance-worm")
        print("  python3 uploader.py")
        sys.exit(1)

    # Initialize uploader
    print(f"Initializing uploader for bucket: {bucket_name}")
    uploader = EvidenceUploader(
        bucket_name=bucket_name,
        aws_region=os.getenv('AWS_REGION', 'us-east-1'),
        retention_days=90
    )

    # Find latest bundle in evidence directory
    from config import EvidenceConfig

    evidence_dir = EvidenceConfig.EVIDENCE_DIR
    bundles = sorted(evidence_dir.glob('EB-*.json'), reverse=True)

    if not bundles:
        print("\nError: No evidence bundles found in {evidence_dir}")
        print("Run pipeline.py or test_integration.py first to generate bundles")
        sys.exit(1)

    bundle_path = bundles[0]
    signature_path = Path(str(bundle_path) + '.bundle')

    if not signature_path.exists():
        print(f"\nError: Signature file not found: {signature_path}")
        sys.exit(1)

    print(f"\nUploading bundle: {bundle_path.name}")

    # Upload bundle
    bundle_uri, sig_uri = uploader.upload_bundle(
        bundle_path=bundle_path,
        signature_path=signature_path,
        client_id='test-client-001'
    )

    print("\n✅ Upload test successful")
    print(f"Bundle URI: {bundle_uri}")
    print(f"Signature URI: {sig_uri}")

    # List bundles
    print("\nListing uploaded bundles:")
    bundles = uploader.list_bundles(client_id='test-client-001')
    for bundle in bundles:
        print(f"  {bundle['bundle_id']} ({bundle['size_bytes']} bytes)")


if __name__ == '__main__':
    main()
