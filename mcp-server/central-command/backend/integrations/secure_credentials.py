"""
Secure credentials wrapper that prevents exposure in logs.

SECURITY REQUIREMENT: NEVER expose credentials in logs, traces, or error messages.

This wrapper:
- Overrides __repr__ and __str__ to return [REDACTED]
- Provides safe access methods for credential retrieval
- Supports JSON serialization for encrypted storage
- Implements comparison without exposing values

Usage:
    creds = SecureCredentials(
        access_token="sk-xxx",
        refresh_token="rt-xxx",
        expires_at="2024-01-01T00:00:00Z"
    )

    print(creds)  # Output: SecureCredentials([REDACTED])
    print(repr(creds))  # Output: SecureCredentials(keys=['access_token', 'refresh_token', 'expires_at'])

    # Safe access
    token = creds.get("access_token")
    token = creds["access_token"]

    # Iteration yields keys only, not values
    for key in creds:
        print(key)  # 'access_token', etc.
"""

import json
import hashlib
from typing import Any, Dict, Iterator, Optional
from datetime import datetime


class SecureCredentials:
    """
    Wrapper for sensitive credentials that prevents log exposure.

    All string representations are redacted. Access to actual values
    requires explicit method calls.
    """

    __slots__ = ("_data", "_created_at")

    def __init__(self, **kwargs: Any):
        """
        Initialize with credential key-value pairs.

        Args:
            **kwargs: Credential fields (e.g., access_token, refresh_token)
        """
        self._data: Dict[str, Any] = dict(kwargs)
        self._created_at = datetime.utcnow()

    def __repr__(self) -> str:
        """Return safe representation showing only keys."""
        return f"SecureCredentials(keys={list(self._data.keys())})"

    def __str__(self) -> str:
        """Return redacted string representation."""
        return "SecureCredentials([REDACTED])"

    def __contains__(self, key: str) -> bool:
        """Check if key exists."""
        return key in self._data

    def __getitem__(self, key: str) -> Any:
        """Get credential value by key."""
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate over keys only (not values)."""
        return iter(self._data.keys())

    def __len__(self) -> int:
        """Return number of credential fields."""
        return len(self._data)

    def __eq__(self, other: object) -> bool:
        """Compare credentials securely (constant-time for strings)."""
        if not isinstance(other, SecureCredentials):
            return False
        if set(self._data.keys()) != set(other._data.keys()):
            return False
        # Use hash comparison to avoid timing attacks
        self_hash = hashlib.sha256(self.to_json().encode()).hexdigest()
        other_hash = hashlib.sha256(other.to_json().encode()).hexdigest()
        return self_hash == other_hash

    def __hash__(self) -> int:
        """Hash based on credential content."""
        return hash(hashlib.sha256(self.to_json().encode()).hexdigest())

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get credential value by key with optional default.

        Args:
            key: Credential field name
            default: Value to return if key not found

        Returns:
            Credential value or default
        """
        return self._data.get(key, default)

    def keys(self) -> Iterator[str]:
        """Return credential field names."""
        return iter(self._data.keys())

    def to_json(self) -> str:
        """
        Serialize credentials to JSON string.

        WARNING: This exposes actual values. Use only for encrypted storage.

        Returns:
            JSON string of credentials
        """
        return json.dumps(self._data, sort_keys=True, default=str)

    def to_dict(self) -> Dict[str, Any]:
        """
        Return credentials as dictionary.

        WARNING: This exposes actual values. Use only for API calls.

        Returns:
            Dictionary of credentials
        """
        return dict(self._data)

    @classmethod
    def from_json(cls, json_str: str) -> "SecureCredentials":
        """
        Create SecureCredentials from JSON string.

        Args:
            json_str: JSON string of credentials

        Returns:
            SecureCredentials instance
        """
        data = json.loads(json_str)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SecureCredentials":
        """
        Create SecureCredentials from dictionary.

        Args:
            data: Dictionary of credentials

        Returns:
            SecureCredentials instance
        """
        return cls(**data)

    def with_updated(self, **kwargs: Any) -> "SecureCredentials":
        """
        Create new SecureCredentials with updated values.

        Args:
            **kwargs: Fields to update

        Returns:
            New SecureCredentials with updated values
        """
        new_data = dict(self._data)
        new_data.update(kwargs)
        return SecureCredentials(**new_data)

    def redacted_dict(self) -> Dict[str, str]:
        """
        Return dictionary with all values redacted.

        Useful for logging credential operations without exposing values.

        Returns:
            Dictionary with keys and "[REDACTED]" values
        """
        return {k: "[REDACTED]" for k in self._data.keys()}

    def has_expired(self, field: str = "expires_at") -> Optional[bool]:
        """
        Check if credentials have expired based on expiry field.

        Args:
            field: Name of the expiry timestamp field

        Returns:
            True if expired, False if not, None if no expiry field
        """
        expires_at = self._data.get(field)
        if not expires_at:
            return None

        if isinstance(expires_at, str):
            # Parse ISO format
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                return None

        if isinstance(expires_at, datetime):
            return datetime.utcnow() > expires_at.replace(tzinfo=None)

        return None

    @property
    def created_at(self) -> datetime:
        """Return when these credentials were created."""
        return self._created_at


class OAuthTokens(SecureCredentials):
    """
    Specialized SecureCredentials for OAuth tokens.

    Provides typed access to common OAuth fields.
    """

    @property
    def access_token(self) -> Optional[str]:
        """Return access token."""
        return self.get("access_token")

    @property
    def refresh_token(self) -> Optional[str]:
        """Return refresh token."""
        return self.get("refresh_token")

    @property
    def token_type(self) -> str:
        """Return token type (default: Bearer)."""
        return self.get("token_type", "Bearer")

    @property
    def expires_at(self) -> Optional[str]:
        """Return expiry timestamp."""
        return self.get("expires_at")

    @property
    def scope(self) -> Optional[str]:
        """Return granted scopes."""
        return self.get("scope")

    def is_expired(self) -> bool:
        """Check if access token has expired."""
        return self.has_expired("expires_at") or False

    def expires_soon(self, minutes: int = 10) -> bool:
        """Check if token expires within given minutes."""
        expires_at = self.get("expires_at")
        if not expires_at:
            return False

        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                return False

        if isinstance(expires_at, datetime):
            from datetime import timedelta
            threshold = datetime.utcnow() + timedelta(minutes=minutes)
            return expires_at.replace(tzinfo=None) < threshold

        return False


class AWSCredentials(SecureCredentials):
    """
    Specialized SecureCredentials for AWS STS credentials.

    Provides typed access to STS session credentials.
    """

    @property
    def access_key_id(self) -> Optional[str]:
        """Return AWS access key ID."""
        return self.get("access_key_id")

    @property
    def secret_access_key(self) -> Optional[str]:
        """Return AWS secret access key."""
        return self.get("secret_access_key")

    @property
    def session_token(self) -> Optional[str]:
        """Return AWS session token."""
        return self.get("session_token")

    @property
    def expiration(self) -> Optional[str]:
        """Return session expiration."""
        return self.get("expiration")

    def is_expired(self) -> bool:
        """Check if session has expired."""
        return self.has_expired("expiration") or False

    def to_boto3_credentials(self) -> Dict[str, str]:
        """
        Return credentials dict for boto3.

        Returns:
            Dict with aws_access_key_id, aws_secret_access_key, aws_session_token
        """
        return {
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            "aws_session_token": self.session_token,
        }
