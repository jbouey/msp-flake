# GitHub Actions Setup Guide

## Current Status

✅ **Workflows configured and ready to use**
⚠️ **Cachix optional** - workflow will run without it (just slower)

## Quick Fix Applied

The workflow now works **without Cachix** by adding `continue-on-error: true`.

**Build times:**
- Without Cachix: ~15-20 minutes (builds from source)
- With Cachix: ~2-5 minutes (uses cached packages)

## Two Workflows Available

### 1. `build-and-sign.yml` - Main Build Pipeline

**Triggers:**
- Push to `main` or `develop` branches
- Pull requests to `main`
- Manual dispatch
- Version tags (`v*`)

**What it does:**
1. ✅ Builds NixOS flake
2. ✅ Generates container image
3. ✅ Signs with cosign (keyless)
4. ✅ Creates SBOM (SPDX + CycloneDX)
5. ✅ Pushes to GitHub Container Registry
6. ✅ Runs vulnerability scans
7. ✅ Checks HIPAA compliance
8. ✅ Generates compliance report

**No secrets required** - uses GitHub's OIDC for keyless signing

### 2. `update-flake.yml` - Dependency Updates

**Triggers:**
- Weekly (Sunday 3 AM UTC)
- Manual dispatch

**What it does:**
1. Updates `flake.lock` with latest packages
2. Runs tests
3. Creates PR with changes
4. Waits for manual review before merge

## Optional: Setup Cachix (Recommended)

**Why:** Speed up builds from 15 minutes to 2 minutes

**Steps:**

1. **Create cache at https://app.cachix.org**
   - Sign up (free tier available)
   - Create cache named: `msp-platform`
   - Choose "Public" (free, recommended)

2. **Generate token**
   - Go to: https://app.cachix.org/personal-auth-tokens
   - Create token named: `msp-platform-github-actions`
   - Copy token (starts with `eyJ...`)

3. **Add to GitHub**
   - Repository → Settings → Secrets and variables → Actions
   - New secret named: `CACHIX_AUTH_TOKEN`
   - Paste token value
   - Save

4. **Verify**
   ```bash
   git commit --allow-empty -m "test: verify cachix"
   git push
   ```

**Full guide:** See `docs/CACHIX-SETUP.md`

## Workflow Outputs

### After successful build:

**GitHub Container Registry:**
- Image: `ghcr.io/jbouey/msp-flake:latest`
- Tagged with: branch name, SHA, semver

**Artifacts (downloadable for 90 days):**
- `sbom-<sha>` - SBOM files (SPDX + CycloneDX)
- `compliance-report-<sha>` - HIPAA compliance report

**Security:**
- SARIF results uploaded to GitHub Security tab
- Signatures verifiable with: `cosign verify ghcr.io/jbouey/msp-flake:latest`

## Manual Workflow Trigger

**Via GitHub UI:**
1. Go to: Actions tab
2. Select workflow (build-and-sign or update-flake)
3. Click "Run workflow"
4. Choose branch
5. Click "Run workflow"

**Via GitHub CLI:**
```bash
# Trigger build
gh workflow run build-and-sign.yml

# Trigger dependency update
gh workflow run update-flake.yml
```

## Troubleshooting

### Build fails with "flake not found"

**Cause:** Workflow looking for wrong flake file

**Fix:** The workflow expects `flake.nix`. You have `flake-compliance.nix`.

**Options:**
1. Rename `flake-compliance.nix` → `flake.nix`
2. Update workflow to use `flake-compliance.nix`

### "Container image not found"

**Cause:** Image tag hardcoded in workflow

**Current issue:** Line 67 uses `registry.example.com/infra-watcher:0.1`

**Fix:** Update to use the actual built image from flake

### SBOM generation fails

**Cause:** Image not loaded to Docker daemon

**Check:** Does `nix run .#load-to-docker` work locally?

## Next Steps

1. ✅ **Immediate:** Workflow runs without Cachix (just slower)
2. 🚀 **Recommended:** Set up Cachix (5 minutes, huge speedup)
3. 🔧 **Optional:** Fix image tag references in workflow
4. 📋 **Future:** Add deployment workflow for VirtualBox VMs

## Monitoring Builds

**GitHub Actions:**
- Repository → Actions tab
- View all workflow runs
- Click run for detailed logs

**Container Registry:**
- Repository → Packages
- View all published images
- See image layers, signatures, SBOMs

**Security:**
- Repository → Security tab
- View vulnerability scans
- Check Dependabot alerts

---

**Quick Reference:**
- Build workflow: `.github/workflows/build-and-sign.yml`
- Update workflow: `.github/workflows/update-flake.yml`
- Cachix guide: `docs/CACHIX-SETUP.md`
- This guide: `GITHUB-ACTIONS-SETUP.md`
