"""User management API for Central Command.

Provides endpoints for:
- Listing users
- Inviting new users
- Managing user roles and status
- Password management
"""

import secrets
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from .auth import (
    require_auth,
    require_admin,
    hash_password,
    verify_password,
    hash_token,
    _log_audit,
)
from .email_service import send_invite_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])

# Invite token expiry
INVITE_EXPIRY_DAYS = 7


# =============================================================================
# MODELS
# =============================================================================

class UserResponse(BaseModel):
    id: str
    username: str
    email: Optional[str]
    display_name: Optional[str]
    role: str
    status: str
    last_login: Optional[str]
    created_at: str


class UserInviteRequest(BaseModel):
    email: EmailStr
    role: Literal["admin", "operator", "readonly"]
    display_name: Optional[str] = None


class UserInviteResponse(BaseModel):
    id: str
    email: str
    role: str
    display_name: Optional[str]
    status: str
    invited_by: Optional[str]
    invited_by_name: Optional[str]
    expires_at: str
    created_at: str


class InviteValidateResponse(BaseModel):
    valid: bool
    email: Optional[str] = None
    role: Optional[str] = None
    display_name: Optional[str] = None
    error: Optional[str] = None


class InviteAcceptRequest(BaseModel):
    token: str
    password: str
    confirm_password: str


class UserUpdateRequest(BaseModel):
    role: Optional[Literal["admin", "operator", "readonly"]] = None
    status: Optional[Literal["active", "disabled"]] = None
    display_name: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class AdminPasswordResetRequest(BaseModel):
    new_password: str


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def generate_invite_token() -> str:
    """Generate a secure invite token."""
    return secrets.token_urlsafe(32)


async def get_db():
    """Get database session."""
    # Try multiple import paths for flexibility
    try:
        from main import async_session
    except ImportError:
        import sys
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
            async_session = sys.modules['server'].async_session
        else:
            raise RuntimeError("Database session not configured")

    async with async_session() as db:
        yield db


# =============================================================================
# USER ENDPOINTS (Admin only)
# =============================================================================

@router.get("", response_model=list[UserResponse])
async def list_users(
    user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """List all admin users."""
    result = await db.execute(text("""
        SELECT id, username, email, display_name, role, status, last_login, created_at
        FROM admin_users
        ORDER BY created_at DESC
    """))

    users = []
    for row in result.fetchall():
        users.append(UserResponse(
            id=str(row[0]),
            username=row[1],
            email=row[2],
            display_name=row[3],
            role=row[4],
            status=row[5],
            last_login=row[6].isoformat() if row[6] else None,
            created_at=row[7].isoformat() if row[7] else None,
        ))

    return users


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    update: UserUpdateRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Update a user's role, status, or display name."""
    # Check user exists
    result = await db.execute(
        text("SELECT id, username FROM admin_users WHERE id = :id"),
        {"id": user_id}
    )
    target = result.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-demotion from admin
    if user_id == current_user["id"] and update.role and update.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot demote yourself from admin")

    # Prevent disabling self
    if user_id == current_user["id"] and update.status == "disabled":
        raise HTTPException(status_code=400, detail="Cannot disable your own account")

    # Build update query
    updates = []
    params = {"id": user_id}

    if update.role:
        updates.append("role = :role")
        params["role"] = update.role
    if update.status:
        updates.append("status = :status")
        params["status"] = update.status
    if update.display_name is not None:
        updates.append("display_name = :display_name")
        params["display_name"] = update.display_name

    if updates:
        updates.append("updated_at = :now")
        params["now"] = datetime.now(timezone.utc)

        await db.execute(
            text(f"UPDATE admin_users SET {', '.join(updates)} WHERE id = :id"),
            params
        )

        # Audit log
        await _log_audit(
            db, current_user["id"], current_user["username"],
            "USER_UPDATED", f"user:{user_id}",
            {"changes": update.model_dump(exclude_none=True)},
            request.client.host if request.client else None
        )
        await db.commit()

    # Return updated user
    result = await db.execute(text("""
        SELECT id, username, email, display_name, role, status, last_login, created_at
        FROM admin_users WHERE id = :id
    """), {"id": user_id})
    row = result.fetchone()

    return UserResponse(
        id=str(row[0]),
        username=row[1],
        email=row[2],
        display_name=row[3],
        role=row[4],
        status=row[5],
        last_login=row[6].isoformat() if row[6] else None,
        created_at=row[7].isoformat() if row[7] else None,
    )


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Delete a user."""
    # Prevent self-deletion
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    # Check user exists
    result = await db.execute(
        text("SELECT username FROM admin_users WHERE id = :id"),
        {"id": user_id}
    )
    target = result.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Delete sessions first
    await db.execute(
        text("DELETE FROM admin_sessions WHERE user_id = :id"),
        {"id": user_id}
    )

    # Delete OAuth identities if any
    await db.execute(
        text("DELETE FROM admin_oauth_identities WHERE user_id = :id"),
        {"id": user_id}
    )

    # Set audit log user_id to NULL to preserve audit trail
    await db.execute(
        text("UPDATE admin_audit_log SET user_id = NULL WHERE user_id = :id"),
        {"id": user_id}
    )

    # Delete user
    await db.execute(
        text("DELETE FROM admin_users WHERE id = :id"),
        {"id": user_id}
    )

    # Audit log
    await _log_audit(
        db, current_user["id"], current_user["username"],
        "USER_DELETED", f"user:{user_id}",
        {"deleted_username": target[0]},
        request.client.host if request.client else None
    )
    await db.commit()

    return {"status": "deleted", "user_id": user_id}


@router.put("/{user_id}/password")
async def admin_reset_password(
    user_id: str,
    reset: AdminPasswordResetRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Admin: Reset a user's password."""
    # Check user exists
    result = await db.execute(
        text("SELECT username FROM admin_users WHERE id = :id"),
        {"id": user_id}
    )
    target = result.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY: Validate password complexity
    from .auth import validate_password_complexity
    is_valid, error_msg = validate_password_complexity(reset.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # Update password
    password_hash = hash_password(reset.new_password)
    await db.execute(
        text("UPDATE admin_users SET password_hash = :hash, updated_at = :now WHERE id = :id"),
        {"hash": password_hash, "now": datetime.now(timezone.utc), "id": user_id}
    )

    # Invalidate all sessions for this user
    await db.execute(
        text("DELETE FROM admin_sessions WHERE user_id = :id"),
        {"id": user_id}
    )

    # Audit log
    await _log_audit(
        db, current_user["id"], current_user["username"],
        "PASSWORD_RESET_BY_ADMIN", f"user:{user_id}",
        {"target_username": target[0]},
        request.client.host if request.client else None
    )
    await db.commit()

    return {"status": "password_reset", "user_id": user_id}


# =============================================================================
# INVITE ENDPOINTS
# =============================================================================

@router.get("/invites", response_model=list[UserInviteResponse])
async def list_invites(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """List pending invites."""
    result = await db.execute(text("""
        SELECT i.id, i.email, i.role, i.display_name, i.status,
               i.invited_by, u.display_name as inviter_name,
               i.expires_at, i.created_at
        FROM admin_user_invites i
        LEFT JOIN admin_users u ON u.id = i.invited_by
        WHERE i.status = 'pending'
        ORDER BY i.created_at DESC
    """))

    invites = []
    for row in result.fetchall():
        invites.append(UserInviteResponse(
            id=str(row[0]),
            email=row[1],
            role=row[2],
            display_name=row[3],
            status=row[4],
            invited_by=str(row[5]) if row[5] else None,
            invited_by_name=row[6],
            expires_at=row[7].isoformat() if row[7] else None,
            created_at=row[8].isoformat() if row[8] else None,
        ))

    return invites


@router.post("/invite", response_model=UserInviteResponse)
async def invite_user(
    invite: UserInviteRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Invite a new user via email."""
    # Check if email already has an active user
    result = await db.execute(
        text("SELECT id FROM admin_users WHERE email = :email"),
        {"email": invite.email}
    )
    if result.fetchone():
        raise HTTPException(status_code=400, detail="User with this email already exists")

    # Check if there's already a pending invite
    result = await db.execute(
        text("SELECT id FROM admin_user_invites WHERE email = :email AND status = 'pending'"),
        {"email": invite.email}
    )
    if result.fetchone():
        raise HTTPException(status_code=400, detail="Pending invite already exists for this email")

    # Generate token
    token = generate_invite_token()
    token_hash = hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)

    # Create invite record
    result = await db.execute(
        text("""
            INSERT INTO admin_user_invites (email, role, display_name, token_hash, invited_by, expires_at)
            VALUES (:email, :role, :display_name, :token_hash, :invited_by, :expires_at)
            RETURNING id, created_at
        """),
        {
            "email": invite.email,
            "role": invite.role,
            "display_name": invite.display_name,
            "token_hash": token_hash,
            "invited_by": current_user["id"],
            "expires_at": expires_at,
        }
    )
    row = result.fetchone()
    invite_id, created_at = str(row[0]), row[1]

    # Audit log
    await _log_audit(
        db, current_user["id"], current_user["username"],
        "USER_INVITED", f"invite:{invite_id}",
        {"email": invite.email, "role": invite.role},
        request.client.host if request.client else None
    )
    await db.commit()

    # Send invite email (async, don't block on failure)
    email_sent = send_invite_email(
        to_email=invite.email,
        invite_token=token,
        inviter_name=current_user.get("displayName", current_user["username"]),
        role=invite.role,
        display_name=invite.display_name
    )

    if not email_sent:
        logger.warning(f"Failed to send invite email to {invite.email}")

    return UserInviteResponse(
        id=invite_id,
        email=invite.email,
        role=invite.role,
        display_name=invite.display_name,
        status="pending",
        invited_by=current_user["id"],
        invited_by_name=current_user.get("displayName"),
        expires_at=expires_at.isoformat(),
        created_at=created_at.isoformat(),
    )


@router.post("/invite/{invite_id}/resend")
async def resend_invite(
    invite_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Resend an invite email with a new token."""
    # Get invite
    result = await db.execute(
        text("SELECT email, role, display_name, status FROM admin_user_invites WHERE id = :id"),
        {"id": invite_id}
    )
    invite = result.fetchone()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    email, role, display_name, status = invite
    if status != "pending":
        raise HTTPException(status_code=400, detail=f"Invite is {status}, cannot resend")

    # Generate new token
    token = generate_invite_token()
    token_hash = hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)

    # Update invite
    await db.execute(
        text("""
            UPDATE admin_user_invites
            SET token_hash = :token_hash, expires_at = :expires_at, invited_at = :now
            WHERE id = :id
        """),
        {
            "token_hash": token_hash,
            "expires_at": expires_at,
            "now": datetime.now(timezone.utc),
            "id": invite_id,
        }
    )

    # Audit log
    await _log_audit(
        db, current_user["id"], current_user["username"],
        "INVITE_RESENT", f"invite:{invite_id}",
        {"email": email},
        request.client.host if request.client else None
    )
    await db.commit()

    # Send email
    send_invite_email(
        to_email=email,
        invite_token=token,
        inviter_name=current_user.get("displayName", current_user["username"]),
        role=role,
        display_name=display_name
    )

    return {"status": "resent", "invite_id": invite_id}


@router.delete("/invite/{invite_id}")
async def revoke_invite(
    invite_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Revoke a pending invite."""
    result = await db.execute(
        text("SELECT email, status FROM admin_user_invites WHERE id = :id"),
        {"id": invite_id}
    )
    invite = result.fetchone()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    email, status = invite
    if status != "pending":
        raise HTTPException(status_code=400, detail=f"Invite is already {status}")

    # Update status to revoked
    await db.execute(
        text("UPDATE admin_user_invites SET status = 'revoked' WHERE id = :id"),
        {"id": invite_id}
    )

    # Audit log
    await _log_audit(
        db, current_user["id"], current_user["username"],
        "INVITE_REVOKED", f"invite:{invite_id}",
        {"email": email},
        request.client.host if request.client else None
    )
    await db.commit()

    return {"status": "revoked", "invite_id": invite_id}


# =============================================================================
# PUBLIC ENDPOINTS (No auth required)
# =============================================================================

@router.get("/invite/validate/{token}", response_model=InviteValidateResponse)
async def validate_invite(token: str, db=Depends(get_db)):
    """Validate an invite token (public endpoint)."""
    token_hash = hash_token(token)

    result = await db.execute(
        text("""
            SELECT email, role, display_name, status, expires_at
            FROM admin_user_invites
            WHERE token_hash = :token_hash
        """),
        {"token_hash": token_hash}
    )
    invite = result.fetchone()

    if not invite:
        return InviteValidateResponse(valid=False, error="Invalid invite token")

    email, role, display_name, status, expires_at = invite

    if status != "pending":
        return InviteValidateResponse(valid=False, error=f"Invite has been {status}")

    if expires_at < datetime.now(timezone.utc):
        return InviteValidateResponse(valid=False, error="Invite has expired")

    return InviteValidateResponse(
        valid=True,
        email=email,
        role=role,
        display_name=display_name
    )


@router.post("/invite/accept")
async def accept_invite(accept: InviteAcceptRequest, request: Request, db=Depends(get_db)):
    """Accept an invite and create user account (public endpoint)."""
    if accept.password != accept.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    # SECURITY: Validate password complexity
    from .auth import validate_password_complexity
    is_valid, error_msg = validate_password_complexity(accept.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    token_hash = hash_token(accept.token)

    # Get and validate invite
    result = await db.execute(
        text("""
            SELECT id, email, role, display_name, status, expires_at
            FROM admin_user_invites
            WHERE token_hash = :token_hash
        """),
        {"token_hash": token_hash}
    )
    invite = result.fetchone()

    if not invite:
        raise HTTPException(status_code=400, detail="Invalid invite token")

    invite_id, email, role, display_name, status, expires_at = invite

    if status != "pending":
        raise HTTPException(status_code=400, detail=f"Invite has been {status}")

    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invite has expired")

    # Create username from email
    username = email.split("@")[0].lower()

    # Check if username exists, append number if so
    base_username = username
    counter = 1
    while True:
        result = await db.execute(
            text("SELECT id FROM admin_users WHERE username = :username"),
            {"username": username}
        )
        if not result.fetchone():
            break
        username = f"{base_username}{counter}"
        counter += 1

    # Create user
    password_hash = hash_password(accept.password)
    result = await db.execute(
        text("""
            INSERT INTO admin_users (username, email, password_hash, display_name, role)
            VALUES (:username, :email, :password_hash, :display_name, :role)
            RETURNING id
        """),
        {
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "display_name": display_name or username,
            "role": role,
        }
    )
    user_id = str(result.fetchone()[0])

    # Update invite status
    await db.execute(
        text("""
            UPDATE admin_user_invites
            SET status = 'accepted', accepted_at = :now, accepted_user_id = :user_id
            WHERE id = :invite_id
        """),
        {
            "now": datetime.now(timezone.utc),
            "user_id": user_id,
            "invite_id": invite_id,
        }
    )

    # Audit log
    await _log_audit(
        db, user_id, username,
        "INVITE_ACCEPTED", f"invite:{invite_id}",
        {"email": email, "role": role},
        request.client.host if request.client else None
    )
    await db.commit()

    return {
        "status": "accepted",
        "username": username,
        "message": "Account created. You can now log in."
    }


# =============================================================================
# SELF-SERVICE ENDPOINTS (Any authenticated user)
# =============================================================================

@router.get("/me", response_model=UserResponse)
async def get_my_profile(current_user: dict = Depends(require_auth), db=Depends(get_db)):
    """Get current user's profile."""
    result = await db.execute(
        text("""
            SELECT id, username, email, display_name, role, status, last_login, created_at
            FROM admin_users WHERE id = :id
        """),
        {"id": current_user["id"]}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=str(row[0]),
        username=row[1],
        email=row[2],
        display_name=row[3],
        role=row[4],
        status=row[5],
        last_login=row[6].isoformat() if row[6] else None,
        created_at=row[7].isoformat() if row[7] else None,
    )


@router.put("/me/password")
async def change_my_password(
    change: PasswordChangeRequest,
    request: Request,
    current_user: dict = Depends(require_auth),
    db=Depends(get_db)
):
    """Change current user's password."""
    if change.new_password != change.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    # SECURITY: Validate password complexity
    from .auth import validate_password_complexity
    is_valid, error_msg = validate_password_complexity(change.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # Verify current password
    result = await db.execute(
        text("SELECT password_hash FROM admin_users WHERE id = :id"),
        {"id": current_user["id"]}
    )
    row = result.fetchone()
    if not row or not verify_password(change.current_password, row[0]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Update password
    password_hash = hash_password(change.new_password)
    await db.execute(
        text("UPDATE admin_users SET password_hash = :hash, updated_at = :now WHERE id = :id"),
        {"hash": password_hash, "now": datetime.now(timezone.utc), "id": current_user["id"]}
    )

    # Audit log
    await _log_audit(
        db, current_user["id"], current_user["username"],
        "PASSWORD_CHANGED", f"user:{current_user['id']}",
        None,
        request.client.host if request.client else None
    )
    await db.commit()

    return {"status": "password_changed"}
