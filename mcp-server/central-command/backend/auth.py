"""Authentication module for Central Command dashboard.

Provides secure admin authentication with:
- bcrypt password hashing
- Session token management
- Audit logging
- Account lockout protection
"""

import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple

from fastapi import Request, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from .shared import execute_with_retry
except ImportError:
    from shared import execute_with_retry  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# MFA pending tokens: {token: {"user_id": ..., "username": ..., "expires": datetime}}
_mfa_pending_tokens: Dict[str, Dict[str, Any]] = {}
MFA_PENDING_TTL_MINUTES = 5

# Configuration
SESSION_DURATION_HOURS = 24
SESSION_IDLE_TIMEOUT_MINUTES = 15  # HIPAA §164.312(a)(2)(iii) automatic logoff
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15

# SECURITY: bcrypt is mandatory for password hashing
try:
    import bcrypt
except ImportError:
    raise RuntimeError(
        "bcrypt library is required for secure password hashing. "
        "Install with: pip install bcrypt"
    )


def validate_password_complexity(password: str) -> Tuple[bool, Optional[str]]:
    """Validate password meets complexity requirements.

    Requirements:
    - At least 12 characters (NIST SP 800-63B recommends 8+, we use 12 for security)
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    - Not a commonly breached password

    Returns:
        (is_valid, error_message) - True if valid, False with error message if not
    """
    import re

    # Minimum length
    if len(password) < 12:
        return False, "Password must be at least 12 characters long"

    # Uppercase check
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"

    # Lowercase check
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"

    # Digit check
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"

    # Special character check
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?~`]", password):
        return False, "Password must contain at least one special character (!@#$%^&*etc.)"

    # Check against common breached passwords
    # Small set of the most common passwords - in production, use a larger database
    COMMON_PASSWORDS = {
        "password123", "123456789012", "qwerty123456", "admin123456",
        "letmein123456", "welcome12345", "password1234", "administrator",
        "changeme1234", "p@ssw0rd1234", "Password123!", "Welcome123!",
        "Summer2024!!", "Winter2024!!", "Spring2024!!", "Fall2024!!",
        "Company1234!", "Security123!", "Admin@12345", "User@123456"
    }

    if password.lower() in [p.lower() for p in COMMON_PASSWORDS]:
        return False, "Password is too common and may have been breached"

    # Check for repeating characters (e.g., "aaaa" or "1111")
    if re.search(r"(.)\1{3,}", password):
        return False, "Password cannot contain 4 or more repeating characters"

    # Check for sequential characters (e.g., "1234" or "abcd")
    sequences = ["0123456789", "9876543210", "abcdefghijklmnopqrstuvwxyz", "zyxwvutsrqponmlkjihgfedcba"]
    password_lower = password.lower()
    for seq in sequences:
        for i in range(len(seq) - 3):
            if seq[i:i+4] in password_lower:
                return False, "Password cannot contain 4 or more sequential characters"

    return True, None


def hash_password(password: str) -> str:
    """Hash a password for storage using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash.

    Supports both bcrypt (preferred) and legacy SHA-256 hashes.
    New passwords should always use hash_password() which uses bcrypt.
    """
    if password_hash.startswith("$2"):
        # bcrypt hash
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    elif password_hash.startswith("sha256$"):
        # Legacy SHA-256 hash (read-only support for migration)
        parts = password_hash.split("$")
        if len(parts) != 3:
            return False
        _, salt, stored_hash = parts
        computed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return secrets.compare_digest(computed, stored_hash)
    return False


def generate_session_token() -> str:
    """Generate a secure session token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a session token for storage using HMAC-SHA256.

    Uses a server-side secret for additional security against rainbow tables.
    Requires SESSION_TOKEN_SECRET environment variable to be set.
    """
    import os
    secret = os.getenv("SESSION_TOKEN_SECRET")
    if not secret:
        raise RuntimeError(
            "SESSION_TOKEN_SECRET environment variable must be set for secure token hashing. "
            "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return hashlib.sha256(f"{secret}:{token}".encode()).hexdigest()


async def ensure_default_admin(db: AsyncSession) -> None:
    """Ensure a default admin user exists.

    Creates admin user on first startup. Password from ADMIN_INITIAL_PASSWORD env var.
    Should be called on startup.
    """
    import os

    result = await db.execute(text("SELECT COUNT(*) FROM admin_users"))
    count = result.scalar()

    if count == 0:
        # Get initial password from environment, fail securely if not set
        initial_password = os.getenv("ADMIN_INITIAL_PASSWORD")
        if not initial_password:
            # SECURITY: Generate random password but NEVER log it
            initial_password = secrets.token_urlsafe(16)
            # Write to a secure file instead of logging
            password_file = "/var/lib/msp/admin_initial_password.txt"
            try:
                import stat
                with open(password_file, "w") as f:
                    f.write(initial_password)
                os.chmod(password_file, stat.S_IRUSR)  # Read-only by owner
                logger.warning(f"ADMIN_INITIAL_PASSWORD not set. Random password written to {password_file}")
                logger.warning("Read the file, set ADMIN_INITIAL_PASSWORD env var, then delete the file")
            except (OSError, IOError):
                # If we can't write the file, fail securely - don't log the password
                logger.error("ADMIN_INITIAL_PASSWORD not set and cannot write password file. Set env var and restart.")
                raise RuntimeError("ADMIN_INITIAL_PASSWORD environment variable must be set")
        else:
            logger.info("Creating admin user with password from ADMIN_INITIAL_PASSWORD env var")

        password_hash = hash_password(initial_password)
        await db.execute(
            text("""
                INSERT INTO admin_users (username, email, password_hash, display_name, role)
                VALUES (:username, :email, :password_hash, :display_name, :role)
            """),
            {
                "username": "admin",
                "email": "admin@local",
                "password_hash": password_hash,
                "display_name": "Administrator",
                "role": "admin",
            }
        )
        await db.commit()
        logger.warning("Default admin created - CHANGE PASSWORD AFTER FIRST LOGIN!")


async def authenticate_user(
    db: AsyncSession,
    username: str,
    password: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Authenticate a user and create a session.

    Returns:
        (success, session_token, user_data) or (False, None, error_message)
    """
    # Get user by username or email
    result = await db.execute(
        text("""
            SELECT id, username, password_hash, display_name, role, status,
                   failed_login_attempts, locked_until
            FROM admin_users
            WHERE username = :username OR email = :username
        """),
        {"username": username}
    )
    row = result.fetchone()

    if not row:
        await _log_audit(db, None, username, "LOGIN_FAILED", "auth", {"reason": "invalid_username"}, ip_address)
        return False, None, {"error": "Invalid username or password"}

    user_id, _, password_hash, display_name, role, status, failed_attempts, locked_until = row

    # Check if account is locked
    if locked_until and locked_until > datetime.now(timezone.utc):
        remaining = (locked_until - datetime.now(timezone.utc)).seconds // 60
        await _log_audit(db, user_id, username, "LOGIN_BLOCKED", "auth", {"reason": "account_locked"}, ip_address)
        return False, None, {"error": f"Account locked. Try again in {remaining} minutes."}

    # Check if account is disabled
    if status != "active":
        await _log_audit(db, user_id, username, "LOGIN_BLOCKED", "auth", {"reason": "account_disabled"}, ip_address)
        return False, None, {"error": "Account is disabled"}

    # Verify password
    if not verify_password(password, password_hash):
        # Increment failed attempts
        new_attempts = (failed_attempts or 0) + 1
        locked_until_new = None

        if new_attempts >= MAX_FAILED_ATTEMPTS:
            locked_until_new = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            logger.warning(f"Account {username} locked due to {new_attempts} failed attempts")

        await db.execute(
            text("""
                UPDATE admin_users
                SET failed_login_attempts = :attempts, locked_until = :locked
                WHERE id = :id
            """),
            {"attempts": new_attempts, "locked": locked_until_new, "id": user_id}
        )
        await db.commit()

        await _log_audit(db, user_id, username, "LOGIN_FAILED", "auth", {"reason": "invalid_password", "attempts": new_attempts}, ip_address)
        return False, None, {"error": "Invalid username or password"}

    # Check MFA status (enabled + required)
    mfa_result = await db.execute(
        text("SELECT mfa_enabled, mfa_required FROM admin_users WHERE id = :id"),
        {"id": user_id}
    )
    mfa_row = mfa_result.fetchone()

    mfa_enabled = mfa_row[0] if mfa_row else False
    mfa_required = mfa_row[1] if mfa_row else False

    # MFA required but not enrolled — block login until setup is complete
    if mfa_required and not mfa_enabled:
        await _log_audit(db, user_id, username, "LOGIN_BLOCKED_MFA_REQUIRED", "auth",
                        {"reason": "mfa_required_but_not_enrolled"}, ip_address)
        await db.commit()
        return False, None, {
            "status": "mfa_setup_required",
            "error": "Multi-factor authentication is required. Please set up MFA before logging in.",
        }

    if mfa_enabled:
        # MFA required — issue a short-lived pending token instead of a session
        mfa_token = secrets.token_urlsafe(32)

        # Clean up expired pending tokens
        now = datetime.now(timezone.utc)
        expired_keys = [k for k, v in _mfa_pending_tokens.items() if v["expires"] < now]
        for k in expired_keys:
            _mfa_pending_tokens.pop(k, None)

        _mfa_pending_tokens[mfa_token] = {
            "user_id": str(user_id),
            "username": username,
            "display_name": display_name,
            "role": role,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "expires": now + timedelta(minutes=MFA_PENDING_TTL_MINUTES),
        }

        await _log_audit(db, user_id, username, "LOGIN_MFA_PENDING", "auth", None, ip_address)
        await db.commit()

        return False, None, {
            "status": "mfa_required",
            "mfa_token": mfa_token,
        }

    # Success - create session (no MFA)
    session_token = generate_session_token()
    token_hash = hash_token(session_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_DURATION_HOURS)

    await db.execute(
        text("""
            INSERT INTO admin_sessions (user_id, token_hash, ip_address, user_agent, expires_at)
            VALUES (:user_id, :token_hash, :ip, :ua, :expires)
        """),
        {
            "user_id": user_id,
            "token_hash": token_hash,
            "ip": ip_address,
            "ua": user_agent,
            "expires": expires_at,
        }
    )

    # Reset failed attempts and update last login
    await db.execute(
        text("""
            UPDATE admin_users
            SET failed_login_attempts = 0, locked_until = NULL, last_login = :now
            WHERE id = :id
        """),
        {"now": datetime.now(timezone.utc), "id": user_id}
    )
    await db.commit()

    await _log_audit(db, user_id, username, "LOGIN_SUCCESS", "auth", None, ip_address)

    return True, session_token, {
        "username": username,
        "displayName": display_name,
        "role": role,
    }


async def complete_mfa_login(
    db: AsyncSession,
    mfa_token: str,
    totp_code: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Complete login after TOTP verification.

    Args:
        db: Database session
        mfa_token: The short-lived MFA pending token
        totp_code: 6-digit TOTP code or 8-char backup code

    Returns:
        (success, session_token, user_data) or (False, None, error_dict)
    """
    from .totp import verify_totp, verify_backup_code

    # Clean up expired tokens
    now = datetime.now(timezone.utc)
    expired_keys = [k for k, v in _mfa_pending_tokens.items() if v["expires"] < now]
    for k in expired_keys:
        _mfa_pending_tokens.pop(k, None)

    pending = _mfa_pending_tokens.pop(mfa_token, None)
    if not pending:
        return False, None, {"error": "Invalid or expired MFA token"}

    if pending["expires"] < now:
        return False, None, {"error": "MFA token has expired"}

    user_id = pending["user_id"]
    username = pending["username"]

    # Get MFA secret and backup codes
    result = await db.execute(
        text("SELECT mfa_secret, mfa_backup_codes FROM admin_users WHERE id = :id"),
        {"id": user_id}
    )
    row = result.fetchone()
    if not row or not row[0]:
        return False, None, {"error": "MFA not configured"}

    mfa_secret, backup_codes_json = row

    # Try TOTP first, then backup code
    code_valid = verify_totp(mfa_secret, totp_code)
    if not code_valid and backup_codes_json:
        code_valid, updated_codes = verify_backup_code(totp_code, backup_codes_json)
        if code_valid:
            # Update backup codes (remove used one)
            await db.execute(
                text("UPDATE admin_users SET mfa_backup_codes = :codes WHERE id = :id"),
                {"codes": updated_codes, "id": user_id}
            )

    if not code_valid:
        await _log_audit(db, user_id, username, "MFA_VERIFY_FAILED", "auth", None, ip_address)
        await db.commit()
        return False, None, {"error": "Invalid TOTP code"}

    # Create session
    session_token = generate_session_token()
    token_hash = hash_token(session_token)
    expires_at = now + timedelta(hours=SESSION_DURATION_HOURS)

    await db.execute(
        text("""
            INSERT INTO admin_sessions (user_id, token_hash, ip_address, user_agent, expires_at)
            VALUES (:user_id, :token_hash, :ip, :ua, :expires)
        """),
        {
            "user_id": user_id,
            "token_hash": token_hash,
            "ip": ip_address or pending.get("ip_address"),
            "ua": user_agent or pending.get("user_agent"),
            "expires": expires_at,
        }
    )

    # Reset failed attempts and update last login
    await db.execute(
        text("""
            UPDATE admin_users
            SET failed_login_attempts = 0, locked_until = NULL, last_login = :now
            WHERE id = :id
        """),
        {"now": now, "id": user_id}
    )
    await db.commit()

    await _log_audit(db, user_id, username, "LOGIN_SUCCESS_MFA", "auth", None, ip_address)

    return True, session_token, {
        "username": username,
        "displayName": pending["display_name"],
        "role": pending["role"],
    }


async def validate_session(
    db: AsyncSession,
    token: str,
) -> Optional[Dict[str, Any]]:
    """Validate a session token and return user data if valid.

    Enforces HIPAA §164.312(a)(2)(iii) idle timeout — sessions inactive
    for more than SESSION_IDLE_TIMEOUT_MINUTES are rejected.
    """
    token_hash = hash_token(token)
    now = datetime.now(timezone.utc)
    idle_cutoff = now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)

    result = await execute_with_retry(
        db,
        """
            SELECT u.id, u.username, u.display_name, u.role, s.expires_at, s.last_activity_at
            FROM admin_sessions s
            JOIN admin_users u ON u.id = s.user_id
            WHERE s.token_hash = :token_hash
              AND s.expires_at > :now
              AND u.status = 'active'
        """,
        {"token_hash": token_hash, "now": now}
    )
    row = result.fetchone()

    if not row:
        return None

    user_id, username, display_name, role, _, last_activity = row

    # HIPAA idle timeout: reject sessions inactive beyond threshold
    if last_activity and last_activity < idle_cutoff:
        await execute_with_retry(
            db,
            "DELETE FROM admin_sessions WHERE token_hash = :token_hash",
            {"token_hash": token_hash}
        )
        await db.commit()
        return None

    # Update last_activity_at on every successful validation
    await execute_with_retry(
        db,
        "UPDATE admin_sessions SET last_activity_at = :now WHERE token_hash = :token_hash",
        {"token_hash": token_hash, "now": now}
    )
    await db.commit()

    # Check org-level scoping (no rows = global admin)
    org_result = await execute_with_retry(
        db,
        "SELECT client_org_id FROM admin_org_assignments WHERE admin_user_id = :uid",
        {"uid": str(user_id)}
    )
    org_rows = org_result.fetchall()
    org_scope = [str(r[0]) for r in org_rows] if org_rows else None

    return {
        "id": str(user_id),
        "username": username,
        "displayName": display_name,
        "role": role,
        "org_scope": org_scope,
    }


def apply_org_filter(base_query: str, user: Dict[str, Any], params: dict, site_alias: str = "s") -> tuple:
    """Apply org-level filtering to a SQL query if the user is org-scoped.

    Args:
        base_query: The SQL query string
        user: The authenticated user dict from require_auth
        params: The query parameters dict (will be mutated)
        site_alias: The alias used for the sites table in the query

    Returns:
        Tuple of (modified_query, params) with org filter appended if needed
    """
    if user.get("org_scope") is not None:
        param_name = "_org_scope_ids"
        base_query += f" AND {site_alias}.client_org_id = ANY(:{param_name})"
        params[param_name] = user["org_scope"]
    return base_query, params


async def require_site_access(conn, user: dict, site_id: str) -> dict:
    """Validate admin user can access site_id.

    Returns site row (id, client_org_id, partner_id) if access granted.
    Raises 404 for both nonexistent sites AND out-of-scope sites.
    Never raises 403 — that would leak site existence (IDOR prevention).

    This helper is for admin auth (require_auth) only. Partner-authenticated
    users cannot reach endpoints that use this helper — partner sessions use
    a different cookie (osiris_partner_session) and auth dependency
    (require_partner), which fail require_auth validation.

    Logic:
      - Global admin (org_scope=None): site must exist
      - Org-scoped user (org_scope=[...]): site must exist AND
        sites.client_org_id must be in user's org_scope
    """
    row = await conn.fetchrow(
        "SELECT id, client_org_id, partner_id FROM sites WHERE site_id = $1",
        site_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Site not found")

    org_scope = user.get("org_scope")
    if org_scope is not None:
        if str(row["client_org_id"]) not in org_scope:
            logger.warning(
                "Site access denied: user=%s site=%s org=%s scope=%s",
                user.get("id"), site_id, row["client_org_id"], org_scope,
            )
            raise HTTPException(status_code=404, detail="Site not found")

    return dict(row)


async def logout(db: AsyncSession, token: str, ip_address: Optional[str] = None) -> bool:
    """Invalidate a session token."""
    token_hash = hash_token(token)

    # Get user for audit log
    result = await db.execute(
        text("""
            SELECT u.id, u.username
            FROM admin_sessions s
            JOIN admin_users u ON u.id = s.user_id
            WHERE s.token_hash = :token_hash
        """),
        {"token_hash": token_hash}
    )
    row = result.fetchone()

    if row:
        user_id, username = row
        await db.execute(
            text("DELETE FROM admin_sessions WHERE token_hash = :token_hash"),
            {"token_hash": token_hash}
        )
        await db.commit()
        await _log_audit(db, user_id, username, "LOGOUT", "auth", None, ip_address)
        return True

    return False


async def get_audit_logs(
    db: AsyncSession,
    limit: int = 100,
    user_id: Optional[str] = None,
) -> list:
    """Get admin audit logs."""
    query = """
        SELECT id, username, action, target, details, ip_address, created_at
        FROM admin_audit_log
        WHERE 1=1
    """
    params: Dict[str, Any] = {}

    if user_id:
        query += " AND user_id = :user_id"
        params["user_id"] = user_id

    query += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    import json
    logs = []
    for row in rows:
        # Ensure details is a string, not an object
        details = row[4]
        if details is not None and not isinstance(details, str):
            details = json.dumps(details)
        # Ensure target is a string, not an object
        target = row[3]
        if target is not None and not isinstance(target, str):
            target = json.dumps(target)
        # Ensure user is a string, not an object
        user = row[1]
        if user is not None and not isinstance(user, str):
            user = json.dumps(user)
        logs.append({
            "id": row[0],
            "user": user,
            "action": row[2],
            "target": target,
            "details": details,
            "ip": row[5],
            "timestamp": row[6].isoformat() if row[6] else None,
        })
    return logs


async def _log_audit(
    db: AsyncSession,
    user_id: Optional[str],
    username: str,
    action: str,
    target: str,
    details: Optional[Dict],
    ip_address: Optional[str],
) -> None:
    """Log an audit event."""
    import json
    await db.execute(
        text("""
            INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
            VALUES (:user_id, :username, :action, :target, :details, :ip)
        """),
        {
            "user_id": user_id,
            "username": username,
            "action": action,
            "target": target,
            "details": json.dumps(details) if details else None,
            "ip": ip_address,
        }
    )
    # Don't commit here - let the caller handle transaction


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """Remove expired sessions. Returns count deleted."""
    result = await db.execute(
        text("DELETE FROM admin_sessions WHERE expires_at < :now"),
        {"now": datetime.now(timezone.utc)}
    )
    await db.commit()
    return result.rowcount


# =============================================================================
# AUTHENTICATION DEPENDENCY FOR ROUTE PROTECTION
# =============================================================================

async def require_auth(request: Request) -> Dict[str, Any]:
    """FastAPI dependency that requires valid authentication.

    Use as a dependency on routes that require authentication:

        @router.get("/protected")
        async def protected_route(user: dict = Depends(require_auth)):
            return {"message": f"Hello {user['username']}"}

    Accepts token from:
    1. HTTP-only cookie (preferred, more secure)
    2. Authorization header (backwards compatibility)

    Raises:
        HTTPException: 401 if no token or invalid token
    """
    # Try cookie first (more secure), then Authorization header
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get database session
    try:
        from main import async_session
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Database session not configured",
        )

    async with async_session() as db:
        user = await validate_session(db, token)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def require_admin(user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """Dependency that requires admin role.

    Raises:
        HTTPException: 403 if user is not admin
    """
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )
    return user


async def require_operator(user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """Dependency that requires operator or admin role.

    Operators can execute actions but cannot manage users/partners.

    Raises:
        HTTPException: 403 if user is readonly
    """
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        )
    return user


async def require_companion(user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """Dependency that requires companion or admin role.

    Companion users can access all HIPAA compliance modules across orgs.
    Admins inherit companion access for oversight.

    Raises:
        HTTPException: 403 if user is not companion or admin
    """
    if user.get("role") not in ("admin", "companion"):
        raise HTTPException(
            status_code=403,
            detail="Companion access required",
        )
    return user


def require_role(*allowed_roles: str):
    """Factory for creating role-based dependencies.

    Usage:
        @router.get("/endpoint")
        async def protected_route(user: dict = Depends(require_role("admin", "operator"))):
            ...

    Args:
        *allowed_roles: Role names that are allowed to access the route

    Returns:
        FastAPI dependency that validates user role
    """
    async def role_dependency(user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
        if user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access requires one of: {', '.join(allowed_roles)}",
            )
        return user
    return role_dependency


async def check_site_access_sa(db: "AsyncSession", user: Dict[str, Any], site_id: str):
    """Validate admin user can access site_id (SQLAlchemy version).

    Returns 404 for both nonexistent and out-of-scope sites (IDOR prevention).
    Global admins (org_scope=None) can access any site.
    Org-scoped users can only access sites in their org.
    """
    result = await db.execute(
        text("SELECT client_org_id FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Site not found")

    org_scope = user.get("org_scope")
    if org_scope is not None:
        if str(row[0]) not in org_scope:
            raise HTTPException(status_code=404, detail="Site not found")


async def check_site_access_pool(user: Dict[str, Any], site_id: str):
    """Validate admin user can access site_id (asyncpg pool version).

    Same IDOR prevention as check_site_access_sa but uses the asyncpg pool
    directly instead of SQLAlchemy. For use in routes that use admin_connection().
    """
    org_scope = user.get("org_scope")
    if org_scope is None:
        return  # Global admin — no restriction

    from .fleet import get_pool
    from .tenant_middleware import admin_connection
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT client_org_id FROM sites WHERE site_id = $1", site_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Site not found")
    if str(row["client_org_id"]) not in org_scope:
        raise HTTPException(status_code=404, detail="Site not found")


def _check_org_access(user: Dict[str, Any], org_id: str):
    """Validate admin user can access org_id.

    Returns None if access granted.
    Raises 404 for out-of-scope orgs (IDOR prevention — never 403).
    Global admins (org_scope=None) can access any org.
    """
    org_scope = user.get("org_scope")
    if org_scope is None:
        return  # Global admin
    if str(org_id) not in org_scope:
        raise HTTPException(status_code=404, detail="Organization not found")
