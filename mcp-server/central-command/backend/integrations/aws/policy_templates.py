"""
AWS IAM policy templates for OsirisCare integration.

CRITICAL SECURITY REQUIREMENT: NEVER use AWS managed SecurityAudit policy.
It includes permissions like secretsmanager:GetSecretValue which could
expose customer secrets.

Our custom policy:
- Only allows specific Describe* and Get* actions for compliance checks
- Explicitly DENIES access to secrets, passwords, parameters
- Uses ExternalId to prevent confused deputy attacks
- Follows least-privilege principle

Usage:
    # Get policy JSON for customer to create
    policy = get_audit_policy()

    # Get trust policy for role creation
    trust = get_trust_policy(
        osiriscare_account="123456789012",
        external_id="ext-abc123"
    )

    # Get CloudFormation template for self-service setup
    cfn = get_cloudformation_template()
"""

import json
from typing import Optional, List

# OsirisCare AWS Account ID (for cross-account role assumption)
OSIRISCARE_AWS_ACCOUNT = "YOUR_ACCOUNT_ID"  # Set via environment

# IAM policy for read-only compliance auditing
OSIRISCARE_AUDIT_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowIAMReadOnly",
            "Effect": "Allow",
            "Action": [
                "iam:GetAccountPasswordPolicy",
                "iam:GetAccountSummary",
                "iam:ListUsers",
                "iam:ListGroups",
                "iam:ListRoles",
                "iam:ListPolicies",
                "iam:ListMFADevices",
                "iam:ListVirtualMFADevices",
                "iam:ListAccessKeys",
                "iam:GetAccessKeyLastUsed",
                "iam:GetUser",
                "iam:GetLoginProfile",
                "iam:ListUserTags",
                "iam:ListAttachedUserPolicies",
                "iam:ListUserPolicies",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowS3BucketReadOnly",
            "Effect": "Allow",
            "Action": [
                "s3:GetBucketAcl",
                "s3:GetBucketPolicy",
                "s3:GetBucketPolicyStatus",
                "s3:GetBucketPublicAccessBlock",
                "s3:GetAccountPublicAccessBlock",
                "s3:GetBucketEncryption",
                "s3:GetBucketVersioning",
                "s3:GetBucketLogging",
                "s3:GetBucketLocation",
                "s3:GetBucketTagging",
                "s3:ListBucket",
                "s3:ListAllMyBuckets",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowEC2ReadOnly",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeInstances",
                "ec2:DescribeVolumes",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeVpcs",
                "ec2:DescribeSubnets",
                "ec2:DescribeNetworkAcls",
                "ec2:DescribeFlowLogs",
                "ec2:DescribeSnapshots",
                "ec2:DescribeImages",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowRDSReadOnly",
            "Effect": "Allow",
            "Action": [
                "rds:DescribeDBInstances",
                "rds:DescribeDBClusters",
                "rds:DescribeDBSnapshots",
                "rds:DescribeDBClusterSnapshots",
                "rds:DescribeDBSecurityGroups",
                "rds:DescribeDBSubnetGroups",
                "rds:ListTagsForResource",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowCloudTrailReadOnly",
            "Effect": "Allow",
            "Action": [
                "cloudtrail:DescribeTrails",
                "cloudtrail:GetTrailStatus",
                "cloudtrail:GetEventSelectors",
                "cloudtrail:ListTrails",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowKMSReadOnly",
            "Effect": "Allow",
            "Action": [
                "kms:ListKeys",
                "kms:ListAliases",
                "kms:DescribeKey",
                "kms:GetKeyPolicy",
                "kms:GetKeyRotationStatus",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowConfigReadOnly",
            "Effect": "Allow",
            "Action": [
                "config:DescribeConfigurationRecorders",
                "config:DescribeConfigurationRecorderStatus",
                "config:DescribeDeliveryChannels",
                "config:DescribeDeliveryChannelStatus",
                "config:DescribeConfigRules",
                "config:DescribeComplianceByConfigRule",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowGuardDutyReadOnly",
            "Effect": "Allow",
            "Action": [
                "guardduty:ListDetectors",
                "guardduty:GetDetector",
                "guardduty:GetFindings",
                "guardduty:ListFindings",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowSecurityHubReadOnly",
            "Effect": "Allow",
            "Action": [
                "securityhub:GetEnabledStandards",
                "securityhub:GetFindings",
                "securityhub:DescribeHub",
                "securityhub:DescribeStandards",
            ],
            "Resource": "*"
        },
        {
            "Sid": "AllowAccessAnalyzerReadOnly",
            "Effect": "Allow",
            "Action": [
                "access-analyzer:ListAnalyzers",
                "access-analyzer:GetAnalyzer",
                "access-analyzer:ListFindings",
            ],
            "Resource": "*"
        },
        # EXPLICIT DENY - Critical Security Controls
        {
            "Sid": "DenySecretsAccess",
            "Effect": "Deny",
            "Action": [
                # Secrets Manager
                "secretsmanager:GetSecretValue",
                "secretsmanager:GetResourcePolicy",
                "secretsmanager:DescribeSecret",
                "secretsmanager:ListSecrets",
                # SSM Parameters (may contain secrets)
                "ssm:GetParameter",
                "ssm:GetParameters",
                "ssm:GetParametersByPath",
                "ssm:GetParameterHistory",
                # KMS Decrypt (can decrypt customer data)
                "kms:Decrypt",
                "kms:GenerateDataKey",
                "kms:GenerateDataKeyWithoutPlaintext",
                # IAM Credentials
                "iam:CreateAccessKey",
                "iam:UpdateAccessKey",
                "iam:DeleteAccessKey",
                "iam:CreateLoginProfile",
                "iam:UpdateLoginProfile",
                "iam:DeleteLoginProfile",
                "iam:ChangePassword",
                # STS Session Hijacking
                "sts:GetFederationToken",
                "sts:GetSessionToken",
                # S3 Object Access (may contain PHI)
                "s3:GetObject",
                "s3:GetObjectVersion",
                "s3:ListBucketVersions",
                # RDS Data Access
                "rds-data:*",
                "rds:DownloadDBLogFilePortion",
                # CloudWatch Logs (may contain PHI)
                "logs:GetLogEvents",
                "logs:FilterLogEvents",
            ],
            "Resource": "*"
        },
        {
            "Sid": "DenyWriteActions",
            "Effect": "Deny",
            "Action": [
                # Prevent any modifications
                "iam:Create*",
                "iam:Delete*",
                "iam:Update*",
                "iam:Put*",
                "iam:Attach*",
                "iam:Detach*",
                "iam:Add*",
                "iam:Remove*",
                "s3:Put*",
                "s3:Delete*",
                "s3:Create*",
                "ec2:Create*",
                "ec2:Delete*",
                "ec2:Modify*",
                "ec2:Start*",
                "ec2:Stop*",
                "ec2:Terminate*",
                "rds:Create*",
                "rds:Delete*",
                "rds:Modify*",
                "rds:Start*",
                "rds:Stop*",
                "kms:Create*",
                "kms:Delete*",
                "kms:Disable*",
                "kms:Enable*",
                "kms:Schedule*",
            ],
            "Resource": "*"
        }
    ]
}


def get_audit_policy() -> dict:
    """
    Get the OsirisCare audit policy.

    Returns:
        IAM policy document as dict
    """
    return OSIRISCARE_AUDIT_POLICY


def get_audit_policy_json() -> str:
    """
    Get the OsirisCare audit policy as JSON string.

    Returns:
        IAM policy document as JSON
    """
    return json.dumps(OSIRISCARE_AUDIT_POLICY, indent=2)


def get_trust_policy(
    osiriscare_account: str,
    external_id: str,
    role_name: str = "osiriscare-integration-role"
) -> dict:
    """
    Generate trust policy for cross-account role assumption.

    Uses ExternalId to prevent confused deputy attacks.

    Args:
        osiriscare_account: OsirisCare AWS account ID
        external_id: Unique external ID for this integration
        role_name: Name of the role in OsirisCare account

    Returns:
        Trust policy document
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowOsirisCareAssumeRole",
                "Effect": "Allow",
                "Principal": {
                    "AWS": f"arn:aws:iam::{osiriscare_account}:role/{role_name}"
                },
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "sts:ExternalId": external_id
                    }
                }
            }
        ]
    }


def get_trust_policy_json(
    osiriscare_account: str,
    external_id: str
) -> str:
    """
    Get trust policy as JSON string.

    Args:
        osiriscare_account: OsirisCare AWS account ID
        external_id: Unique external ID

    Returns:
        Trust policy as JSON
    """
    return json.dumps(
        get_trust_policy(osiriscare_account, external_id),
        indent=2
    )


def get_cloudformation_template(
    osiriscare_account: Optional[str] = None,
    role_name: str = "OsirisCare-Compliance-Audit-Role"
) -> str:
    """
    Get CloudFormation template for customer self-service setup.

    Customers can launch this template to create the required
    IAM role with minimal permissions.

    Args:
        osiriscare_account: OsirisCare AWS account ID
        role_name: Name for the IAM role

    Returns:
        CloudFormation template as YAML string
    """
    account = osiriscare_account or OSIRISCARE_AWS_ACCOUNT

    return f"""AWSTemplateFormatVersion: '2010-09-09'
Description: >
  OsirisCare Compliance Audit Role - Provides read-only access for
  HIPAA compliance monitoring. No access to secrets or data.

Parameters:
  ExternalId:
    Type: String
    Description: >
      External ID provided by OsirisCare. Required for secure
      cross-account access (prevents confused deputy attacks).
    MinLength: 20
    MaxLength: 100
    AllowedPattern: '[a-zA-Z0-9-]+'

  OsirisCareAccountId:
    Type: String
    Description: OsirisCare AWS Account ID
    Default: '{account}'
    AllowedPattern: '[0-9]{{12}}'

Resources:
  OsirisCareAuditRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: {role_name}
      Description: >
        Read-only role for OsirisCare compliance monitoring.
        No access to secrets, parameters, or customer data.
      MaxSessionDuration: 3600  # 1 hour max
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Sid: AllowOsirisCareAssumeRole
            Effect: Allow
            Principal:
              AWS: !Sub 'arn:aws:iam::${{OsirisCareAccountId}}:role/osiriscare-integration-role'
            Action: sts:AssumeRole
            Condition:
              StringEquals:
                sts:ExternalId: !Ref ExternalId
      Tags:
        - Key: Purpose
          Value: OsirisCare Compliance Monitoring
        - Key: ManagedBy
          Value: OsirisCare CloudFormation

  OsirisCareAuditPolicy:
    Type: AWS::IAM::Policy
    Properties:
      PolicyName: OsirisCare-Compliance-Audit-Policy
      Roles:
        - !Ref OsirisCareAuditRole
      PolicyDocument:
        Version: '2012-10-17'
        Statement:
          # IAM Read-Only
          - Sid: AllowIAMReadOnly
            Effect: Allow
            Action:
              - iam:GetAccountPasswordPolicy
              - iam:GetAccountSummary
              - iam:ListUsers
              - iam:ListGroups
              - iam:ListRoles
              - iam:ListPolicies
              - iam:ListMFADevices
              - iam:ListVirtualMFADevices
              - iam:ListAccessKeys
              - iam:GetAccessKeyLastUsed
              - iam:GetUser
              - iam:GetLoginProfile
              - iam:ListUserTags
            Resource: '*'

          # S3 Read-Only (no object access)
          - Sid: AllowS3ReadOnly
            Effect: Allow
            Action:
              - s3:GetBucketAcl
              - s3:GetBucketPolicy
              - s3:GetBucketPolicyStatus
              - s3:GetBucketPublicAccessBlock
              - s3:GetAccountPublicAccessBlock
              - s3:GetBucketEncryption
              - s3:GetBucketVersioning
              - s3:GetBucketLogging
              - s3:ListAllMyBuckets
            Resource: '*'

          # EC2 Read-Only
          - Sid: AllowEC2ReadOnly
            Effect: Allow
            Action:
              - ec2:DescribeInstances
              - ec2:DescribeVolumes
              - ec2:DescribeSecurityGroups
              - ec2:DescribeVpcs
              - ec2:DescribeSubnets
              - ec2:DescribeSnapshots
            Resource: '*'

          # RDS Read-Only
          - Sid: AllowRDSReadOnly
            Effect: Allow
            Action:
              - rds:DescribeDBInstances
              - rds:DescribeDBClusters
              - rds:DescribeDBSnapshots
              - rds:ListTagsForResource
            Resource: '*'

          # CloudTrail Read-Only
          - Sid: AllowCloudTrailReadOnly
            Effect: Allow
            Action:
              - cloudtrail:DescribeTrails
              - cloudtrail:GetTrailStatus
              - cloudtrail:GetEventSelectors
            Resource: '*'

          # KMS Read-Only (no decrypt)
          - Sid: AllowKMSReadOnly
            Effect: Allow
            Action:
              - kms:ListKeys
              - kms:ListAliases
              - kms:DescribeKey
              - kms:GetKeyRotationStatus
            Resource: '*'

          # Security Services Read-Only
          - Sid: AllowSecurityServicesReadOnly
            Effect: Allow
            Action:
              - guardduty:ListDetectors
              - guardduty:GetDetector
              - securityhub:GetEnabledStandards
              - securityhub:DescribeHub
              - access-analyzer:ListAnalyzers
            Resource: '*'

          # EXPLICIT DENY - Prevent access to secrets and data
          - Sid: DenySecretsAndData
            Effect: Deny
            Action:
              - secretsmanager:GetSecretValue
              - secretsmanager:DescribeSecret
              - secretsmanager:ListSecrets
              - ssm:GetParameter
              - ssm:GetParameters
              - ssm:GetParametersByPath
              - kms:Decrypt
              - kms:GenerateDataKey
              - s3:GetObject
              - s3:GetObjectVersion
              - rds-data:*
              - logs:GetLogEvents
              - logs:FilterLogEvents
            Resource: '*'

          # EXPLICIT DENY - Prevent any modifications
          - Sid: DenyAllWriteActions
            Effect: Deny
            Action:
              - iam:Create*
              - iam:Delete*
              - iam:Update*
              - iam:Attach*
              - iam:Detach*
              - s3:Put*
              - s3:Delete*
              - ec2:Create*
              - ec2:Delete*
              - ec2:Modify*
              - ec2:Terminate*
              - rds:Create*
              - rds:Delete*
              - rds:Modify*
            Resource: '*'

Outputs:
  RoleArn:
    Description: ARN of the OsirisCare audit role
    Value: !GetAtt OsirisCareAuditRole.Arn
    Export:
      Name: OsirisCareAuditRoleArn

  RoleName:
    Description: Name of the OsirisCare audit role
    Value: !Ref OsirisCareAuditRole

  ExternalIdUsed:
    Description: External ID configured for this role
    Value: !Ref ExternalId
"""


def validate_role_arn(role_arn: str) -> bool:
    """
    Validate an AWS role ARN format.

    Args:
        role_arn: ARN to validate

    Returns:
        True if valid ARN format
    """
    import re
    pattern = r'^arn:aws:iam::\d{12}:role/[\w+=,.@-]+$'
    return bool(re.match(pattern, role_arn))


def extract_account_from_arn(role_arn: str) -> Optional[str]:
    """
    Extract AWS account ID from role ARN.

    Args:
        role_arn: AWS role ARN

    Returns:
        12-digit account ID or None
    """
    import re
    match = re.match(r'arn:aws:iam::(\d{12}):role/', role_arn)
    return match.group(1) if match else None


def get_required_actions() -> List[str]:
    """
    Get list of all required IAM actions.

    Useful for documentation and verification.

    Returns:
        List of IAM action strings
    """
    actions = []
    for statement in OSIRISCARE_AUDIT_POLICY["Statement"]:
        if statement["Effect"] == "Allow":
            stmt_actions = statement.get("Action", [])
            if isinstance(stmt_actions, str):
                actions.append(stmt_actions)
            else:
                actions.extend(stmt_actions)
    return sorted(set(actions))


def get_denied_actions() -> List[str]:
    """
    Get list of explicitly denied IAM actions.

    Returns:
        List of denied IAM action strings
    """
    actions = []
    for statement in OSIRISCARE_AUDIT_POLICY["Statement"]:
        if statement["Effect"] == "Deny":
            stmt_actions = statement.get("Action", [])
            if isinstance(stmt_actions, str):
                actions.append(stmt_actions)
            else:
                actions.extend(stmt_actions)
    return sorted(set(actions))
