"""Organization-level shared credential management.

Org credentials are inherited by all sites in the org.
Site-level credentials take precedence over org credentials.
"""

import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .auth import require_auth, require_operator, _check_org_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/organizations", tags=["org-credentials"])


class OrgCredentialCreate(BaseModel):
    credential_type: str
    credential_name: str
    host: str = ""
    username: str = ""
    password: str = ""
    domain: Optional[str] = None
    port: Optional[int] = None


@router.get("/{org_id}/credentials")
async def list_org_credentials(org_id: str, user: dict = Depends(require_auth)):
    """List credentials for an organization (passwords redacted)."""
    _check_org_access(user, org_id)
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Verify org exists
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1", org_id
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        rows = await conn.fetch("""
            SELECT id, credential_name, credential_type, encrypted_data, created_at
            FROM org_credentials
            WHERE client_org_id = $1
            ORDER BY created_at DESC
        """, org_id)

        credentials = []
        for row in rows:
            try:
                data = json.loads(row['encrypted_data']) if row['encrypted_data'] else {}
                credentials.append({
                    'id': str(row['id']),
                    'credential_type': row['credential_type'],
                    'credential_name': row['credential_name'],
                    'host': data.get('host', ''),
                    'username': data.get('username', ''),
                    'domain': data.get('domain', ''),
                    'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                })
            except (json.JSONDecodeError, TypeError):
                pass

        return {"credentials": credentials, "count": len(credentials)}


@router.post("/{org_id}/credentials")
async def create_org_credential(
    org_id: str,
    cred: OrgCredentialCreate,
    user: dict = Depends(require_operator),
):
    """Add a credential to an organization."""
    _check_org_access(user, org_id)
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1", org_id
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        encrypted_data = json.dumps({
            'host': cred.host,
            'username': cred.username,
            'password': cred.password,
            'domain': cred.domain,
            'port': cred.port,
        })

        row = await conn.fetchrow("""
            INSERT INTO org_credentials (client_org_id, credential_name, credential_type, encrypted_data)
            VALUES ($1, $2, $3, $4)
            RETURNING id
        """, org_id, cred.credential_name, cred.credential_type, encrypted_data)

        logger.info(f"Created org credential {row['id']} for org {org_id}")
        return {"status": "created", "id": str(row['id'])}


@router.delete("/{org_id}/credentials/{credential_id}")
async def delete_org_credential(
    org_id: str,
    credential_id: str,
    user: dict = Depends(require_operator),
):
    """Delete an organization credential."""
    _check_org_access(user, org_id)
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        result = await conn.execute("""
            DELETE FROM org_credentials
            WHERE id = $1 AND client_org_id = $2
        """, credential_id, org_id)

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Credential not found")

        logger.info(f"Deleted org credential {credential_id} from org {org_id}")
        return {"status": "deleted"}
