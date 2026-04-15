// Package daemon — cold-boot self-heal for known systemd failure modes.
//
// Mesh Hardening Phase 2 (Daemon 0.4.5): detect + remove stale
// /run/systemd/system/appliance-daemon.service.d/override.conf drop-ins that
// the update_daemon fleet flow leaves behind when a binary gets moved or
// downgraded out from under a prior override. The failure mode observed on
// .226 was: override.conf still pointed at /var/lib/msp/appliance-daemon,
// but the file at that path was an older version than the intended one —
// systemd happily ran the stale binary forever. No alert, no self-repair.
//
// On every cold start we emit a one-line diagnostic describing the running
// binary + any override, and remove the override when its ExecStart target
// no longer exists. The canonical /etc/systemd/system/appliance-daemon.service
// unit then takes over on the next restart.
package daemon

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// systemdOverridePath is the drop-in override.conf written by the
// update_daemon fleet order flow. See processor.go handleUpdateDaemon.
const systemdOverridePath = "/run/systemd/system/appliance-daemon.service.d/override.conf"

// HealStaleSystemdOverride logs a startup diagnostic and removes override.conf
// when its ExecStart target is missing. Safe to call unconditionally at cold
// start; no-op when no override is present. Does NOT restart the daemon —
// if the override was pinning a stale-but-present binary, removal alone isn't
// enough; operator gets a loud log line instead.
func HealStaleSystemdOverride() {
	exePath, err := os.Readlink("/proc/self/exe")
	if err != nil {
		exePath = "<unknown>"
	}
	overrideTarget := parseOverrideExecStart(systemdOverridePath)
	canonicalPath := "/run/current-system/sw/bin/appliance-daemon"
	canonicalExists := fileExists(canonicalPath)

	log.Printf(
		"[boot-selfheal] startup diagnostic: version=%s exe=%s override_target=%q canonical=%s canonical_exists=%v",
		Version, exePath, overrideTarget, canonicalPath, canonicalExists,
	)

	if overrideTarget == "" {
		return
	}

	if fileExists(overrideTarget) {
		if overrideTarget != exePath {
			log.Printf(
				"[boot-selfheal] NOTE: override target (%s) differs from /proc/self/exe (%s) — systemd ignored override, possibly due to a prior daemon-reload race",
				overrideTarget, exePath,
			)
		}
		return
	}

	log.Printf(
		"[boot-selfheal] STALE override detected: %s points at %q which does not exist. Removing override + reloading systemd.",
		systemdOverridePath, overrideTarget,
	)

	if err := removeStaleOverride(); err != nil {
		log.Printf("[boot-selfheal] ERROR: failed to remove stale override: %v", err)
		return
	}
	log.Printf(
		"[boot-selfheal] stale override removed + systemd reloaded. Canonical unit at /etc/systemd/system/appliance-daemon.service takes over on next restart.",
	)
}

// parseOverrideExecStart reads override.conf and returns the effective
// ExecStart binary path. override.conf format:
//
//	[Service]
//	ExecStart=          # empty — clears inherited ExecStart
//	ExecStart=/path/to/binary [args ...]
//
// Returns "" if the file does not exist, has no effective ExecStart, or is
// unreadable.
func parseOverrideExecStart(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	var last string
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "ExecStart=") {
			continue
		}
		rest := strings.TrimSpace(strings.TrimPrefix(line, "ExecStart="))
		if rest == "" {
			continue
		}
		fields := strings.Fields(rest)
		if len(fields) == 0 {
			continue
		}
		candidate := strings.Trim(fields[0], "\"'")
		if filepath.IsAbs(candidate) {
			last = candidate
		}
	}
	return last
}

func fileExists(path string) bool {
	if path == "" {
		return false
	}
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// removeStaleOverride uses systemd-run to escape ProtectSystem=strict, same
// pattern processor.go uses for update_daemon installs. /run/systemd is
// read-only inside the sandbox.
func removeStaleOverride() error {
	unitSuffix := fmt.Sprintf("%d", time.Now().UnixMilli())
	script := fmt.Sprintf("rm -f %s && systemctl daemon-reload", systemdOverridePath)

	bashPath := "/run/current-system/sw/bin/bash"
	if !fileExists(bashPath) {
		bashPath = "/bin/bash"
	}
	envPath := "PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin"

	cmd := exec.Command(
		"systemd-run",
		"--unit=msp-daemon-bootselfheal-"+unitSuffix,
		"--wait", "--pipe", "--collect",
		"--property=TimeoutStartSec=15",
		"--setenv="+envPath,
		bashPath, "-c", script,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("systemd-run: %w\n%s", err, string(out))
	}
	return nil
}
