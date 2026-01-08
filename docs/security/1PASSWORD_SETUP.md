# 1Password Setup for OsirisCare

This guide walks you through setting up 1Password to manage OsirisCare secrets.

---

## Prerequisites

1. **1Password Account** with a vault you can create/manage
2. **1Password CLI** installed:
   ```bash
   brew install 1password-cli
   ```
3. **Sign in to 1Password CLI**:
   ```bash
   op signin
   ```

---

## Step 1: Create Vault Structure

Create a vault called `OsirisCare` with the following items:

### Production Environment Items

```bash
# Create the vault
op vault create OsirisCare

# Create PostgreSQL item
op item create --vault OsirisCare \
  --category login \
  --title "Production/PostgreSQL" \
  --url "postgresql://postgres:5432" \
  username=mcp \
  password="YOUR_STRONG_PASSWORD" \
  database=mcp

# Create Redis item
op item create --vault OsirisCare \
  --category password \
  --title "Production/Redis" \
  password="YOUR_STRONG_PASSWORD"

# Create MinIO item
op item create --vault OsirisCare \
  --category login \
  --title "Production/MinIO" \
  username=minio \
  password="YOUR_STRONG_PASSWORD" \
  access_key=minio \
  secret_key="YOUR_STRONG_PASSWORD"

# Create Anthropic API key
op item create --vault OsirisCare \
  --category "API Credential" \
  --title "Production/Anthropic" \
  credential="sk-ant-api03-..."

# Create SMTP credentials
op item create --vault OsirisCare \
  --category login \
  --title "Production/SMTP" \
  --url "mail.privateemail.com" \
  username="alerts@osiriscare.net" \
  password="YOUR_SMTP_PASSWORD" \
  server="mail.privateemail.com"

# Create Admin Dashboard credentials
op item create --vault OsirisCare \
  --category password \
  --title "Production/AdminDashboard" \
  password="YOUR_ADMIN_PASSWORD"
```

---

## Step 2: Load Secrets

### Interactive (Development)

```bash
cd mcp-server
./scripts/load-secrets.sh --development
```

### Production Deployment

```bash
cd mcp-server
./scripts/load-secrets.sh --production
```

### CI/CD Pipeline

Use a 1Password Service Account:

```bash
# Set service account token
export OP_SERVICE_ACCOUNT_TOKEN="..."

# Generate .env
./scripts/load-secrets.sh --export > .env
```

---

## Step 3: Verify

```bash
# Check that secrets loaded
cat .env | grep -v "^#" | grep "="

# Test database connection
docker compose exec postgres psql -U mcp -c "SELECT 1"

# Test Redis connection
docker compose exec redis redis-cli ping
```

---

## Step 4: Deploy

```bash
# Restart services to pick up new secrets
docker compose down
docker compose up -d

# Verify services healthy
docker compose ps
```

---

## Rotating Secrets

### Database Password Rotation

```bash
# 1. Update 1Password
op item edit "OsirisCare/Production/PostgreSQL" password="NEW_PASSWORD"

# 2. Reload secrets
./scripts/load-secrets.sh --production

# 3. Update PostgreSQL user
docker compose exec postgres psql -U postgres -c \
  "ALTER USER mcp PASSWORD 'NEW_PASSWORD';"

# 4. Restart application
docker compose restart mcp-server
```

### API Key Rotation

```bash
# 1. Generate new key in provider console (Anthropic/OpenAI)
# 2. Update 1Password
op item edit "OsirisCare/Production/Anthropic" credential="NEW_KEY"

# 3. Reload secrets
./scripts/load-secrets.sh --production

# 4. Restart application
docker compose restart mcp-server
```

---

## Security Best Practices

1. **Never commit `.env` files** - Already in `.gitignore`
2. **Rotate secrets every 90 days** - See SECRETS_INVENTORY.md for schedule
3. **Use service accounts for CI/CD** - Don't use personal 1Password accounts
4. **Audit access regularly** - Check who has vault access
5. **Monitor for leaked secrets** - Use GitHub secret scanning

---

## Troubleshooting

### "Not signed in to 1Password"
```bash
op signin
```

### "Vault not found"
```bash
op vault list
# Create if missing
op vault create OsirisCare
```

### "Item not found"
```bash
# List items in vault
op item list --vault OsirisCare
```

### Service won't start after secret rotation
```bash
# Check logs
docker compose logs mcp-server

# Verify .env format
cat .env | head -20

# Ensure no blank lines in .env values
```

---

## Reference

- [1Password CLI Documentation](https://developer.1password.com/docs/cli/)
- [OsirisCare Secrets Inventory](./SECRETS_INVENTORY.md)
- [Environment Template](../../mcp-server/.env.template)
