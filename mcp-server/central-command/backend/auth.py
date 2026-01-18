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

logger = logging.getLogger(__name__)

# Configuration
SESSION_DURATION_HOURS = 24
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15

# Try to import bcrypt, fall back to hashlib if not available
try:
    import bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False
    logger.warning("bcrypt not installed, using SHA-256 fallback (less secure)")


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
    """Hash a password for storage."""
    if HAS_BCRYPT:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    else:
        # SHA-256 fallback with salt
        salt = secrets.token_hex(16)
        hash_val = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return f"sha256${salt}${hash_val}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    if HAS_BCRYPT and password_hash.startswith("$2"):
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    elif password_hash.startswith("sha256$"):
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
    """
    import os
    # Use environment variable or fallback to a derived key
    secret = os.getenv("SESSION_TOKEN_SECRET", "osiriscare-session-secret-change-in-production")
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
    # Get user
    result = await db.execute(
        text("""
            SELECT id, username, password_hash, display_name, role, status,
                   failed_login_attempts, locked_until
            FROM admin_users
            WHERE username = :username
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

    # Success - create session
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


async def validate_session(
    db: AsyncSession,
    token: str,
) -> Optional[Dict[str, Any]]:
    """Validate a session token and return user data if valid."""
    token_hash = hash_token(token)

    result = await db.execute(
        text("""
            SELECT u.id, u.username, u.display_name, u.role, s.expires_at
            FROM admin_sessions s
            JOIN admin_users u ON u.id = s.user_id
            WHERE s.token_hash = :token_hash
              AND s.expires_at > :now
              AND u.status = 'active'
        """),
        {"token_hash": token_hash, "now": datetime.now(timezone.utc)}
    )
    row = result.fetchone()

    if not row:
        return None

    user_id, username, display_name, role, _ = row

    return {
        "id": str(user_id),
        "username": username,
        "displayName": display_name,
        "role": role,
    }


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

    return [
        {
            "id": row[0],
            "user": row[1],
            "action": row[2],
            "target": row[3],
            "details": row[4],
            "ip": row[5],
            "timestamp": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]


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

    Raises:
        HTTPException: 401 if no token or invalid token
    """
    auth_header = request.headers.get("authorization", "")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[7:]

    # Get database session - try multiple import paths for flexibility
    try:
        from main import async_session
    except ImportError:
        # Fallback: try importing from the server module's globals
        import sys
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
            async_session = sys.modules['server'].async_session
        else:
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
