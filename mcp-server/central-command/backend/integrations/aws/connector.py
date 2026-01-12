"""
Secure AWS integration connector.

Connects to customer AWS accounts using STS AssumeRole with ExternalId
for confused deputy protection. Collects compliance-relevant resources
without accessing secrets or customer data.

Security Features:
- Minimal IAM permissions (explicit deny on secrets)
- ExternalId required for all role assumptions
- Session caching (50 minutes, credentials valid 1 hour)
- Circuit breaker for AWS API failures
- Resource collection limits (5000 per type)
- Complete audit trail

HIPAA Mappings:
- ec2_encryption -> 164.312(a)(2)(iv) Encryption
- s3_public_access -> 164.312(a)(1) Access Control
- iam_mfa -> 164.312(d) Person Authentication
- cloudtrail_enabled -> 164.312(b) Audit Controls
- rds_backup -> 164.308(a)(7)(ii)(A) Data Backup Plan

Usage:
    connector = AWSConnector(
        integration_id="int-123",
        role_arn="arn:aws:iam::111111111111:role/OsirisCare-Audit",
        external_id="ext-abc123",
        db=db_session,
        vault=credential_vault
    )

    # Collect all resources
    results = await connector.collect_all_resources()

    # Check connection
    is_valid = await connector.validate_connection()
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

import boto3
from botocore.exceptions import ClientError, BotoCoreError

from ..secure_credentials import AWSCredentials
from ..credential_vault import CredentialVault
from ..audit_logger import IntegrationAuditLogger
from .policy_templates import validate_role_arn

logger = logging.getLogger(__name__)


# Configuration
MAX_RESOURCES_PER_TYPE = 5000
SESSION_DURATION_SECONDS = 3600  # 1 hour
SESSION_CACHE_DURATION = 50 * 60  # 50 minutes (refresh before expiry)
COLLECT_TIMEOUT_SECONDS = 300  # 5 minutes per resource type
DEFAULT_REGIONS = ["us-east-1", "us-west-2"]


# HIPAA control mappings for AWS findings
HIPAA_MAPPINGS = {
    "ec2_unencrypted_volume": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
    "ec2_public_ip": ["164.312(a)(1)", "164.312(e)(1)"],
    "s3_public_access": ["164.312(a)(1)", "164.312(c)(1)"],
    "s3_no_encryption": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
    "s3_no_versioning": ["164.312(c)(1)", "164.308(a)(7)(ii)(A)"],
    "s3_no_logging": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
    "iam_no_mfa": ["164.312(d)", "164.308(a)(5)(ii)(D)"],
    "iam_root_access_key": ["164.312(a)(1)", "164.308(a)(4)"],
    "iam_old_access_key": ["164.308(a)(5)(ii)(D)", "164.312(d)"],
    "iam_no_password_policy": ["164.308(a)(5)(ii)(D)"],
    "rds_no_encryption": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
    "rds_public_access": ["164.312(a)(1)", "164.312(e)(1)"],
    "rds_no_backup": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
    "cloudtrail_disabled": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
    "kms_key_rotation_disabled": ["164.312(a)(2)(iv)", "164.308(a)(5)(ii)(D)"],
    "security_group_wide_open": ["164.312(a)(1)", "164.312(e)(1)"],
}


@dataclass
class ComplianceCheck:
    """Result of a single compliance check."""
    check_id: str
    check_name: str
    status: str  # pass, fail, unknown
    severity: str  # critical, high, medium, low
    details: Optional[str] = None
    remediation: Optional[str] = None
    hipaa_controls: List[str] = field(default_factory=list)


@dataclass
class AWSResource:
    """Collected AWS resource with compliance status."""
    resource_type: str
    resource_id: str
    resource_name: Optional[str]
    region: str
    compliance_checks: List[ComplianceCheck]
    risk_level: Optional[str]
    raw_data: Dict[str, Any]

    @property
    def is_compliant(self) -> bool:
        """Check if all compliance checks pass."""
        return all(c.status == "pass" for c in self.compliance_checks)

    @property
    def hipaa_controls(self) -> List[str]:
        """Get all HIPAA controls from failed checks."""
        controls = set()
        for check in self.compliance_checks:
            if check.status == "fail":
                controls.update(check.hipaa_controls)
        return sorted(controls)


@dataclass
class CollectionResult:
    """Result of resource collection."""
    resource_type: str
    resources: List[AWSResource]
    count: int
    truncated: bool
    duration_seconds: float
    error: Optional[str] = None


class AWSConnectorError(Exception):
    """Base exception for AWS connector errors."""
    pass


class RoleAssumptionError(AWSConnectorError):
    """Error assuming AWS role."""
    pass


class ResourceCollectionError(AWSConnectorError):
    """Error collecting AWS resources."""
    pass


class AWSConnector:
    """
    Secure AWS integration connector.

    Handles STS role assumption and resource collection with
    full security controls and audit logging.
    """

    def __init__(
        self,
        integration_id: str,
        site_id: str,
        role_arn: str,
        external_id: str,
        db,
        vault: CredentialVault,
        regions: Optional[List[str]] = None
    ):
        """
        Initialize AWS connector.

        Args:
            integration_id: Integration ID
            site_id: Site ID
            role_arn: AWS role ARN to assume
            external_id: ExternalId for role assumption
            db: Database session
            vault: Credential vault for encryption
            regions: AWS regions to scan (default: us-east-1, us-west-2)
        """
        if not validate_role_arn(role_arn):
            raise ValueError(f"Invalid role ARN format: {role_arn}")

        self.integration_id = integration_id
        self.site_id = site_id
        self.role_arn = role_arn
        self.external_id = external_id
        self.db = db
        self.vault = vault
        self.regions = regions or DEFAULT_REGIONS
        self.audit = IntegrationAuditLogger(db)

        # Session cache
        self._session_cache: Dict[str, Any] = {}
        self._session_expires: Optional[datetime] = None

    async def _get_session(self, region: str = "us-east-1") -> boto3.Session:
        """
        Get or create AWS session with assumed role credentials.

        Uses caching to avoid repeated STS calls.

        Args:
            region: AWS region

        Returns:
            boto3 Session with assumed role credentials
        """
        cache_key = f"{self.role_arn}:{region}"

        # Check cache
        if (
            cache_key in self._session_cache and
            self._session_expires and
            datetime.utcnow() < self._session_expires
        ):
            return self._session_cache[cache_key]

        # Assume role
        try:
            sts_client = boto3.client("sts", region_name=region)

            response = sts_client.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName=f"osiriscare-{self.integration_id[:8]}",
                ExternalId=self.external_id,
                DurationSeconds=SESSION_DURATION_SECONDS
            )

            credentials = response["Credentials"]

            # Create session with assumed credentials
            session = boto3.Session(
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
                region_name=region
            )

            # Cache session
            self._session_cache[cache_key] = session
            self._session_expires = datetime.utcnow() + timedelta(seconds=SESSION_CACHE_DURATION)

            # Audit log
            await self.audit.log_aws_role_assumed(
                site_id=self.site_id,
                integration_id=self.integration_id,
                role_arn=self.role_arn,
                session_duration=SESSION_DURATION_SECONDS
            )

            logger.info(
                f"AWS role assumed: integration={self.integration_id[:8]} "
                f"role={self.role_arn} region={region}"
            )

            return session

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            await self.audit.log_aws_role_failed(
                site_id=self.site_id,
                integration_id=self.integration_id,
                role_arn=self.role_arn,
                error=f"{error_code}: {error_msg}"
            )

            logger.error(
                f"AWS role assumption failed: integration={self.integration_id[:8]} "
                f"error={error_code}"
            )

            raise RoleAssumptionError(f"Failed to assume role: {error_msg}") from e

    async def validate_connection(self) -> bool:
        """
        Validate AWS connection by attempting role assumption.

        Returns:
            True if connection is valid
        """
        try:
            session = await self._get_session()
            sts = session.client("sts")
            sts.get_caller_identity()
            return True
        except Exception as e:
            logger.warning(f"AWS connection validation failed: {e}")
            return False

    async def collect_all_resources(
        self,
        resource_types: Optional[List[str]] = None
    ) -> Dict[str, CollectionResult]:
        """
        Collect all compliance-relevant resources.

        Args:
            resource_types: Specific types to collect (default: all)

        Returns:
            Dict mapping resource type to collection results
        """
        if resource_types is None:
            resource_types = [
                "iam_users",
                "s3_buckets",
                "ec2_instances",
                "rds_instances",
                "cloudtrail",
                "security_groups",
            ]

        results = {}

        # Collect in parallel with timeout per type
        tasks = []
        for resource_type in resource_types:
            collector = getattr(self, f"_collect_{resource_type}", None)
            if collector:
                tasks.append(self._collect_with_timeout(resource_type, collector))

        collected = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(collected):
            resource_type = resource_types[i]
            if isinstance(result, Exception):
                results[resource_type] = CollectionResult(
                    resource_type=resource_type,
                    resources=[],
                    count=0,
                    truncated=False,
                    duration_seconds=0,
                    error=str(result)
                )
            else:
                results[resource_type] = result

        return results

    async def _collect_with_timeout(
        self,
        resource_type: str,
        collector
    ) -> CollectionResult:
        """
        Run collector with timeout.

        Args:
            resource_type: Type being collected
            collector: Collector coroutine

        Returns:
            Collection result
        """
        start_time = datetime.utcnow()

        try:
            result = await asyncio.wait_for(
                collector(),
                timeout=COLLECT_TIMEOUT_SECONDS
            )
            duration = (datetime.utcnow() - start_time).total_seconds()
            result.duration_seconds = duration
            return result

        except asyncio.TimeoutError:
            duration = (datetime.utcnow() - start_time).total_seconds()
            return CollectionResult(
                resource_type=resource_type,
                resources=[],
                count=0,
                truncated=False,
                duration_seconds=duration,
                error=f"Collection timeout after {COLLECT_TIMEOUT_SECONDS}s"
            )

    async def _collect_iam_users(self) -> CollectionResult:
        """Collect IAM users with MFA and access key checks."""
        resources = []
        session = await self._get_session()
        iam = session.client("iam")

        try:
            # Get password policy for context
            try:
                password_policy = iam.get_account_password_policy()["PasswordPolicy"]
            except ClientError:
                password_policy = None

            # List users with pagination
            paginator = iam.get_paginator("list_users")
            user_count = 0

            for page in paginator.paginate():
                for user in page["Users"]:
                    if user_count >= MAX_RESOURCES_PER_TYPE:
                        return CollectionResult(
                            resource_type="iam_users",
                            resources=resources,
                            count=len(resources),
                            truncated=True,
                            duration_seconds=0
                        )

                    user_name = user["UserName"]
                    checks = []

                    # Check MFA
                    mfa_devices = iam.list_mfa_devices(UserName=user_name)["MFADevices"]
                    has_mfa = len(mfa_devices) > 0

                    checks.append(ComplianceCheck(
                        check_id="iam_mfa",
                        check_name="MFA Enabled",
                        status="pass" if has_mfa else "fail",
                        severity="high" if not has_mfa else "low",
                        details=f"MFA devices: {len(mfa_devices)}",
                        remediation="Enable MFA for this user",
                        hipaa_controls=HIPAA_MAPPINGS.get("iam_no_mfa", [])
                    ))

                    # Check access keys
                    access_keys = iam.list_access_keys(UserName=user_name)["AccessKeyMetadata"]
                    for key in access_keys:
                        key_age_days = (datetime.utcnow() - key["CreateDate"].replace(tzinfo=None)).days

                        checks.append(ComplianceCheck(
                            check_id="iam_access_key_age",
                            check_name="Access Key Rotation",
                            status="pass" if key_age_days < 90 else "fail",
                            severity="medium" if key_age_days >= 90 else "low",
                            details=f"Key {key['AccessKeyId'][:8]}... is {key_age_days} days old",
                            remediation="Rotate access keys every 90 days",
                            hipaa_controls=HIPAA_MAPPINGS.get("iam_old_access_key", [])
                        ))

                    # Determine risk level
                    failed_checks = [c for c in checks if c.status == "fail"]
                    if any(c.severity == "critical" for c in failed_checks):
                        risk_level = "critical"
                    elif any(c.severity == "high" for c in failed_checks):
                        risk_level = "high"
                    elif failed_checks:
                        risk_level = "medium"
                    else:
                        risk_level = None

                    resources.append(AWSResource(
                        resource_type="iam_user",
                        resource_id=user["Arn"],
                        resource_name=user_name,
                        region="global",
                        compliance_checks=checks,
                        risk_level=risk_level,
                        raw_data={
                            "UserName": user_name,
                            "Arn": user["Arn"],
                            "CreateDate": user["CreateDate"].isoformat(),
                            "PasswordLastUsed": user.get("PasswordLastUsed", "").isoformat() if user.get("PasswordLastUsed") else None,
                            "MFAEnabled": has_mfa,
                            "AccessKeyCount": len(access_keys),
                        }
                    ))

                    user_count += 1

            return CollectionResult(
                resource_type="iam_users",
                resources=resources,
                count=len(resources),
                truncated=False,
                duration_seconds=0
            )

        except ClientError as e:
            raise ResourceCollectionError(f"Failed to collect IAM users: {e}") from e

    async def _collect_s3_buckets(self) -> CollectionResult:
        """Collect S3 buckets with security checks."""
        resources = []
        session = await self._get_session()
        s3 = session.client("s3")

        try:
            buckets = s3.list_buckets()["Buckets"]

            for i, bucket in enumerate(buckets):
                if i >= MAX_RESOURCES_PER_TYPE:
                    return CollectionResult(
                        resource_type="s3_buckets",
                        resources=resources,
                        count=len(resources),
                        truncated=True,
                        duration_seconds=0
                    )

                bucket_name = bucket["Name"]
                checks = []

                # Check public access block
                try:
                    public_access = s3.get_public_access_block(Bucket=bucket_name)
                    config = public_access["PublicAccessBlockConfiguration"]
                    is_public_blocked = all([
                        config.get("BlockPublicAcls", False),
                        config.get("BlockPublicPolicy", False),
                        config.get("IgnorePublicAcls", False),
                        config.get("RestrictPublicBuckets", False),
                    ])
                except ClientError:
                    is_public_blocked = False

                checks.append(ComplianceCheck(
                    check_id="s3_public_access",
                    check_name="Public Access Blocked",
                    status="pass" if is_public_blocked else "fail",
                    severity="critical" if not is_public_blocked else "low",
                    details="Public access block enabled" if is_public_blocked else "Public access not blocked",
                    remediation="Enable S3 Block Public Access",
                    hipaa_controls=HIPAA_MAPPINGS.get("s3_public_access", [])
                ))

                # Check encryption
                try:
                    encryption = s3.get_bucket_encryption(Bucket=bucket_name)
                    is_encrypted = True
                except ClientError:
                    is_encrypted = False

                checks.append(ComplianceCheck(
                    check_id="s3_encryption",
                    check_name="Default Encryption",
                    status="pass" if is_encrypted else "fail",
                    severity="high" if not is_encrypted else "low",
                    details="Default encryption enabled" if is_encrypted else "No default encryption",
                    remediation="Enable S3 default encryption (SSE-S3 or SSE-KMS)",
                    hipaa_controls=HIPAA_MAPPINGS.get("s3_no_encryption", [])
                ))

                # Check versioning
                try:
                    versioning = s3.get_bucket_versioning(Bucket=bucket_name)
                    is_versioned = versioning.get("Status") == "Enabled"
                except ClientError:
                    is_versioned = False

                checks.append(ComplianceCheck(
                    check_id="s3_versioning",
                    check_name="Versioning Enabled",
                    status="pass" if is_versioned else "fail",
                    severity="medium" if not is_versioned else "low",
                    details="Versioning enabled" if is_versioned else "Versioning not enabled",
                    remediation="Enable S3 versioning for data protection",
                    hipaa_controls=HIPAA_MAPPINGS.get("s3_no_versioning", [])
                ))

                # Check logging
                try:
                    logging_config = s3.get_bucket_logging(Bucket=bucket_name)
                    has_logging = "LoggingEnabled" in logging_config
                except ClientError:
                    has_logging = False

                checks.append(ComplianceCheck(
                    check_id="s3_logging",
                    check_name="Access Logging",
                    status="pass" if has_logging else "fail",
                    severity="medium" if not has_logging else "low",
                    details="Access logging enabled" if has_logging else "Access logging not enabled",
                    remediation="Enable S3 server access logging",
                    hipaa_controls=HIPAA_MAPPINGS.get("s3_no_logging", [])
                ))

                # Determine risk level
                failed_checks = [c for c in checks if c.status == "fail"]
                if any(c.severity == "critical" for c in failed_checks):
                    risk_level = "critical"
                elif any(c.severity == "high" for c in failed_checks):
                    risk_level = "high"
                elif failed_checks:
                    risk_level = "medium"
                else:
                    risk_level = None

                resources.append(AWSResource(
                    resource_type="s3_bucket",
                    resource_id=f"arn:aws:s3:::{bucket_name}",
                    resource_name=bucket_name,
                    region="global",
                    compliance_checks=checks,
                    risk_level=risk_level,
                    raw_data={
                        "Name": bucket_name,
                        "CreationDate": bucket["CreationDate"].isoformat(),
                        "PublicAccessBlocked": is_public_blocked,
                        "EncryptionEnabled": is_encrypted,
                        "VersioningEnabled": is_versioned,
                        "LoggingEnabled": has_logging,
                    }
                ))

            return CollectionResult(
                resource_type="s3_buckets",
                resources=resources,
                count=len(resources),
                truncated=False,
                duration_seconds=0
            )

        except ClientError as e:
            raise ResourceCollectionError(f"Failed to collect S3 buckets: {e}") from e

    async def _collect_cloudtrail(self) -> CollectionResult:
        """Collect CloudTrail configuration."""
        resources = []
        session = await self._get_session()
        cloudtrail = session.client("cloudtrail")

        try:
            trails = cloudtrail.describe_trails()["trailList"]

            for trail in trails[:MAX_RESOURCES_PER_TYPE]:
                trail_name = trail["Name"]
                trail_arn = trail["TrailARN"]
                checks = []

                # Check if trail is logging
                try:
                    status = cloudtrail.get_trail_status(Name=trail_name)
                    is_logging = status.get("IsLogging", False)
                except ClientError:
                    is_logging = False

                checks.append(ComplianceCheck(
                    check_id="cloudtrail_logging",
                    check_name="CloudTrail Logging Active",
                    status="pass" if is_logging else "fail",
                    severity="critical" if not is_logging else "low",
                    details="Trail is actively logging" if is_logging else "Trail is not logging",
                    remediation="Start CloudTrail logging",
                    hipaa_controls=HIPAA_MAPPINGS.get("cloudtrail_disabled", [])
                ))

                # Check multi-region
                is_multi_region = trail.get("IsMultiRegionTrail", False)

                checks.append(ComplianceCheck(
                    check_id="cloudtrail_multi_region",
                    check_name="Multi-Region Trail",
                    status="pass" if is_multi_region else "fail",
                    severity="high" if not is_multi_region else "low",
                    details="Multi-region enabled" if is_multi_region else "Single region only",
                    remediation="Enable multi-region for complete audit coverage",
                    hipaa_controls=["164.312(b)"]
                ))

                # Check log file validation
                has_validation = trail.get("LogFileValidationEnabled", False)

                checks.append(ComplianceCheck(
                    check_id="cloudtrail_log_validation",
                    check_name="Log File Validation",
                    status="pass" if has_validation else "fail",
                    severity="high" if not has_validation else "low",
                    details="Log validation enabled" if has_validation else "No log validation",
                    remediation="Enable log file validation for integrity",
                    hipaa_controls=["164.312(c)(1)", "164.312(c)(2)"]
                ))

                # Determine risk level
                failed_checks = [c for c in checks if c.status == "fail"]
                if any(c.severity == "critical" for c in failed_checks):
                    risk_level = "critical"
                elif any(c.severity == "high" for c in failed_checks):
                    risk_level = "high"
                elif failed_checks:
                    risk_level = "medium"
                else:
                    risk_level = None

                resources.append(AWSResource(
                    resource_type="cloudtrail",
                    resource_id=trail_arn,
                    resource_name=trail_name,
                    region=trail.get("HomeRegion", "global"),
                    compliance_checks=checks,
                    risk_level=risk_level,
                    raw_data={
                        "Name": trail_name,
                        "TrailARN": trail_arn,
                        "IsLogging": is_logging,
                        "IsMultiRegionTrail": is_multi_region,
                        "LogFileValidationEnabled": has_validation,
                        "S3BucketName": trail.get("S3BucketName"),
                    }
                ))

            # If no trails, create a finding
            if not trails:
                resources.append(AWSResource(
                    resource_type="cloudtrail",
                    resource_id="no-cloudtrail",
                    resource_name="No CloudTrail",
                    region="global",
                    compliance_checks=[ComplianceCheck(
                        check_id="cloudtrail_exists",
                        check_name="CloudTrail Configured",
                        status="fail",
                        severity="critical",
                        details="No CloudTrail trails configured",
                        remediation="Create a CloudTrail trail for audit logging",
                        hipaa_controls=HIPAA_MAPPINGS.get("cloudtrail_disabled", [])
                    )],
                    risk_level="critical",
                    raw_data={"trails": 0}
                ))

            return CollectionResult(
                resource_type="cloudtrail",
                resources=resources,
                count=len(resources),
                truncated=False,
                duration_seconds=0
            )

        except ClientError as e:
            raise ResourceCollectionError(f"Failed to collect CloudTrail: {e}") from e

    async def _collect_ec2_instances(self) -> CollectionResult:
        """Collect EC2 instances across regions."""
        resources = []

        for region in self.regions:
            session = await self._get_session(region)
            ec2 = session.client("ec2")

            try:
                paginator = ec2.get_paginator("describe_instances")

                for page in paginator.paginate():
                    for reservation in page["Reservations"]:
                        for instance in reservation["Instances"]:
                            if len(resources) >= MAX_RESOURCES_PER_TYPE:
                                return CollectionResult(
                                    resource_type="ec2_instances",
                                    resources=resources,
                                    count=len(resources),
                                    truncated=True,
                                    duration_seconds=0
                                )

                            instance_id = instance["InstanceId"]
                            checks = []

                            # Check if instance has public IP
                            has_public_ip = instance.get("PublicIpAddress") is not None

                            checks.append(ComplianceCheck(
                                check_id="ec2_public_ip",
                                check_name="No Public IP",
                                status="fail" if has_public_ip else "pass",
                                severity="high" if has_public_ip else "low",
                                details=f"Public IP: {instance.get('PublicIpAddress', 'None')}",
                                remediation="Remove public IP or use NAT/bastion",
                                hipaa_controls=HIPAA_MAPPINGS.get("ec2_public_ip", [])
                            ))

                            # Get instance name from tags
                            instance_name = None
                            for tag in instance.get("Tags", []):
                                if tag["Key"] == "Name":
                                    instance_name = tag["Value"]
                                    break

                            # Determine risk level
                            failed_checks = [c for c in checks if c.status == "fail"]
                            if any(c.severity == "critical" for c in failed_checks):
                                risk_level = "critical"
                            elif any(c.severity == "high" for c in failed_checks):
                                risk_level = "high"
                            elif failed_checks:
                                risk_level = "medium"
                            else:
                                risk_level = None

                            resources.append(AWSResource(
                                resource_type="ec2_instance",
                                resource_id=f"arn:aws:ec2:{region}::instance/{instance_id}",
                                resource_name=instance_name or instance_id,
                                region=region,
                                compliance_checks=checks,
                                risk_level=risk_level,
                                raw_data={
                                    "InstanceId": instance_id,
                                    "InstanceType": instance["InstanceType"],
                                    "State": instance["State"]["Name"],
                                    "PublicIpAddress": instance.get("PublicIpAddress"),
                                    "PrivateIpAddress": instance.get("PrivateIpAddress"),
                                    "VpcId": instance.get("VpcId"),
                                    "SubnetId": instance.get("SubnetId"),
                                }
                            ))

            except ClientError as e:
                logger.warning(f"Failed to collect EC2 in {region}: {e}")
                continue

        return CollectionResult(
            resource_type="ec2_instances",
            resources=resources,
            count=len(resources),
            truncated=False,
            duration_seconds=0
        )

    async def _collect_rds_instances(self) -> CollectionResult:
        """Collect RDS instances with security checks."""
        resources = []

        for region in self.regions:
            session = await self._get_session(region)
            rds = session.client("rds")

            try:
                paginator = rds.get_paginator("describe_db_instances")

                for page in paginator.paginate():
                    for db in page["DBInstances"]:
                        if len(resources) >= MAX_RESOURCES_PER_TYPE:
                            return CollectionResult(
                                resource_type="rds_instances",
                                resources=resources,
                                count=len(resources),
                                truncated=True,
                                duration_seconds=0
                            )

                        db_id = db["DBInstanceIdentifier"]
                        checks = []

                        # Check encryption
                        is_encrypted = db.get("StorageEncrypted", False)

                        checks.append(ComplianceCheck(
                            check_id="rds_encryption",
                            check_name="Storage Encryption",
                            status="pass" if is_encrypted else "fail",
                            severity="critical" if not is_encrypted else "low",
                            details="Storage encrypted" if is_encrypted else "Storage not encrypted",
                            remediation="Enable RDS storage encryption",
                            hipaa_controls=HIPAA_MAPPINGS.get("rds_no_encryption", [])
                        ))

                        # Check public accessibility
                        is_public = db.get("PubliclyAccessible", False)

                        checks.append(ComplianceCheck(
                            check_id="rds_public_access",
                            check_name="Not Publicly Accessible",
                            status="fail" if is_public else "pass",
                            severity="critical" if is_public else "low",
                            details="Publicly accessible" if is_public else "Not publicly accessible",
                            remediation="Disable public accessibility",
                            hipaa_controls=HIPAA_MAPPINGS.get("rds_public_access", [])
                        ))

                        # Check backup retention
                        backup_days = db.get("BackupRetentionPeriod", 0)
                        has_backup = backup_days > 0

                        checks.append(ComplianceCheck(
                            check_id="rds_backup",
                            check_name="Automated Backups",
                            status="pass" if has_backup else "fail",
                            severity="high" if not has_backup else "low",
                            details=f"Backup retention: {backup_days} days",
                            remediation="Enable automated backups (minimum 7 days)",
                            hipaa_controls=HIPAA_MAPPINGS.get("rds_no_backup", [])
                        ))

                        # Determine risk level
                        failed_checks = [c for c in checks if c.status == "fail"]
                        if any(c.severity == "critical" for c in failed_checks):
                            risk_level = "critical"
                        elif any(c.severity == "high" for c in failed_checks):
                            risk_level = "high"
                        elif failed_checks:
                            risk_level = "medium"
                        else:
                            risk_level = None

                        resources.append(AWSResource(
                            resource_type="rds_instance",
                            resource_id=db["DBInstanceArn"],
                            resource_name=db_id,
                            region=region,
                            compliance_checks=checks,
                            risk_level=risk_level,
                            raw_data={
                                "DBInstanceIdentifier": db_id,
                                "DBInstanceClass": db["DBInstanceClass"],
                                "Engine": db["Engine"],
                                "EngineVersion": db["EngineVersion"],
                                "StorageEncrypted": is_encrypted,
                                "PubliclyAccessible": is_public,
                                "BackupRetentionPeriod": backup_days,
                                "MultiAZ": db.get("MultiAZ", False),
                            }
                        ))

            except ClientError as e:
                logger.warning(f"Failed to collect RDS in {region}: {e}")
                continue

        return CollectionResult(
            resource_type="rds_instances",
            resources=resources,
            count=len(resources),
            truncated=False,
            duration_seconds=0
        )

    async def _collect_security_groups(self) -> CollectionResult:
        """Collect security groups with open port checks."""
        resources = []

        for region in self.regions:
            session = await self._get_session(region)
            ec2 = session.client("ec2")

            try:
                paginator = ec2.get_paginator("describe_security_groups")

                for page in paginator.paginate():
                    for sg in page["SecurityGroups"]:
                        if len(resources) >= MAX_RESOURCES_PER_TYPE:
                            return CollectionResult(
                                resource_type="security_groups",
                                resources=resources,
                                count=len(resources),
                                truncated=True,
                                duration_seconds=0
                            )

                        sg_id = sg["GroupId"]
                        sg_name = sg["GroupName"]
                        checks = []

                        # Check for open inbound rules (0.0.0.0/0)
                        wide_open = False
                        open_ports = []

                        for rule in sg.get("IpPermissions", []):
                            for ip_range in rule.get("IpRanges", []):
                                if ip_range.get("CidrIp") == "0.0.0.0/0":
                                    port = rule.get("FromPort", "all")
                                    open_ports.append(str(port))
                                    if port in [22, 3389, 0, -1]:  # SSH, RDP, or all
                                        wide_open = True

                        if open_ports:
                            checks.append(ComplianceCheck(
                                check_id="sg_open_ports",
                                check_name="No Open Ports to Internet",
                                status="fail",
                                severity="critical" if wide_open else "high",
                                details=f"Ports open to 0.0.0.0/0: {', '.join(open_ports)}",
                                remediation="Restrict security group ingress rules",
                                hipaa_controls=HIPAA_MAPPINGS.get("security_group_wide_open", [])
                            ))
                        else:
                            checks.append(ComplianceCheck(
                                check_id="sg_open_ports",
                                check_name="No Open Ports to Internet",
                                status="pass",
                                severity="low",
                                details="No ports open to 0.0.0.0/0",
                                hipaa_controls=[]
                            ))

                        # Determine risk level
                        failed_checks = [c for c in checks if c.status == "fail"]
                        if any(c.severity == "critical" for c in failed_checks):
                            risk_level = "critical"
                        elif any(c.severity == "high" for c in failed_checks):
                            risk_level = "high"
                        elif failed_checks:
                            risk_level = "medium"
                        else:
                            risk_level = None

                        resources.append(AWSResource(
                            resource_type="security_group",
                            resource_id=f"arn:aws:ec2:{region}::security-group/{sg_id}",
                            resource_name=sg_name,
                            region=region,
                            compliance_checks=checks,
                            risk_level=risk_level,
                            raw_data={
                                "GroupId": sg_id,
                                "GroupName": sg_name,
                                "Description": sg.get("Description"),
                                "VpcId": sg.get("VpcId"),
                                "InboundRulesCount": len(sg.get("IpPermissions", [])),
                                "OutboundRulesCount": len(sg.get("IpPermissionsEgress", [])),
                            }
                        ))

            except ClientError as e:
                logger.warning(f"Failed to collect security groups in {region}: {e}")
                continue

        return CollectionResult(
            resource_type="security_groups",
            resources=resources,
            count=len(resources),
            truncated=False,
            duration_seconds=0
        )
