package daemon

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// wgStatus holds parsed WireGuard tunnel state for audit logging.
type wgStatus struct {
	Connected     bool
	PeerEndpoint  string
	LastHandshake time.Time
	BytesReceived int64
	BytesSent     int64
}

// findWgBinary locates the wg command, checking PATH and common NixOS locations.
func findWgBinary() string {
	if p, err := exec.LookPath("wg"); err == nil {
		return p
	}
	// NixOS: wg may not be in the daemon's PATH but exists via nix profile or /run
	for _, dir := range []string{"/run/current-system/sw/bin", "/root/.nix-profile/bin"} {
		p := filepath.Join(dir, "wg")
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return ""
}

// checkWireGuardStatus parses `wg show wg0 dump` output to determine tunnel state.
// Returns nil if WireGuard is not configured or the interface doesn't exist.
func checkWireGuardStatus() *wgStatus {
	wgBin := findWgBinary()
	if wgBin == "" {
		return nil // wg binary not found
	}
	out, err := exec.Command(wgBin, "show", "wg0", "dump").Output()
	if err != nil {
		return nil // WireGuard not configured or wg0 doesn't exist
	}

	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) < 2 {
		return nil
	}

	// Second line is the peer: pubkey, preshared, endpoint, allowed-ips, latest-handshake, rx, tx, keepalive
	fields := strings.Split(lines[1], "\t")
	if len(fields) < 7 {
		return nil
	}

	status := &wgStatus{
		PeerEndpoint: fields[2],
	}

	// Parse latest handshake (unix timestamp)
	if ts, err := strconv.ParseInt(fields[4], 10, 64); err == nil && ts > 0 {
		status.LastHandshake = time.Unix(ts, 0)
		status.Connected = time.Since(status.LastHandshake) < 3*time.Minute
	}

	// Parse transfer stats
	if rx, err := strconv.ParseInt(fields[5], 10, 64); err == nil {
		status.BytesReceived = rx
	}
	if tx, err := strconv.ParseInt(fields[6], 10, 64); err == nil {
		status.BytesSent = tx
	}

	return status
}

// getWireGuardIP returns the IP address assigned to the wg0 interface, if any.
func getWireGuardIP() string {
	iface, err := net.InterfaceByName("wg0")
	if err != nil {
		return ""
	}
	addrs, err := iface.Addrs()
	if err != nil || len(addrs) == 0 {
		return ""
	}
	// Return first IP without the CIDR mask
	ip, _, err := net.ParseCIDR(addrs[0].String())
	if err != nil {
		return addrs[0].String()
	}
	return ip.String()
}

// wgMonitor tracks WireGuard tunnel state and logs transitions.
type wgMonitor struct {
	lastConnected *bool // nil = unknown (first check)
}

// check polls WireGuard status and logs state transitions.
// Returns the current status (may be nil if WireGuard is not configured).
func (m *wgMonitor) check() *wgStatus {
	status := checkWireGuardStatus()
	if status == nil {
		// Not configured — only log transition from connected to unconfigured
		if m.lastConnected != nil && *m.lastConnected {
			slog.Warn("tunnel wg0 no longer present (interface removed or deconfigured)", "component", "wireguard")
			f := false
			m.lastConnected = &f
		}
		return nil
	}

	connected := status.Connected

	if m.lastConnected == nil {
		// First check — log initial state
		if connected {
			slog.Info("tunnel UP", "component", "wireguard", "peer", status.PeerEndpoint, "handshake_ago", time.Since(status.LastHandshake).Truncate(time.Second), "rx", status.BytesReceived, "tx", status.BytesSent)
		} else {
			slog.Warn("tunnel DOWN", "component", "wireguard", "peer", status.PeerEndpoint, "last_handshake", status.LastHandshake.Format(time.RFC3339))
		}
	} else if connected != *m.lastConnected {
		// State transition
		if connected {
			slog.Info("tunnel RECONNECTED", "component", "wireguard", "peer", status.PeerEndpoint, "handshake_ago", time.Since(status.LastHandshake).Truncate(time.Second))
		} else {
			slog.Warn("tunnel DISCONNECTED", "component", "wireguard", "peer", status.PeerEndpoint, "last_handshake", status.LastHandshake.Format(time.RFC3339))
		}
	}

	m.lastConnected = &connected
	return status
}

// checkKeyIntegrity verifies that the WireGuard private key file has correct
// permissions (0600). Logs a warning if permissions are too permissive.
func checkKeyIntegrity(stateDir string) {
	keyPath := filepath.Join(stateDir, "wireguard", "private.key")
	info, err := os.Stat(keyPath)
	if err != nil {
		return // no key file — WireGuard not provisioned
	}

	// Check permissions — private key must be 0600 (owner read/write only)
	perm := info.Mode().Perm()
	if perm != 0o600 {
		slog.Warn("private key has wrong permissions", "component", "wireguard", "permissions", fmt.Sprintf("%04o", perm), "expected", "0600")
	}
}

// runWireGuardMonitor checks WireGuard tunnel status every 5 minutes and logs
// connection state transitions. Also performs periodic key file integrity checks.
// Runs until the context is cancelled.
func (d *Daemon) runWireGuardMonitor(ctx context.Context) {
	// Do an initial check immediately
	d.wgMon.check()
	checkKeyIntegrity(d.config.StateDir)

	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			d.wgMon.check()
			checkKeyIntegrity(d.config.StateDir)
		}
	}
}
