# Deployment Script Fix - Directory Creation Issue

## Problem

When running on a fresh system (like your iMac), the deployment script failed with:

```
./scripts/deploy-vbox-vms.sh: line 67: /Users/jrelly/Documents/msp-flake/deploy/deploy.log: No such file or directory
tee: /Users/jrelly/Documents/msp-flake/deploy/deploy.log: No such file or directory
[ERROR] Script failed with exit code: 1
```

## Root Cause

The script tried to write to a log file **before** creating the deploy directory:

1. Script starts
2. Log functions defined (try to write to `deploy/deploy.log`)
3. `validate_environment()` called (creates `deploy/` directory)
4. **Too late!** - Logging already failed

## Fix Applied

**Two-part fix:**

### 1. Made logging functions defensive

```bash
log_info() {
    local msg="${GREEN}[INFO]${NC} $*"
    echo -e "$msg"
    # Only write to log if directory exists
    if [ -d "$DEPLOY_DIR" ]; then
        echo -e "$msg" >> "$LOG_FILE"
    fi
}
```

### 2. Created directory immediately in validation

```bash
validate_environment() {
    # Create deploy directory FIRST (before any logging)
    mkdir -p "$DEPLOY_DIR" 2>/dev/null || {
        echo -e "${RED}[ERROR]${NC} Failed to create deploy directory: $DEPLOY_DIR" >&2
        exit 1
    }

    # Initialize log file
    echo "=== MSP VirtualBox Deployment Log ===" > "$LOG_FILE"
    # ... rest of validation
}
```

## Files Changed

- `scripts/deploy-vbox-vms.sh` - Main deployment script
- `quick-vm-test.sh` - Quick test script (added pre-flight checks)

## Testing

The fix ensures:

✅ Works on fresh systems (no deploy directory)
✅ Works on existing systems (deploy directory present)
✅ Gracefully handles permission errors
✅ Logs to console even if log file can't be created

## To Verify Fix

```bash
# Remove deploy directory (simulates fresh system)
rm -rf deploy/

# Run script (should work now)
./scripts/deploy-vbox-vms.sh --package

# Check that deploy directory was created
ls -la deploy/

# Check log file exists and has content
cat deploy/deploy.log
```

## For Your iMac

Pull the latest changes and try again:

```bash
cd ~/Documents/msp-flake
git pull origin main
./scripts/deploy-vbox-vms.sh --full test-client-wired
```

Should now work without errors!

---

**Fixed:** 2025-11-17
**Tested:** Fresh directory creation
**Status:** ✅ Ready for deployment
