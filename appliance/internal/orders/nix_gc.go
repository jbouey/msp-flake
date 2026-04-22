package orders

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"time"
)

// handleNixGC runs nix-collect-garbage on the appliance to reclaim disk
// space in /nix/store. Introduced 2026-04-22 after the SWE-2 `nixos_rebuild
// _success_drought` invariant surfaced a fleet-wide 59-day rebuild drought
// and the 0.4.7 diagnostic upgrade revealed the real nix error on the
// primary canary: `error: writing to file: No space left on device`.
//
// Safety: the order is idempotent, confined to /nix/store reclamation, and
// never touches the running system closure (the current generation is
// always retained). `--delete-older-than <N>d` preserves rollback targets
// inside the window.
//
// Parameters:
//
//	older_than_days (int, default 14, range 1..365):
//	    passed to nix-collect-garbage --delete-older-than; generations
//	    newer than this are kept so rollback stays possible.
//
//	optimise (bool, default true):
//	    after GC, run `nix-store --optimise` to hardlink duplicate store
//	    paths. Best-effort — failure does NOT fail the order because GC
//	    already succeeded by the time optimise runs.
//
// Result payload fields:
//
//	before_bytes / after_bytes — used bytes on the /nix filesystem
//	    (statfs), snapshotted before and after the GC run. Used bytes,
//	    not size of /nix/store itself, so the numbers line up with `df`.
//
//	bytes_freed — before_bytes - after_bytes (0 if the fs grew or stats
//	    were unavailable).
//
//	gc_duration_ms / optimise_duration_ms — wall-clock time per phase.
func (p *Processor) handleNixGC(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	olderDays := 14
	if v, ok := params["older_than_days"]; ok {
		switch n := v.(type) {
		case float64:
			olderDays = int(n)
		case int:
			olderDays = n
		case string:
			parsed, err := strconv.Atoi(n)
			if err != nil {
				return nil, fmt.Errorf("older_than_days must be an integer, got %q", n)
			}
			olderDays = parsed
		default:
			return nil, fmt.Errorf("older_than_days must be numeric, got %T", v)
		}
	}
	if olderDays < 1 || olderDays > 365 {
		return nil, fmt.Errorf("older_than_days out of range (got %d, want 1..365)", olderDays)
	}

	optimise := true
	if v, ok := params["optimise"]; ok {
		if b, ok := v.(bool); ok {
			optimise = b
		}
	}

	beforeBytes := nixFsUsedBytes()

	gcDuration := fmt.Sprintf("%dd", olderDays)
	// systemd-run escapes ProtectSystem=strict. nix-collect-garbage lives in
	// /run/current-system/sw/bin on every NixOS system.
	gcCmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-nix-gc", "--wait", "--pipe", "--collect",
		"--property=TimeoutStartSec=1200",
		"/run/current-system/sw/bin/nix-collect-garbage", "-d",
		"--delete-older-than", gcDuration)
	gcStart := time.Now()
	gcOut, gcErr := gcCmd.CombinedOutput()
	gcMs := time.Since(gcStart).Milliseconds()
	if gcErr != nil {
		logPath := filepath.Join(p.stateDir, "last-nix-gc-error.log")
		_ = os.WriteFile(logPath, gcOut, 0o644)
		outStr := string(gcOut)
		const head = 2048
		const tail = 2048
		truncated := outStr
		if len(outStr) > head+tail+64 {
			truncated = outStr[:head] +
				"\n…[truncated, full on appliance at " + logPath + "]…\n" +
				outStr[len(outStr)-tail:]
		}
		return nil, fmt.Errorf(
			"nix-collect-garbage failed after %dms: %v\nfull log: %s\n%s",
			gcMs, gcErr, logPath, truncated)
	}

	var optMs int64
	if optimise {
		optCmd := exec.CommandContext(ctx, "systemd-run",
			"--unit=msp-nix-optimise", "--wait", "--pipe", "--collect",
			"--property=TimeoutStartSec=600",
			"/run/current-system/sw/bin/nix-store", "--optimise")
		optStart := time.Now()
		_, optErr := optCmd.CombinedOutput()
		optMs = time.Since(optStart).Milliseconds()
		if optErr != nil {
			// Best-effort: the GC already succeeded. Report the failure
			// but don't fail the order — the operator still got disk back.
			log.Printf("[orders] nix-store --optimise failed (non-fatal): %v", optErr)
		}
	}

	afterBytes := nixFsUsedBytes()
	var bytesFreed int64
	if beforeBytes > 0 && afterBytes > 0 && beforeBytes > afterBytes {
		bytesFreed = beforeBytes - afterBytes
	}

	return map[string]interface{}{
		"older_than_days":      olderDays,
		"optimise":             optimise,
		"before_bytes":         beforeBytes,
		"after_bytes":          afterBytes,
		"bytes_freed":          bytesFreed,
		"gc_duration_ms":       gcMs,
		"optimise_duration_ms": optMs,
	}, nil
}

// nixFsUsedBytes returns used bytes on the filesystem containing /nix.
// Returns 0 on stat failure (best-effort — the order doesn't fail if we
// can't snapshot the fs, the operator just loses the bytes_freed delta).
func nixFsUsedBytes() int64 {
	var st syscall.Statfs_t
	if err := syscall.Statfs("/nix", &st); err != nil {
		return 0
	}
	total := int64(st.Blocks) * int64(st.Bsize)
	free := int64(st.Bavail) * int64(st.Bsize)
	used := total - free
	if used < 0 {
		return 0
	}
	return used
}
