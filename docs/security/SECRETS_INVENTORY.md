# OsirisCare Secrets Inventory

**Last Updated:** 2026-01-08
**Classification:** CONFIDENTIAL - DO NOT COMMIT TO PUBLIC REPOS

---

## Overview

This document catalogs all secrets, credentials, and sensitive configuration used across the OsirisCare platform. All secrets should be managed via 1Password and injected at runtime.

---

## Secret Categories

### 1. Database Credentials

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| PostgreSQL User | `POSTGRES_USER` | .env file | `OsirisCare/Production/PostgreSQL` |
| PostgreSQL Password | `POSTGRES_PASSWORD` | .env file | `OsirisCare/Production/PostgreSQL` |
| PostgreSQL Database | `POSTGRES_DB` | .env file | `OsirisCare/Production/PostgreSQL` |

**Files that use this:**
- `mcp-server/docker-compose.yml`
- `mcp-server/central-command/backend/fleet.py`
- `mcp-server/server.py`

### 2. Redis Credentials

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| Redis Password | `REDIS_PASSWORD` | .env file | `OsirisCare/Production/Redis` |

**Files that use this:**
- `mcp-server/docker-compose.yml`
- `mcp-server/server.py`

### 3. MinIO (Object Storage) Credentials

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| MinIO Root User | `MINIO_ROOT_USER` | .env file | `OsirisCare/Production/MinIO` |
| MinIO Root Password | `MINIO_ROOT_PASSWORD` | .env file | `OsirisCare/Production/MinIO` |
| MinIO Access Key | `MINIO_ACCESS_KEY` | .env file | `OsirisCare/Production/MinIO` |
| MinIO Secret Key | `MINIO_SECRET_KEY` | .env file | `OsirisCare/Production/MinIO` |

**Files that use this:**
- `mcp-server/docker-compose.yml`
- `mcp-server/main.py` (lines 160-162)

### 4. LLM API Keys

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| Anthropic API Key | `ANTHROPIC_API_KEY` | .env file | `OsirisCare/Production/Anthropic` |
| OpenAI API Key | `OPENAI_API_KEY` | .env file | `OsirisCare/Production/OpenAI` |
| Azure OpenAI Key | `AZURE_OPENAI_API_KEY` | .env file | `OsirisCare/Production/AzureOpenAI` |
| Azure OpenAI Endpoint | `AZURE_OPENAI_ENDPOINT` | .env file | `OsirisCare/Production/AzureOpenAI` |

**Files that use this:**
- `mcp-server/server.py` (lines 42-43)
- `mcp-server/central-command/backend/l2_planner.py` (lines 22-28)

### 5. Email/SMTP Credentials

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| SMTP Host | `SMTP_HOST` | hardcoded | `OsirisCare/Production/SMTP` |
| SMTP User | `SMTP_USER` | .env file | `OsirisCare/Production/SMTP` |
| SMTP Password | `SMTP_PASSWORD` | .env file | `OsirisCare/Production/SMTP` |

**Files that use this:**
- `mcp-server/central-command/backend/email_alerts.py` (lines 19-24)
- `mcp-server/central-command/backend/escalation_engine.py` (lines 41-44)

### 6. Admin Dashboard Credentials

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| Default Admin Password | hardcoded `admin` | auth.py line 81 | `OsirisCare/Production/AdminDashboard` |

**Files that use this:**
- `mcp-server/central-command/backend/auth.py` (line 81-82)

### 7. Client Site Credentials (Stored in Database)

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| Windows Domain Creds | `site_credentials` table | PostgreSQL (encrypted) | Per-client vaults |
| SSH Keys | `site_credentials` table | PostgreSQL (encrypted) | Per-client vaults |

**Files that use this:**
- `mcp-server/central-command/backend/sites.py`
- `mcp-server/server.py` (checkin response)

### 8. Appliance API Keys

| Secret | Location | Current Storage | 1Password Item |
|--------|----------|-----------------|----------------|
| Site API Keys | `sites.api_key` column | PostgreSQL | Auto-generated |

**Files that use this:**
- `mcp-server/server.py` (checkin validation)
- `packages/compliance-agent/src/compliance_agent/appliance_client.py`

---

## Production Server Locations

### VPS (178.156.162.116)

| Path | Contents | Risk |
|------|----------|------|
| `/opt/mcp-server/.env` | All production secrets | **CRITICAL** |
| `/opt/mcp-server/docker-compose.yml` | References .env | Medium |
| PostgreSQL container | Hashed admin password | Low |
| `site_credentials` table | Encrypted client creds | Medium |

---

## Hardcoded Values Status

### Fixed (Session 17 - 2026-01-08)

1. ✅ **Default admin password** - `auth.py:71-108`
   - Now uses `ADMIN_INITIAL_PASSWORD` env var
   - Generates random 16-char password if not set
   - Logs warning to set password on first boot

2. ✅ **SMTP settings** - `escalation_engine.py:41-44`
   - Now uses `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` env vars
   - Defaults to mail.privateemail.com:587

3. ✅ **Documentation example API keys** - `Documentation.tsx`
   - Replaced with `"YOUR_API_KEY_HERE"` placeholders

---

## Environment Variables Reference

```bash
# Database
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/DB
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=

# Redis
REDIS_URL=redis://:PASSWORD@HOST:6379/0
REDIS_PASSWORD=

# MinIO
MINIO_ROOT_USER=
MINIO_ROOT_PASSWORD=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_BUCKET=evidence-worm

# LLM
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-02-15-preview
LLM_MODEL=gpt-4o

# SMTP
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=alerts@osiriscare.net
ALERT_EMAIL=administrator@osiriscare.net

# Admin
ADMIN_INITIAL_PASSWORD=  # Required on first boot

# Server
LOG_LEVEL=INFO
RATE_LIMIT_REQUESTS=10
RATE_LIMIT_WINDOW=300
ORDER_TTL_SECONDS=900
```

---

## 1Password Vault Structure

**Actual Vault:** `Central Command` (not OsirisCare)

```
Central Command/
├── Anthropic Key           # ✅ EXISTS - Claude API key (credential field)
├── PostgreSQL              # NEEDED - DB credentials (username, password, database)
├── Redis                   # NEEDED - Cache password (password field)
├── MinIO                   # NEEDED - Object storage (username, password)
├── OpenAI Key              # OPTIONAL - GPT API key (credential field)
├── SMTP                    # NEEDED - Email (server, username, password)
├── Admin Dashboard         # NEEDED - Initial admin password (password field)
└── VPS-SSH                 # OPTIONAL - SSH keys for 178.156.162.116
```

**To create remaining items:**
```bash
# PostgreSQL
op item create --vault "Central Command" --category login --title "PostgreSQL" \
  username=mcp password="SECURE_PASSWORD" database=mcp

# Redis
op item create --vault "Central Command" --category password --title "Redis" \
  password="SECURE_PASSWORD"

# MinIO
op item create --vault "Central Command" --category login --title "MinIO" \
  username=minio password="SECURE_PASSWORD"

# SMTP
op item create --vault "Central Command" --category login --title "SMTP" \
  server="mail.privateemail.com" username="jbouey@osiriscare.net" password="YOUR_PASSWORD"

# Admin Dashboard
op item create --vault "Central Command" --category password --title "Admin Dashboard" \
  password="SECURE_PASSWORD"
```

---

## Rotation Schedule

| Secret Type | Rotation Frequency | Last Rotated |
|-------------|-------------------|--------------|
| Database passwords | 90 days | Never |
| Redis password | 90 days | Never |
| MinIO credentials | 90 days | Never |
| LLM API keys | On compromise | N/A |
| SMTP password | 90 days | Never |
| Admin password | Immediately after setup | Never |
| Client credentials | Per client policy | Varies |

---

## Immediate Actions Required

1. [ ] Change default admin password on production (set `ADMIN_INITIAL_PASSWORD` env var)
2. [ ] Rotate all production passwords (currently using initial values)
3. [x] Remove API key from Documentation.tsx examples ✅ (Session 17)
4. [x] Set up 1Password CLI integration ✅ (`scripts/load-secrets.sh`)
5. [x] Create `.env.template` with placeholder values ✅
6. [x] Add `.env` to `.gitignore` (verify not committed) ✅
7. [ ] Create 1Password items for all secrets (only Anthropic Key exists)
8. [ ] Deploy updated `.env` to production VPS

---

## Security Notes

- **Never commit `.env` files** - Already in `.gitignore`
- **Client credentials** are stored encrypted in PostgreSQL `site_credentials` table
- **Appliance API keys** are generated randomly and stored hashed
- **Session tokens** use 256-bit entropy and are hashed before storage
