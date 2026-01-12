"""
AWS Integration Module.

Provides secure connection to AWS accounts using STS AssumeRole
with ExternalId for confused deputy protection.

Security:
- Custom IAM policy with explicit Deny on secrets
- No use of AWS SecurityAudit managed policy
- ExternalId required for all role assumptions
- Session caching with 50-minute expiry
"""

from .connector import AWSConnector
from .policy_templates import (
    OSIRISCARE_AUDIT_POLICY,
    get_trust_policy,
    get_cloudformation_template,
)

__all__ = [
    "AWSConnector",
    "OSIRISCARE_AUDIT_POLICY",
    "get_trust_policy",
    "get_cloudformation_template",
]
