"""
Multi-tenant data isolation layer.

SECURITY REQUIREMENT: Every data access MUST verify ownership chain:
- User belongs to Partner (if partner user)
- Partner owns Site (or user is admin)
- Site owns Integration
- Integration owns Resources

Returns 404 (not 403) to prevent enumeration attacks.

Usage:
    @require_site_access
    async def get_site_integrations(site_id: str, user = Depends(get_current_user)):
        # User is verified to have access to site_id
        ...

    @require_integration_access
    async def sync_integration(
        site_id: str,
        integration_id: str,
        user = Depends(get_current_user)
    ):
        # User is verified to have access to integration_id via site_id
        ...
"""

import logging
from functools import wraps
from typing import Callable, Optional, List, Set, Any

from fastapi import HTTPException, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class TenantIsolationError(Exception):
    """Base exception for tenant isolation errors."""
    pass


class TenantIsolation:
    """
    Static methods for verifying tenant ownership chains.

    All methods return 404 on failure to prevent enumeration.

    Note: The sites table has two ID columns:
    - id: UUID (primary key, used in foreign keys)
    - site_id: VARCHAR (human-readable identifier like "physical-appliance-pilot-1aea78")

    The API uses site_id (human-readable), but integrations table references sites.id (UUID).
    """

    @staticmethod
    async def get_site_uuid(db: AsyncSession, site_id: str) -> Optional[str]:
        """
        Get the UUID of a site from its human-readable site_id.

        Args:
            db: Database session
            site_id: Human-readable site identifier

        Returns:
            UUID string or None if not found
        """
        result = await db.execute(
            text("SELECT id FROM sites WHERE site_id = :site_id"),
            {"site_id": site_id}
        )
        row = result.fetchone()
        return str(row[0]) if row else None

    @staticmethod
    async def get_user_accessible_sites(
        db: AsyncSession,
        user_id: str,
        user_role: str,
        partner_id: Optional[str] = None
    ) -> Set[str]:
        """
        Get all site IDs a user can access.

        Args:
            db: Database session
            user_id: User ID
            user_role: User's role (admin, operator, readonly)
            partner_id: Partner ID if user is a partner user

        Returns:
            Set of accessible site IDs
        """
        # Admins can access all sites
        if user_role == "admin":
            result = await db.execute(text("SELECT id FROM sites"))
            return {row[0] for row in result.fetchall()}

        # Partner users can only access their partner's sites
        if partner_id:
            result = await db.execute(
                text("""
                    SELECT s.id FROM sites s
                    JOIN partners p ON p.id = s.partner_id
                    WHERE p.id = :partner_id
                """),
                {"partner_id": partner_id}
            )
            return {row[0] for row in result.fetchall()}

        # Operators can access sites they're assigned to
        result = await db.execute(
            text("""
                SELECT site_id FROM user_site_access
                WHERE user_id = :user_id
                UNION
                SELECT id FROM sites WHERE created_by = :user_id
            """),
            {"user_id": user_id}
        )
        return {row[0] for row in result.fetchall()}

    @staticmethod
    async def verify_site_access(
        db: AsyncSession,
        user_id: str,
        user_role: str,
        site_id: str,
        partner_id: Optional[str] = None
    ) -> bool:
        """
        Verify user has access to a specific site.

        Args:
            db: Database session
            user_id: User ID
            user_role: User's role
            site_id: Site ID to check
            partner_id: Partner ID if user is a partner user

        Returns:
            True if access is allowed
        """
        # Admins can access everything
        if user_role == "admin":
            # But verify site exists (site_id is the human-readable identifier, not the UUID id)
            result = await db.execute(
                text("SELECT 1 FROM sites WHERE site_id = :site_id"),
                {"site_id": site_id}
            )
            return result.fetchone() is not None

        # Partner users check partner ownership
        if partner_id:
            result = await db.execute(
                text("""
                    SELECT 1 FROM sites s
                    WHERE s.site_id = :site_id AND s.partner_id = :partner_id
                """),
                {"site_id": site_id, "partner_id": partner_id}
            )
            return result.fetchone() is not None

        # Check direct site access or ownership
        result = await db.execute(
            text("""
                SELECT 1 FROM sites
                WHERE site_id = :site_id AND (
                    created_by = :user_id
                    OR site_id IN (
                        SELECT usa.site_id FROM user_site_access usa
                        WHERE usa.user_id = :user_id
                    )
                )
            """),
            {"site_id": site_id, "user_id": user_id}
        )
        return result.fetchone() is not None

    @staticmethod
    async def verify_integration_access(
        db: AsyncSession,
        user_id: str,
        user_role: str,
        integration_id: str,
        site_id: str,
        partner_id: Optional[str] = None
    ) -> bool:
        """
        Verify user has access to a specific integration.

        Args:
            db: Database session
            user_id: User ID
            user_role: User's role
            integration_id: Integration ID to check
            site_id: Site ID the integration should belong to
            partner_id: Partner ID if user is a partner user

        Returns:
            True if access is allowed
        """
        # Get the site UUID from the human-readable site_id
        site_uuid = await TenantIsolation.get_site_uuid(db, site_id)
        if not site_uuid:
            return False

        # Verify integration belongs to the site (using UUID)
        result = await db.execute(
            text("""
                SELECT site_id FROM integrations
                WHERE id = :integration_id
            """),
            {"integration_id": integration_id}
        )
        row = result.fetchone()

        if not row:
            return False

        if str(row[0]) != site_uuid:
            # Integration doesn't belong to claimed site
            logger.warning(
                f"Integration site mismatch: integration={integration_id} "
                f"claimed_site={site_id} (uuid={site_uuid}) actual_site={row[0]}"
            )
            return False

        # Then verify site access
        return await TenantIsolation.verify_site_access(
            db, user_id, user_role, site_id, partner_id
        )

    @staticmethod
    async def verify_resource_access(
        db: AsyncSession,
        user_id: str,
        user_role: str,
        resource_id: str,
        integration_id: str,
        site_id: str,
        partner_id: Optional[str] = None
    ) -> bool:
        """
        Verify user has access to a specific resource.

        Args:
            db: Database session
            user_id: User ID
            user_role: User's role
            resource_id: Resource ID to check
            integration_id: Integration the resource should belong to
            site_id: Site the integration should belong to
            partner_id: Partner ID if user is a partner user

        Returns:
            True if access is allowed
        """
        # Verify resource belongs to integration
        result = await db.execute(
            text("""
                SELECT integration_id FROM integration_resources
                WHERE id = :resource_id
            """),
            {"resource_id": resource_id}
        )
        row = result.fetchone()

        if not row:
            return False

        if row[0] != integration_id:
            logger.warning(
                f"Resource integration mismatch: resource={resource_id} "
                f"claimed_integration={integration_id} actual_integration={row[0]}"
            )
            return False

        # Then verify integration access
        return await TenantIsolation.verify_integration_access(
            db, user_id, user_role, integration_id, site_id, partner_id
        )


def require_site_access(func: Callable) -> Callable:
    """
    Decorator to verify user has access to the site_id parameter.

    Returns 404 on access denied to prevent enumeration.

    Usage:
        @router.get("/sites/{site_id}/integrations")
        @require_site_access
        async def get_integrations(site_id: str, ...):
            ...
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Import here to avoid circular imports
        from .api import get_db, get_current_user

        # Extract parameters
        site_id = kwargs.get("site_id")
        db = kwargs.get("db")
        user = kwargs.get("user") or kwargs.get("current_user")

        if not site_id:
            raise HTTPException(status_code=400, detail="site_id is required")

        if not db:
            # Try to get from dependencies
            raise HTTPException(status_code=500, detail="Database session not available")

        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Verify access
        has_access = await TenantIsolation.verify_site_access(
            db=db,
            user_id=user.get("id") or user.get("user_id"),
            user_role=user.get("role", "readonly"),
            site_id=site_id,
            partner_id=user.get("partner_id")
        )

        if not has_access:
            # Return 404, NOT 403 (prevent enumeration)
            logger.warning(
                f"Site access denied: user={user.get('id')} site={site_id}"
            )
            raise HTTPException(status_code=404, detail="Site not found")

        return await func(*args, **kwargs)

    return wrapper


def require_integration_access(func: Callable) -> Callable:
    """
    Decorator to verify user has access to the integration_id parameter.

    Requires both site_id and integration_id in function parameters.
    Returns 404 on access denied to prevent enumeration.

    Usage:
        @router.post("/sites/{site_id}/integrations/{integration_id}/sync")
        @require_integration_access
        async def sync_integration(site_id: str, integration_id: str, ...):
            ...
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Extract parameters
        site_id = kwargs.get("site_id")
        integration_id = kwargs.get("integration_id")
        db = kwargs.get("db")
        user = kwargs.get("user") or kwargs.get("current_user")

        if not site_id:
            raise HTTPException(status_code=400, detail="site_id is required")

        if not integration_id:
            raise HTTPException(status_code=400, detail="integration_id is required")

        if not db:
            raise HTTPException(status_code=500, detail="Database session not available")

        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Verify access
        has_access = await TenantIsolation.verify_integration_access(
            db=db,
            user_id=user.get("id") or user.get("user_id"),
            user_role=user.get("role", "readonly"),
            integration_id=integration_id,
            site_id=site_id,
            partner_id=user.get("partner_id")
        )

        if not has_access:
            # Return 404, NOT 403 (prevent enumeration)
            logger.warning(
                f"Integration access denied: user={user.get('id')} "
                f"site={site_id} integration={integration_id}"
            )
            raise HTTPException(status_code=404, detail="Integration not found")

        return await func(*args, **kwargs)

    return wrapper


def require_resource_access(func: Callable) -> Callable:
    """
    Decorator to verify user has access to the resource_id parameter.

    Requires site_id, integration_id, and resource_id in function parameters.
    Returns 404 on access denied to prevent enumeration.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        site_id = kwargs.get("site_id")
        integration_id = kwargs.get("integration_id")
        resource_id = kwargs.get("resource_id")
        db = kwargs.get("db")
        user = kwargs.get("user") or kwargs.get("current_user")

        if not all([site_id, integration_id, resource_id]):
            raise HTTPException(
                status_code=400,
                detail="site_id, integration_id, and resource_id are required"
            )

        if not db:
            raise HTTPException(status_code=500, detail="Database session not available")

        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")

        has_access = await TenantIsolation.verify_resource_access(
            db=db,
            user_id=user.get("id") or user.get("user_id"),
            user_role=user.get("role", "readonly"),
            resource_id=resource_id,
            integration_id=integration_id,
            site_id=site_id,
            partner_id=user.get("partner_id")
        )

        if not has_access:
            logger.warning(
                f"Resource access denied: user={user.get('id')} "
                f"site={site_id} integration={integration_id} resource={resource_id}"
            )
            raise HTTPException(status_code=404, detail="Resource not found")

        return await func(*args, **kwargs)

    return wrapper


class TenantContext:
    """
    Context manager for tenant-scoped operations.

    Provides a consistent way to access tenant information
    throughout a request lifecycle.
    """

    def __init__(
        self,
        user_id: str,
        user_role: str,
        site_id: Optional[str] = None,
        partner_id: Optional[str] = None,
        integration_id: Optional[str] = None
    ):
        self.user_id = user_id
        self.user_role = user_role
        self.site_id = site_id
        self.partner_id = partner_id
        self.integration_id = integration_id

    def is_admin(self) -> bool:
        """Check if current user is admin."""
        return self.user_role == "admin"

    def is_partner_user(self) -> bool:
        """Check if current user is a partner user."""
        return self.partner_id is not None

    def can_access_site(self, target_site_id: str, db: AsyncSession) -> bool:
        """
        Check if context user can access a site.

        This is a synchronous check that should be used after
        the async verification has been done.
        """
        if self.is_admin():
            return True
        if self.site_id and self.site_id == target_site_id:
            return True
        return False

    def __repr__(self) -> str:
        return (
            f"TenantContext(user={self.user_id}, role={self.user_role}, "
            f"site={self.site_id}, partner={self.partner_id})"
        )
