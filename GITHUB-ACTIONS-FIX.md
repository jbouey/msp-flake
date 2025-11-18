# GitHub Actions Build Fix - Container Parameter Mismatch

## Issue

GitHub Actions workflow was failing with:
```
error: function 'anonymous lambda' called without required argument 'infra-watcher'
at /nix/store/.../flake/container/default.nix:2:1:
    1| # flake/container/default.nix - Fixed version
    2| { pkgs, infra-watcher, nix2container }:
     | ^
```

## Root Cause

**Parameter Name Mismatch:**

The container build function signature in `flake/container/default.nix`:
```nix
{ pkgs, infra-watcher, nix2container }:
```

But `flake.nix` was calling it with the wrong parameter name:
```nix
container-img = import ./flake/container/default.nix {
  inherit pkgs log-watcher;  # ❌ Wrong parameter name
  nix2container = n2cPkgs.nix2container;
};
```

**Why This Happened:**

In the flake, there's an alias:
```nix
log-watcher = infra-watcher-fixed;
infra-watcher-fixed = import ./flake/pkgs/infra-watcher-fixed.nix { inherit pkgs; };
```

The container file was written to expect `infra-watcher` as the parameter name, but the call was using the alias `log-watcher`.

## Solution

Fixed the parameter passing in `flake.nix`:

```diff
 container-img = import ./flake/container/default.nix {
-  inherit pkgs log-watcher;
+  inherit pkgs;
+  infra-watcher = infra-watcher-fixed;
   nix2container = n2cPkgs.nix2container;
 };
```

**Why This Works:**
- `infra-watcher-fixed` is the actual package
- We pass it with the correct parameter name `infra-watcher`
- The container function receives exactly what it expects

## Technical Details

### Nix Function Calling Rules

Nix has strict parameter matching. When you define a function:
```nix
{ pkgs, infra-watcher, nix2container }: ...
```

The caller **must** pass parameters with those exact names:
```nix
someFunction {
  inherit pkgs;            # ✅ Correct name
  infra-watcher = value;   # ✅ Correct name
  nix2container = value;   # ✅ Correct name
}
```

**This will fail:**
```nix
someFunction {
  inherit pkgs log-watcher;  # ❌ log-watcher != infra-watcher
  nix2container = value;
}
# Error: missing required argument 'infra-watcher'
```

### File Structure

```
flake.nix                           # Main flake (was calling with wrong name)
├── flake/container/default.nix    # Container builder (expects infra-watcher)
└── flake/pkgs/infra-watcher-fixed.nix  # Actual package implementation
```

## Testing

### Local Verification
```bash
# Evaluate flake structure
nix flake show

# Check for errors (takes a while)
nix flake check --show-trace
```

### GitHub Actions
Workflow should now:
1. ✅ Pull latest code with fix
2. ✅ Run `nix flake check` successfully
3. ✅ Build container image
4. ✅ Complete without errors

## Changes Made

**Commit:** `f6b7d6c` - "fix: correct container build parameter name"

**Files Modified:**
- `flake.nix` - Fixed container-img parameter passing
- `flake.lock` - Updated with latest input locks

## Verification

Check GitHub Actions workflow:
```
https://github.com/jbouey/msp-flake/actions
```

Expected result:
```
✅ Run nix flake check --show-trace
✅ Build container
✅ All checks passed
```

## Lessons Learned

1. **Parameter names must match exactly** in Nix function calls
2. **Aliases don't transfer through function boundaries** - `log-watcher` is local to flake.nix
3. **Always check function signatures** when importing modules
4. **Use `nix flake show`** to quickly validate flake structure locally

## Related Files

- `flake.nix` - Main flake configuration
- `flake/container/default.nix` - Container builder (function signature)
- `flake/pkgs/infra-watcher-fixed.nix` - Package implementation
- `.github/workflows/build-and-sign.yml` - CI/CD workflow

---

**Fixed:** 2025-11-18
**Commit:** f6b7d6c
**Status:** ✅ Pushed to GitHub, awaiting CI confirmation
