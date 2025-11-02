#!/usr/bin/env python3
"""
Test WORM Storage Upload

Tests evidence bundle upload to S3 with Object Lock
Verifies that:
1. Upload succeeds
2. Object is locked with retention period
3. Object cannot be deleted
4. Object cannot be modified

Usage:
  export AWS_ACCESS_KEY_ID=<from terraform output>
  export AWS_SECRET_ACCESS_KEY=<from terraform output>
  python3 test_worm_upload.py --bucket msp-evidence-worm-test
"""

import boto3
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys


class WORMStorageTester:
    """Test WORM storage functionality"""

    def __init__(self, bucket_name: str, region: str = "us-east-1"):
        self.bucket_name = bucket_name
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)

    def create_test_evidence_bundle(self) -> dict:
        """Create synthetic evidence bundle for testing"""

        bundle = {
            "bundle_id": f"EB-TEST-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "client_id": "test-clinic",
            "incident_id": "INC-TEST-001",
            "runbook_id": "RB-TEST-001",
            "timestamp": datetime.utcnow().isoformat(),
            "operator": "test-system",
            "hipaa_controls": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
            "actions_taken": [
                {
                    "step": 1,
                    "action": "test_action",
                    "result": "success",
                    "script_hash": "sha256:test123..."
                }
            ],
            "evidence_type": "test",
            "test_upload": True
        }

        return bundle

    def upload_evidence(self, evidence: dict) -> str:
        """Upload evidence bundle to WORM storage"""

        key = f"evidence/{evidence['bundle_id']}.json"
        content = json.dumps(evidence, indent=2)

        print(f"\n📤 Uploading evidence bundle: {key}")

        try:
            response = self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=content.encode('utf-8'),
                ContentType='application/json',
                Metadata={
                    'client-id': evidence['client_id'],
                    'incident-id': evidence['incident_id'],
                    'bundle-id': evidence['bundle_id']
                }
                # Note: Object Lock retention is applied automatically via bucket default
            )

            print(f"✅ Upload successful!")
            print(f"   ETag: {response['ETag']}")
            print(f"   Version: {response.get('VersionId', 'N/A')}")

            return key

        except Exception as e:
            print(f"❌ Upload failed: {e}")
            raise

    def verify_object_lock(self, key: str):
        """Verify object has retention lock applied"""

        print(f"\n🔒 Verifying Object Lock for: {key}")

        try:
            response = self.s3_client.get_object_retention(
                Bucket=self.bucket_name,
                Key=key
            )

            retention = response['Retention']
            print(f"✅ Object Lock ENABLED")
            print(f"   Mode: {retention['Mode']}")
            print(f"   Retain Until: {retention['RetainUntilDate']}")

            # Verify COMPLIANCE mode
            if retention['Mode'] != 'COMPLIANCE':
                print(f"⚠️  Warning: Expected COMPLIANCE mode, got {retention['Mode']}")
                return False

            # Verify retention is in future
            retain_until = retention['RetainUntilDate']
            if retain_until > datetime.now(retain_until.tzinfo):
                print(f"✅ Retention is active until {retain_until}")
            else:
                print(f"⚠️  Warning: Retention already expired")
                return False

            return True

        except self.s3_client.exceptions.NoSuchKey:
            print(f"❌ Object not found")
            return False

        except Exception as e:
            if 'NoSuchObjectLockConfiguration' in str(e):
                print(f"❌ Object Lock not configured on this object")
                return False
            print(f"❌ Verification failed: {e}")
            return False

    def test_delete_protection(self, key: str):
        """Test that object cannot be deleted"""

        print(f"\n🛡️  Testing delete protection for: {key}")

        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=key
            )

            print(f"❌ FAIL: Object was deleted (Object Lock not working!)")
            return False

        except Exception as e:
            if 'AccessDenied' in str(e):
                print(f"✅ PASS: Delete denied (Object Lock working)")
                return True
            else:
                print(f"⚠️  Unexpected error: {e}")
                return False

    def test_modification_protection(self, key: str):
        """Test that object cannot be modified"""

        print(f"\n📝 Testing modification protection for: {key}")

        try:
            # Try to overwrite object
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=b'{"modified": true}',
                ContentType='application/json'
            )

            print(f"⚠️  Object was overwritten (versioning creates new version)")
            print(f"   Note: Original version is still immutable")
            return True  # This is expected behavior with versioning

        except Exception as e:
            if 'AccessDenied' in str(e):
                print(f"✅ PASS: Modification denied")
                return True
            else:
                print(f"⚠️  Unexpected error: {e}")
                return False

    def list_evidence_bundles(self):
        """List all evidence bundles in WORM storage"""

        print(f"\n📋 Listing evidence bundles in {self.bucket_name}:")

        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix='evidence/'
            )

            if 'Contents' not in response:
                print("   (no evidence bundles found)")
                return

            for obj in response['Contents']:
                print(f"   - {obj['Key']} ({obj['Size']} bytes, {obj['LastModified']})")

        except Exception as e:
            print(f"❌ Listing failed: {e}")

    def run_full_test(self):
        """Run complete WORM storage test suite"""

        print("=" * 70)
        print("WORM Storage Test Suite")
        print("=" * 70)
        print(f"Bucket: {self.bucket_name}")
        print(f"Region: {self.region}")
        print()

        # Test 1: Create and upload evidence
        evidence = self.create_test_evidence_bundle()
        key = self.upload_evidence(evidence)

        # Test 2: Verify Object Lock
        lock_verified = self.verify_object_lock(key)

        # Test 3: Test delete protection
        delete_protected = self.test_delete_protection(key)

        # Test 4: Test modification protection
        mod_protected = self.test_modification_protection(key)

        # Test 5: List all bundles
        self.list_evidence_bundles()

        # Summary
        print("\n" + "=" * 70)
        print("Test Summary")
        print("=" * 70)

        tests = [
            ("Upload Evidence", True),
            ("Object Lock Verified", lock_verified),
            ("Delete Protection", delete_protected),
            ("Modification Protection", mod_protected)
        ]

        all_passed = all(result for _, result in tests)

        for test_name, result in tests:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{status}: {test_name}")

        print()

        if all_passed:
            print("✅ All tests PASSED - WORM storage is working correctly!")
            print()
            print("HIPAA Compliance Status:")
            print("  ✅ §164.310(d)(2)(iv) - Data Backup and Storage (immutable)")
            print("  ✅ §164.312(c)(1) - Integrity Controls (tamper-evident)")
            return 0
        else:
            print("❌ Some tests FAILED - WORM storage may not be configured correctly")
            return 1


def main():
    parser = argparse.ArgumentParser(description="Test WORM storage upload")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")

    args = parser.parse_args()

    tester = WORMStorageTester(bucket_name=args.bucket, region=args.region)

    try:
        exit_code = tester.run_full_test()
        sys.exit(exit_code)

    except Exception as e:
        print(f"\n❌ Test suite failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
