# Cachix Setup Guide

## What is Cachix?

Cachix is a binary cache for Nix that speeds up builds by caching compiled packages. Without it, GitHub Actions will rebuild everything from source (~15-20 minutes). With it, builds complete in ~2-5 minutes.

## Setup Instructions

### 1. Create Cachix Account

Visit https://app.cachix.org and sign up (free tier available)

### 2. Create Binary Cache

1. Click "Create binary cache"
2. Name it: `msp-platform` (must match workflow config)
3. Choose visibility:
   - **Public** (recommended for open source): Free, anyone can use
   - **Private**: Requires paid plan

### 3. Generate Auth Token

1. Go to https://app.cachix.org/personal-auth-tokens
2. Click "Create token"
3. Give it a name: `msp-platform-github-actions`
4. Copy the token (starts with `eyJ...`)

### 4. Add Token to GitHub Secrets

1. Go to your GitHub repository
2. Navigate to: **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `CACHIX_AUTH_TOKEN`
5. Value: Paste the token from step 3
6. Click **Add secret**

### 5. Verify Setup

Push a commit to trigger the workflow:

```bash
git commit --allow-empty -m "test: verify cachix setup"
git push
```

Check the Actions tab - the "Setup Cachix" step should now succeed.

## Alternative: Use Public Caches Only

If you don't want to create your own Cachix cache, you can use only the public nixos cache. See Option 2 below.

## Troubleshooting

### "Binary cache doesn't exist or it's private"

**Cause:** Cache name mismatch or token not set

**Fix:**
```bash
# Verify cache name in workflow matches Cachix
grep "name:" .github/workflows/build-and-sign.yml

# Should show: name: msp-platform
```

### "Invalid auth token"

**Cause:** Token expired or incorrect

**Fix:**
1. Generate new token in Cachix dashboard
2. Update GitHub secret `CACHIX_AUTH_TOKEN`

### Workflow still fails after setup

**Cause:** Token permissions or cache visibility

**Fix:**
1. Ensure token has "Write" permission
2. Make cache public or ensure token is valid for private cache
3. Check cache settings in Cachix dashboard

## Cost Considerations

**Free Tier:**
- Public cache: Unlimited
- Private cache: 5GB storage

**Paid Plans:**
- Start at $20/month for private caches
- Recommended only for production/enterprise

**Recommendation for this project:**
- Use **public cache** (free, sufficient for open source)
- Caches NixOS packages, VM images, build artifacts
- No sensitive data in cache (only public packages)
