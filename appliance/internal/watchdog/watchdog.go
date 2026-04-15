// Package watchdog implements the appliance-watchdog service — a second
// systemd unit that runs alongside the main appliance daemon with its
// own Ed25519 identity + its own 2-minute checkin loop. It accepts a
// tight whitelist of six fleet-order types that can recover a wedged
// main daemon without requiring SSH:
//
//	watchdog_restart_daemon      systemctl restart appliance-daemon
//	watchdog_refetch_config      re-download /var/lib/msp/config.yaml
//	watchdog_reset_pin_store     delete /var/lib/msp/winrm_pins.json
//	watchdog_reset_api_key       trigger /api/provision/rekey flow
//	watchdog_redeploy_daemon     re-download + install daemon binary
//	watchdog_collect_diagnostics bundle journal + state, POST back
//
// Session 207 Phase W1. Backend surface shipped in Phase W0 —
// /api/watchdog/checkin + /diagnostics + /orders/{id}/complete. The
// systemd unit wrapping this binary ships in Phase W2 (appliance-
// disk-image.nix).
package watchdog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// Version tracks the watchdog binary. Bumped via ldflags at build time,
// same pattern as daemon.Version.
var Version = "0.1.0"

const (
	defaultCheckinInterval = 2 * time.Minute
	defaultHTTPTimeout     = 15 * time.Second
	defaultConfigPath      = "/etc/msp-watchdog.yaml"
	pinStorePath           = "/var/lib/msp/winrm_pins.json"
	diagBootPath           = "/boot/msp-boot-diag.json"
	beaconPath             = "/var/lib/msp/beacon.json"
)

// Config is the watchdog's own on-disk config — distinct from the main
// daemon's config.yaml so a corrupt main config cannot wedge the
// watchdog. Minimum set: site_id, appliance_id (with `-watchdog`
// suffix), api_key, api_endpoint.
type Config struct {
	SiteID      string `yaml:"site_id"`
	ApplianceID string `yaml:"appliance_id"`
	APIKey      string `yaml:"api_key"`
	APIEndpoint string `yaml:"api_endpoint"`
}

// LoadConfig reads YAML from path. Empty/missing returns an error; the
// main caller treats that as "idle until config lands" rather than
// crashing.
func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if cfg.APIEndpoint == "" {
		cfg.APIEndpoint = "https://api.osiriscare.net"
	}
	if cfg.SiteID == "" || cfg.ApplianceID == "" || cfg.APIKey == "" {
		return nil, errors.New("watchdog config missing required field (site_id / appliance_id / api_key)")
	}
	return &cfg, nil
}

// Watchdog is the long-lived object. One Run() per process.
type Watchdog struct {
	cfg    *Config
	http   *http.Client
	log    *slog.Logger
	boot   time.Time
	cpath  string
}

// New returns a Watchdog ready to Run.
func New(configPath string) (*Watchdog, error) {
	cfg, err := LoadConfig(configPath)
	if err != nil {
		return nil, err
	}
	return &Watchdog{
		cfg:   cfg,
		http:  &http.Client{Timeout: defaultHTTPTimeout},
		log:   slog.Default().With("component", "watchdog"),
		boot:  time.Now(),
		cpath: configPath,
	}, nil
}

// Run executes the 2-minute checkin loop until ctx is cancelled.
func (w *Watchdog) Run(ctx context.Context) error {
	w.log.Info("watchdog starting",
		"version", Version,
		"site_id", w.cfg.SiteID,
		"appliance_id", w.cfg.ApplianceID,
		"api_endpoint", w.cfg.APIEndpoint,
	)

	// Initial fast checkin so the backend observes us immediately on
	// service start — don't make the operator wait 2 min to confirm
	// that a newly-installed watchdog is wired correctly.
	w.tick(ctx)

	ticker := time.NewTicker(defaultCheckinInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			w.log.Info("watchdog shutting down")
			return nil
		case <-ticker.C:
			w.tick(ctx)
		}
	}
}

// tick is one pass through the checkin loop: collect state, POST
// /checkin, dispatch any pending orders, ACK completions. Every error
// is logged and otherwise swallowed — the watchdog's job is to stay
// alive so it can retry on the next tick.
func (w *Watchdog) tick(ctx context.Context) {
	payload := w.collectHealthPayload(ctx)
	pending, err := w.postCheckin(ctx, payload)
	if err != nil {
		w.log.Warn("checkin failed", "err", err)
		return
	}
	for _, o := range pending {
		w.executeOrder(ctx, o)
	}
}

// collectHealthPayload produces the JSON sent in /checkin.
func (w *Watchdog) collectHealthPayload(ctx context.Context) map[string]any {
	status := runCmd(ctx, "systemctl", "is-active", "appliance-daemon")
	substate := runCmd(ctx, "systemctl", "show", "appliance-daemon", "-p", "SubState", "--value")
	return map[string]any{
		"site_id":              w.cfg.SiteID,
		"appliance_id":         w.cfg.ApplianceID,
		"watchdog_version":     Version,
		"main_daemon_status":   status,
		"main_daemon_substate": substate,
		"uptime_seconds":       int(time.Since(w.boot).Seconds()),
		"wall_time":            time.Now().UTC().Format(time.RFC3339),
	}
}

type pendingOrder struct {
	OrderID    string         `json:"order_id"`
	OrderType  string         `json:"order_type"`
	Parameters map[string]any `json:"parameters"`
}

type checkinResponse struct {
	OK            bool           `json:"ok"`
	PendingOrders []pendingOrder `json:"pending_orders"`
}

// postCheckin issues POST /api/watchdog/checkin, returns the list of
// pending orders. Any non-2xx is an error.
func (w *Watchdog) postCheckin(ctx context.Context, payload map[string]any) ([]pendingOrder, error) {
	url := w.cfg.APIEndpoint + "/api/watchdog/checkin"
	body, _ := json.Marshal(payload)
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+w.cfg.APIKey)
	resp, err := w.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("checkin HTTP %d: %s", resp.StatusCode, string(respBody))
	}
	var cr checkinResponse
	if err := json.Unmarshal(respBody, &cr); err != nil {
		return nil, fmt.Errorf("parse checkin response: %w", err)
	}
	return cr.PendingOrders, nil
}

// executeOrder dispatches a single pending order to its handler, then
// POSTs the completion back to /api/watchdog/orders/{id}/complete.
func (w *Watchdog) executeOrder(ctx context.Context, o pendingOrder) {
	w.log.Info("executing watchdog order",
		"order_id", o.OrderID,
		"order_type", o.OrderType,
	)

	var output map[string]any
	var execErr error

	switch o.OrderType {
	case "watchdog_restart_daemon":
		output, execErr = w.restartDaemon(ctx)
	case "watchdog_collect_diagnostics":
		output, execErr = w.collectDiagnostics(ctx, o.OrderID)
	case "watchdog_reset_pin_store":
		output, execErr = w.resetPinStore(ctx, o.Parameters)
	case "watchdog_refetch_config":
		output, execErr = w.refetchConfig(ctx, o.Parameters)
	case "watchdog_reset_api_key":
		output, execErr = w.resetAPIKey(ctx, o.Parameters)
	case "watchdog_redeploy_daemon":
		output, execErr = w.redeployDaemon(ctx, o.Parameters)
	case "enable_recovery_shell_24h":
		output, execErr = w.enableRecoveryShell(ctx, o.Parameters)
	default:
		execErr = fmt.Errorf("unknown watchdog order_type %q", o.OrderType)
	}

	status := "success"
	errMsg := ""
	if execErr != nil {
		status = "failure"
		errMsg = execErr.Error()
		w.log.Error("watchdog order failed", "order_id", o.OrderID, "err", execErr)
	} else {
		w.log.Info("watchdog order ok", "order_id", o.OrderID, "order_type", o.OrderType)
	}

	w.ackOrder(ctx, o, status, output, errMsg)
}

// ackOrder posts the completion to /api/watchdog/orders/{id}/complete.
func (w *Watchdog) ackOrder(
	ctx context.Context, o pendingOrder, status string,
	output map[string]any, errMsg string,
) {
	body, _ := json.Marshal(map[string]any{
		"order_id":      o.OrderID,
		"order_type":    o.OrderType,
		"status":        status,
		"output":        output,
		"error_message": errMsg,
	})
	url := fmt.Sprintf("%s/api/watchdog/orders/%s/complete", w.cfg.APIEndpoint, o.OrderID)
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		w.log.Warn("ack build failed", "err", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+w.cfg.APIKey)
	resp, err := w.http.Do(req)
	if err != nil {
		w.log.Warn("ack send failed", "err", err)
		return
	}
	resp.Body.Close()
	if resp.StatusCode >= 300 {
		w.log.Warn("ack non-2xx", "status_code", resp.StatusCode, "order_id", o.OrderID)
	}
}

// ── Order handlers ───────────────────────────────────────────────────

func (w *Watchdog) restartDaemon(ctx context.Context) (map[string]any, error) {
	out, err := exec.CommandContext(ctx, "systemctl", "restart", "appliance-daemon").CombinedOutput()
	return map[string]any{
		"command": "systemctl restart appliance-daemon",
		"stdout":  string(out),
	}, err
}

func (w *Watchdog) collectDiagnostics(ctx context.Context, orderID string) (map[string]any, error) {
	bundle := map[string]any{
		"watchdog_version": Version,
		"wall_time":        time.Now().UTC().Format(time.RFC3339),
		"systemd_status":   runCmd(ctx, "systemctl", "status", "appliance-daemon", "--no-pager"),
		"daemon_journal":   runCmd(ctx, "journalctl", "-u", "appliance-daemon", "-n", "100", "--no-pager", "-o", "cat"),
		"ip_addr":          runCmd(ctx, "ip", "-j", "addr"),
		"dns_resolve":      runCmd(ctx, "host", "api.osiriscare.net"),
		"pin_store_exists": fileExists(pinStorePath),
		"config_yaml_sha":  sha256File(ctx, "/var/lib/msp/config.yaml"),
	}
	if d, err := os.ReadFile(diagBootPath); err == nil {
		bundle["boot_diag_json"] = string(d)
	}
	if b, err := os.ReadFile(beaconPath); err == nil {
		bundle["beacon_json"] = string(b)
	}

	body, _ := json.Marshal(map[string]any{
		"site_id":      w.cfg.SiteID,
		"appliance_id": w.cfg.ApplianceID,
		"order_id":     orderID,
		"bundle":       bundle,
	})
	url := w.cfg.APIEndpoint + "/api/watchdog/diagnostics"
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build diag req: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+w.cfg.APIKey)
	resp, err := w.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("post diag: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("diag upload HTTP %d: %s", resp.StatusCode, string(respBody))
	}
	return map[string]any{"bundle_keys": keys(bundle)}, nil
}

func (w *Watchdog) resetPinStore(_ context.Context, params map[string]any) (map[string]any, error) {
	// Host-scoped removal if parameters include a host; otherwise delete
	// the whole pin store to force re-TOFU on next WinRM call.
	host, _ := params["host"].(string)
	if host == "" {
		if err := os.Remove(pinStorePath); err != nil && !os.IsNotExist(err) {
			return nil, err
		}
		return map[string]any{"scope": "all", "removed_file": pinStorePath}, nil
	}
	// Host-scoped deletion: read, remove that key, atomic rename.
	data, err := os.ReadFile(pinStorePath)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]any{"scope": host, "note": "pin store absent — nothing to remove"}, nil
		}
		return nil, fmt.Errorf("read pin store: %w", err)
	}
	var pins map[string]any
	if err := json.Unmarshal(data, &pins); err != nil {
		return nil, fmt.Errorf("parse pin store: %w", err)
	}
	delete(pins, host)
	out, _ := json.Marshal(pins)
	tmp := pinStorePath + ".tmp"
	if err := os.WriteFile(tmp, out, 0o600); err != nil {
		return nil, err
	}
	if err := os.Rename(tmp, pinStorePath); err != nil {
		os.Remove(tmp)
		return nil, err
	}
	return map[string]any{"scope": host, "removed_key": host}, nil
}

// The remaining three handlers are STUBS for Phase W1.1. Shipping the
// binary without them is safe — the backend won't issue these orders
// until the operator escalates. Each returns a structured error so the
// completion ACK records "not yet implemented" rather than looking
// silently successful.

func (w *Watchdog) refetchConfig(_ context.Context, _ map[string]any) (map[string]any, error) {
	return nil, errors.New("watchdog_refetch_config: not implemented in W1 (stubbed for W1.1)")
}

func (w *Watchdog) resetAPIKey(_ context.Context, _ map[string]any) (map[string]any, error) {
	return nil, errors.New("watchdog_reset_api_key: not implemented in W1 (stubbed for W1.1)")
}

func (w *Watchdog) redeployDaemon(_ context.Context, _ map[string]any) (map[string]any, error) {
	return nil, errors.New("watchdog_redeploy_daemon: not implemented in W1 (stubbed for W1.1)")
}

// enableRecoveryShell — Session 207 Phase S escape hatch. Writes the
// operator's SSH public key to /etc/msp-recovery-authorized-keys,
// `systemctl start sshd`, and arms a systemd-run transient timer for
// `duration_hours` that stops sshd + wipes the keys file when it fires.
// The timer is systemd-enforced — operator oversight can fail; the
// timer cannot.
//
// Requires the installed system's NixOS config to have sshd present
// in the closure but wantedBy=[] (see Phase S+R+recovery wire-up in
// iso/appliance-disk-image.nix). Without that, systemctl start will
// fail because the unit isn't in the closure.
//
// Parameters (all strings in the fleet_order.parameters JSON):
//
//	ssh_pubkey     — single authorized_keys line (ssh-ed25519 …)
//	duration_hours — "1".."24"; parsed + clamped. Default "4".
func (w *Watchdog) enableRecoveryShell(ctx context.Context, params map[string]any) (map[string]any, error) {
	pubkey, _ := params["ssh_pubkey"].(string)
	pubkey = strings.TrimSpace(pubkey)
	if pubkey == "" {
		return nil, errors.New("enable_recovery_shell: ssh_pubkey parameter required")
	}
	// Tight validation: single-line, starts with ssh-ed25519/ssh-rsa/
	// ecdsa-sha2-*, no shell metacharacters.
	if strings.ContainsAny(pubkey, "\n\r;|&$`") {
		return nil, errors.New("enable_recovery_shell: ssh_pubkey contains disallowed characters")
	}
	prefixOK := false
	for _, p := range []string{"ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-"} {
		if strings.HasPrefix(pubkey, p) {
			prefixOK = true
			break
		}
	}
	if !prefixOK {
		return nil, errors.New("enable_recovery_shell: ssh_pubkey must start with ssh-ed25519 / ssh-rsa / ecdsa-sha2-*")
	}

	durationHours := 4
	if raw, ok := params["duration_hours"].(string); ok && raw != "" {
		if v, err := strconv.Atoi(raw); err == nil {
			if v >= 1 && v <= 24 {
				durationHours = v
			}
		}
	} else if raw, ok := params["duration_hours"].(float64); ok {
		v := int(raw)
		if v >= 1 && v <= 24 {
			durationHours = v
		}
	}

	keyFile := "/etc/msp-recovery-authorized-keys"
	tmp := keyFile + ".tmp"
	if err := os.WriteFile(tmp, []byte(pubkey+"\n"), 0o600); err != nil {
		return nil, fmt.Errorf("write authorized_keys: %w", err)
	}
	if err := os.Rename(tmp, keyFile); err != nil {
		os.Remove(tmp)
		return nil, fmt.Errorf("install authorized_keys: %w", err)
	}

	// `systemctl start sshd` — unit must be present in closure.
	startOut, startErr := exec.CommandContext(ctx, "systemctl", "start", "sshd").CombinedOutput()
	if startErr != nil {
		os.Remove(keyFile)
		return nil, fmt.Errorf("systemctl start sshd: %w: %s", startErr, string(startOut))
	}

	// Arm the systemd-run transient timer. After duration_hours:
	//   - systemctl stop sshd
	//   - rm /etc/msp-recovery-authorized-keys
	// If the watchdog itself dies, the timer keeps ticking (systemd
	// owns it). Recovery shell closes on schedule regardless.
	unitSuffix := fmt.Sprintf("%d", time.Now().UnixMilli())
	script := fmt.Sprintf(
		"systemctl stop sshd; rm -f %s; echo msp-recovery-expired",
		keyFile,
	)
	bash := "/run/current-system/sw/bin/bash"
	if _, err := os.Stat(bash); err != nil {
		bash = "/bin/bash"
	}
	timerCmd := exec.CommandContext(
		ctx,
		"systemd-run",
		"--unit=msp-recovery-shell-expire-"+unitSuffix,
		"--on-active="+strconv.Itoa(durationHours)+"h",
		"--timer-property=AccuracySec=1min",
		"--setenv=PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin",
		bash, "-c", script,
	)
	timerOut, timerErr := timerCmd.CombinedOutput()
	if timerErr != nil {
		// Best-effort: try to tear down so we don't leave sshd running
		// indefinitely on a failed timer arm.
		_ = exec.CommandContext(ctx, "systemctl", "stop", "sshd").Run()
		_ = os.Remove(keyFile)
		return nil, fmt.Errorf("arm expire timer: %w: %s", timerErr, string(timerOut))
	}

	expireAt := time.Now().Add(time.Duration(durationHours) * time.Hour).UTC().Format(time.RFC3339)
	w.log.Warn("RECOVERY SHELL ENABLED",
		"duration_hours", durationHours,
		"expire_at", expireAt,
		"timer_unit", "msp-recovery-shell-expire-"+unitSuffix,
	)
	return map[string]any{
		"expire_at":          expireAt,
		"duration_hours":     durationHours,
		"systemd_timer_unit": "msp-recovery-shell-expire-" + unitSuffix,
		"authorized_keys":    keyFile,
	}, nil
}

// ── Utilities ────────────────────────────────────────────────────────

// runCmd wraps exec.CommandContext with a 10-second per-call timeout.
// The 10s cap is critical: without it, a hung journalctl / host /
// systemctl on a broken-network box will pin the watchdog's tick
// forever — the very failure mode the watchdog exists to surface.
func runCmd(parent context.Context, name string, args ...string) string {
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, args...).CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		return fmt.Sprintf("ERR: %s timed out after 10s\n%s", name, string(out))
	}
	if err != nil {
		return fmt.Sprintf("ERR: %v\n%s", err, string(out))
	}
	return string(out)
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func sha256File(parent context.Context, path string) string {
	// Cheap hash via external sha256sum to avoid pulling crypto package
	// into this already-minimal binary. Fails soft — return empty.
	// Context-bounded so a stuck disk read can't wedge the tick.
	ctx, cancel := context.WithTimeout(parent, 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "sha256sum", path).Output()
	if err != nil {
		return ""
	}
	if len(out) < 12 {
		return ""
	}
	return string(out[:12])
}

func keys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// ConfigDir is exported so the systemd unit (Phase W2) can place the
// config file in a predictable location.
func ConfigDir() string { return filepath.Dir(defaultConfigPath) }
