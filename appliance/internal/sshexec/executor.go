// Package sshexec implements an SSH executor for running bash scripts
// on Linux targets. Handles key/password auth, sudo, session caching,
// distro detection, TOFU host key verification, and retry with exponential backoff.
package sshexec

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/ssh"
)

// Target describes a Linux machine to execute scripts on.
type Target struct {
	Hostname       string  `json:"hostname"`
	Port           int     `json:"port"`
	Username       string  `json:"username"`
	Password       *string `json:"password,omitempty"`
	PrivateKey     *string `json:"private_key,omitempty"`      // PEM-encoded key content
	PrivateKeyPath *string `json:"private_key_path,omitempty"` // Path to key file
	SudoPassword   *string `json:"sudo_password,omitempty"`
	Distro         string  `json:"distro,omitempty"` // Detected at runtime
	ConnectTimeout int     `json:"connect_timeout"`
	CommandTimeout int     `json:"command_timeout"`
}

// ExecutionResult is the result of a script execution.
type ExecutionResult struct {
	Success       bool                   `json:"success"`
	RunbookID     string                 `json:"runbook_id"`
	Target        string                 `json:"target"`
	Phase         string                 `json:"phase"`
	Output        map[string]interface{} `json:"output"`
	DurationSecs  float64                `json:"duration_seconds"`
	Error         string                 `json:"error,omitempty"`
	Timestamp     string                 `json:"timestamp"`
	OutputHash    string                 `json:"output_hash"`
	RetryCount    int                    `json:"retry_count"`
	HIPAAControls []string               `json:"hipaa_controls,omitempty"`
	Distro        string                 `json:"distro"`
	ExitCode      int                    `json:"exit_code"`
}

// cachedConn holds an SSH client with its creation time.
type cachedConn struct {
	client    *ssh.Client
	createdAt time.Time
}

// distroCacheEntry holds a cached distro detection result with TTL.
type distroCacheEntry struct {
	distro   string
	cachedAt time.Time
}

const (
	connMaxAge      = 300 * time.Second
	defaultTimeout  = 60 // seconds
	maxCachedConns  = 50 // LRU eviction threshold
	distroTTL       = 24 * time.Hour
)

// knownHostsPath is where TOFU-persisted host keys are stored.
const knownHostsPath = "/var/lib/msp/ssh_known_hosts"

// Executor manages SSH connections and script execution.
type Executor struct {
	conns       map[string]*cachedConn
	connOrder   []string                      // LRU order: oldest first
	distroCache map[string]*distroCacheEntry
	hostKeys    map[string]ssh.PublicKey       // in-memory TOFU cache
	mu          sync.Mutex
}

// NewExecutor creates a new SSH executor. Loads persisted host keys from disk.
func NewExecutor() *Executor {
	e := &Executor{
		conns:       make(map[string]*cachedConn),
		distroCache: make(map[string]*distroCacheEntry),
		hostKeys:    make(map[string]ssh.PublicKey),
	}
	e.loadKnownHosts()
	return e
}

// Execute runs a bash script on a Linux target with retry support.
func (e *Executor) Execute(ctx context.Context, target *Target, script, runbookID, phase string, timeout int, retries int, retryDelay float64, useSudo bool, hipaaControls []string) *ExecutionResult {
	if timeout <= 0 {
		timeout = defaultTimeout
	}
	if retryDelay <= 0 {
		retryDelay = 5.0
	}

	start := time.Now().UTC()
	var lastErr string
	retryCount := 0

	for attempt := 0; attempt <= retries; attempt++ {
		if attempt > 0 {
			delay := time.Duration(retryDelay*float64(attempt)) * time.Second
			log.Printf("[ssh] Retry %d/%d for %s after %.0fs delay", attempt, retries, target.Hostname, delay.Seconds())

			select {
			case <-ctx.Done():
				return failResult(runbookID, target.Hostname, phase, "context cancelled", start, retryCount, hipaaControls, target.Distro)
			case <-time.After(delay):
			}
			retryCount++
		}

		output, exitCode, err := e.executeOnce(ctx, target, script, timeout, useSudo)
		if err != nil {
			lastErr = err.Error()
			log.Printf("[ssh] Execution failed on %s: %v", target.Hostname, err)

			// Don't retry auth failures
			if isAuthError(err) {
				e.InvalidateConnection(target.Hostname)
				break
			}
			e.InvalidateConnection(target.Hostname)
			continue
		}

		elapsed := time.Since(start).Seconds()
		errMsg := ""
		if exitCode != 0 {
			// Script ran but returned non-zero — extract stdout/stderr from output map.
			if stdout, ok := output["stdout"].(string); ok && stdout != "" {
				errMsg = stdout
			} else if stderr, ok := output["stderr"].(string); ok && stderr != "" {
				errMsg = stderr
			}
			if len(errMsg) > 500 {
				errMsg = errMsg[len(errMsg)-500:]
			}
			if errMsg == "" {
				errMsg = fmt.Sprintf("exit code %d (no output)", exitCode)
			}
		}
		return &ExecutionResult{
			Success:       exitCode == 0,
			RunbookID:     runbookID,
			Target:        target.Hostname,
			Phase:         phase,
			Output:        output,
			Error:         errMsg,
			DurationSecs:  elapsed,
			Timestamp:     start.Format(time.RFC3339),
			OutputHash:    hashOutput(output),
			RetryCount:    retryCount,
			HIPAAControls: hipaaControls,
			Distro:        target.Distro,
			ExitCode:      exitCode,
		}
	}

	return failResult(runbookID, target.Hostname, phase, lastErr, start, retryCount, hipaaControls, target.Distro)
}

// executeOnce runs a script via SSH, using base64 encoding to avoid shell quoting issues.
func (e *Executor) executeOnce(ctx context.Context, target *Target, script string, timeout int, useSudo bool) (map[string]interface{}, int, error) {
	client, err := e.getConnection(ctx, target)
	if err != nil {
		return nil, -1, fmt.Errorf("get connection: %w", err)
	}

	session, err := client.NewSession()
	if err != nil {
		return nil, -1, fmt.Errorf("new session: %w", err)
	}
	defer session.Close()

	// Base64 encode to avoid shell quoting issues
	encoded := base64.StdEncoding.EncodeToString([]byte(script))

	var cmd string
	if useSudo && target.Username != "root" {
		// Warn if sudo is needed but no password is available — this will fail
		// on hosts that require password-based sudo (most non-root users).
		if target.SudoPassword == nil || *target.SudoPassword == "" {
			log.Printf("[ssh] WARNING: sudo requested for %s@%s but no SudoPassword set — passwordless sudo required", target.Username, target.Hostname)
		}
		// Use `sudo env` to pass environment vars through sudo barrier.
		// SYSTEMD_PAGER= prevents systemctl from paging output.
		// DEBIAN_FRONTEND=noninteractive prevents apt/dpkg prompts.
		// Without these, systemctl on Ubuntu 24.04+ triggers polkit
		// "Interactive authentication required" even when running as root.
		if target.SudoPassword != nil && *target.SudoPassword != "" {
			escapedPass := shellEscapeSingleQuote(*target.SudoPassword)
			cmd = fmt.Sprintf(`echo '%s' | sudo -S env SYSTEMD_PAGER= DEBIAN_FRONTEND=noninteractive bash -c "$(echo %s | base64 -d)"`, escapedPass, encoded)
		} else {
			cmd = fmt.Sprintf(`sudo env SYSTEMD_PAGER= DEBIAN_FRONTEND=noninteractive bash -c "$(echo %s | base64 -d)"`, encoded)
		}
	} else {
		cmd = fmt.Sprintf(`bash -c "$(echo %s | base64 -d)"`, encoded)
	}

	// Set up output capture
	var stdout, stderr strings.Builder
	session.Stdout = &stdout
	session.Stderr = &stderr

	// Run with timeout
	done := make(chan error, 1)
	go func() {
		done <- session.Run(cmd)
	}()

	timeoutDur := time.Duration(timeout) * time.Second
	select {
	case <-ctx.Done():
		session.Close() // Kill orphaned goroutine
		return nil, -1, fmt.Errorf("context cancelled")
	case <-time.After(timeoutDur):
		session.Close() // Kill orphaned goroutine
		return nil, -1, fmt.Errorf("execution timed out after %ds", timeout)
	case err := <-done:
		exitCode := 0
		if err != nil {
			if exitErr, ok := err.(*ssh.ExitError); ok {
				exitCode = exitErr.ExitStatus()
			} else {
				return nil, -1, fmt.Errorf("run: %w", err)
			}
		}

		output := map[string]interface{}{
			"stdout":    strings.TrimSpace(stdout.String()),
			"stderr":    strings.TrimSpace(stderr.String()),
			"exit_code": exitCode,
			"success":   exitCode == 0,
		}

		// Try to parse JSON output
		stdoutStr := strings.TrimSpace(stdout.String())
		if stdoutStr != "" {
			var parsed interface{}
			if json.Unmarshal([]byte(stdoutStr), &parsed) == nil {
				output["parsed"] = parsed
			}
		}

		return output, exitCode, nil
	}
}

// DetectDistro detects the Linux distribution on a target.
func (e *Executor) DetectDistro(ctx context.Context, target *Target) (string, error) {
	e.mu.Lock()
	if entry, ok := e.distroCache[target.Hostname]; ok && time.Since(entry.cachedAt) < distroTTL {
		e.mu.Unlock()
		return entry.distro, nil
	}
	e.mu.Unlock()

	script := `if [ -f /etc/os-release ]; then . /etc/os-release; echo "$ID"; elif [ -f /etc/redhat-release ]; then echo "rhel"; elif [ -f /etc/debian_version ]; then echo "debian"; else echo "unknown"; fi`

	output, exitCode, err := e.executeOnce(ctx, target, script, 10, false)
	if err != nil || exitCode != 0 {
		return "unknown", err
	}

	stdoutVal, ok := output["stdout"].(string)
	if !ok {
		return "unknown", nil
	}
	distro := strings.TrimSpace(stdoutVal)
	if distro == "" {
		distro = "unknown"
	}

	e.mu.Lock()
	e.distroCache[target.Hostname] = &distroCacheEntry{distro: distro, cachedAt: time.Now()}
	e.mu.Unlock()

	return distro, nil
}

// getConnection returns a cached or new SSH connection.
// The context is used for TCP dial cancellation and SSH handshake deadline.
//
// IMPORTANT: The mutex is released before TCP dial and SSH handshake so that
// tofuHostKeyCallback (called during handshake) can acquire e.mu without
// deadlocking. Network I/O must never run under e.mu.
func (e *Executor) getConnection(ctx context.Context, target *Target) (*ssh.Client, error) {
	// --- Phase 1: check cache (under lock) ---
	e.mu.Lock()
	if cached, ok := e.conns[target.Hostname]; ok {
		if time.Since(cached.createdAt) < connMaxAge {
			// Quick check: try to open a session to verify connection is alive.
			// Close it immediately to avoid leaking SSH sessions.
			// Use a goroutine + timer because NewSession() on a half-open TCP
			// connection blocks indefinitely (no timeout or context support in
			// crypto/ssh). Without this guard, one dead connection blocks ALL
			// SSH operations via the executor mutex.
			type sessResult struct {
				sess *ssh.Session
				err  error
			}
			ch := make(chan sessResult, 1)
			go func() {
				s, err := cached.client.NewSession()
				ch <- sessResult{s, err}
			}()
			select {
			case r := <-ch:
				if r.err == nil {
					r.sess.Close()
					e.lruTouch(target.Hostname)
					e.mu.Unlock()
					return cached.client, nil
				}
				log.Printf("[ssh] Stale connection to %s (session error: %v), reconnecting", target.Hostname, r.err)
			case <-time.After(5 * time.Second):
				log.Printf("[ssh] Connection health check timed out for %s, reconnecting", target.Hostname)
			case <-ctx.Done():
				e.mu.Unlock()
				return nil, fmt.Errorf("context cancelled while checking connection to %s", target.Hostname)
			}
		}
		_ = cached.client.Close()
		delete(e.conns, target.Hostname)
		e.lruRemove(target.Hostname)
	}
	e.mu.Unlock()

	// --- Phase 2: build config, dial, handshake (NO lock held) ---
	// tofuHostKeyCallback acquires e.mu during handshake, so we must not
	// hold it here. Context cancellation can interrupt DialContext and the
	// TCP deadline guards the handshake.

	config, err := e.buildSSHConfig(target)
	if err != nil {
		return nil, err
	}

	port := target.Port
	if port == 0 {
		port = 22
	}

	connectTimeout := time.Duration(target.ConnectTimeout) * time.Second
	if connectTimeout == 0 {
		connectTimeout = 30 * time.Second
	}

	addr := net.JoinHostPort(target.Hostname, fmt.Sprintf("%d", port))
	dialer := net.Dialer{Timeout: connectTimeout}
	conn, err := dialer.DialContext(ctx, "tcp", addr)
	if err != nil {
		return nil, fmt.Errorf("dial %s: %w", addr, err)
	}

	// Set deadline on TCP conn so SSH handshake cannot hang forever.
	// The handshake (key exchange, auth) must complete within connectTimeout.
	if err := conn.SetDeadline(time.Now().Add(connectTimeout)); err != nil {
		conn.Close()
		return nil, fmt.Errorf("set deadline %s: %w", addr, err)
	}

	sshConn, chans, reqs, err := ssh.NewClientConn(conn, addr, config)
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("SSH handshake %s: %w", addr, err)
	}

	// Clear the deadline after successful handshake — sessions manage their own timeouts.
	if err := conn.SetDeadline(time.Time{}); err != nil {
		log.Printf("[ssh] Warning: failed to clear deadline on %s: %v", addr, err)
	}

	client := ssh.NewClient(sshConn, chans, reqs)

	// --- Phase 3: store in cache (under lock) ---
	e.mu.Lock()
	defer e.mu.Unlock()

	// LRU eviction: if at capacity, close oldest connection
	if len(e.conns) >= maxCachedConns && len(e.connOrder) > 0 {
		evictHost := e.connOrder[0]
		e.connOrder = e.connOrder[1:]
		if old, ok := e.conns[evictHost]; ok {
			_ = old.client.Close()
			delete(e.conns, evictHost)
			log.Printf("[ssh] LRU evicted connection for %s (cache full at %d)", evictHost, maxCachedConns)
		}
	}

	e.conns[target.Hostname] = &cachedConn{
		client:    client,
		createdAt: time.Now(),
	}
	// Add to LRU order (remove first if already exists, then append)
	e.lruTouch(target.Hostname)

	log.Printf("[ssh] New connection to %s:%d as %s", target.Hostname, port, target.Username)
	return client, nil
}

// lruTouch moves a hostname to the back of the LRU order (most recently used).
// Must be called with e.mu held.
func (e *Executor) lruTouch(hostname string) {
	e.lruRemove(hostname)
	e.connOrder = append(e.connOrder, hostname)
}

// lruRemove removes a hostname from the LRU order.
// Must be called with e.mu held.
func (e *Executor) lruRemove(hostname string) {
	for i, h := range e.connOrder {
		if h == hostname {
			e.connOrder = append(e.connOrder[:i], e.connOrder[i+1:]...)
			return
		}
	}
}

// InvalidateConnection removes a cached connection for a host.
func (e *Executor) InvalidateConnection(hostname string) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if cached, ok := e.conns[hostname]; ok {
		_ = cached.client.Close()
		delete(e.conns, hostname)
		e.lruRemove(hostname)
	}
	log.Printf("[ssh] Invalidated connection for %s", hostname)
}

// ConnectionCount returns the number of cached connections.
func (e *Executor) ConnectionCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.conns)
}

// CloseAll closes all cached connections.
func (e *Executor) CloseAll() {
	e.mu.Lock()
	defer e.mu.Unlock()

	for host, cached := range e.conns {
		_ = cached.client.Close()
		delete(e.conns, host)
	}
	e.connOrder = nil
}

// --- Helpers ---

// shellEscapeSingleQuote escapes a string for safe use inside single quotes
// in POSIX shell. The standard technique ends the current single-quoted string,
// inserts an escaped literal single quote, then re-opens the single-quoted string.
func shellEscapeSingleQuote(s string) string {
	return strings.ReplaceAll(s, "'", "'\\''")
}

func (e *Executor) buildSSHConfig(target *Target) (*ssh.ClientConfig, error) {
	username := target.Username
	if username == "" {
		username = "root"
	}

	config := &ssh.ClientConfig{
		User:            username,
		HostKeyCallback: e.tofuHostKeyCallback,
		Timeout:         30 * time.Second,
	}

	// Try key auth first, then password
	switch {
	case target.PrivateKey != nil && *target.PrivateKey != "":
		signer, err := ssh.ParsePrivateKey([]byte(*target.PrivateKey))
		if err != nil {
			return nil, fmt.Errorf("parse private key: %w", err)
		}
		config.Auth = []ssh.AuthMethod{ssh.PublicKeys(signer)}
	case target.Password != nil && *target.Password != "":
		config.Auth = []ssh.AuthMethod{ssh.Password(*target.Password)}
	default:
		return nil, fmt.Errorf("no auth method for %s (need key or password)", target.Hostname)
	}

	return config, nil
}

// tofuHostKeyCallback implements Trust On First Use: accept and persist new
// host keys, reject changed keys (potential MITM).
func (e *Executor) tofuHostKeyCallback(hostname string, remote net.Addr, key ssh.PublicKey) error {
	// Normalize hostname (strip port if present)
	host, _, err := net.SplitHostPort(hostname)
	if err != nil {
		host = hostname
	}

	e.mu.Lock()
	defer e.mu.Unlock()

	existing, known := e.hostKeys[host]
	if !known {
		// First contact — trust and persist
		e.hostKeys[host] = key
		log.Printf("[ssh] TOFU: accepted new host key for %s (%s)", host, key.Type())
		e.saveKnownHosts()
		return nil
	}

	// Key is known — verify it matches
	if bytes.Equal(existing.Marshal(), key.Marshal()) {
		return nil
	}

	log.Printf("[ssh] SECURITY: host key CHANGED for %s (was %s, now %s) — possible MITM attack",
		host, existing.Type(), key.Type())
	return fmt.Errorf("host key mismatch for %s: expected %s, got %s (remove from %s to accept new key)",
		host, ssh.FingerprintSHA256(existing), ssh.FingerprintSHA256(key), knownHostsPath)
}

// loadKnownHosts reads persisted host keys from disk.
// Format: one line per host: "hostname key-type base64-key"
func (e *Executor) loadKnownHosts() {
	f, err := os.Open(knownHostsPath)
	if err != nil {
		return // File doesn't exist yet — normal on first run
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	loaded := 0
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, " ", 3)
		if len(parts) != 3 {
			continue
		}
		host := parts[0]
		keyBytes, err := base64.StdEncoding.DecodeString(parts[2])
		if err != nil {
			log.Printf("[ssh] TOFU: bad base64 for %s in known_hosts, skipping", host)
			continue
		}
		pubKey, err := ssh.ParsePublicKey(keyBytes)
		if err != nil {
			log.Printf("[ssh] TOFU: bad key for %s in known_hosts, skipping", host)
			continue
		}
		e.hostKeys[host] = pubKey
		loaded++
	}
	if loaded > 0 {
		log.Printf("[ssh] TOFU: loaded %d known host keys from %s", loaded, knownHostsPath)
	}
}

// saveKnownHosts persists all known host keys to disk.
func (e *Executor) saveKnownHosts() {
	dir := filepath.Dir(knownHostsPath)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		log.Printf("[ssh] TOFU: cannot create dir %s: %v", dir, err)
		return
	}

	var buf strings.Builder
	buf.WriteString("# SSH known hosts (TOFU — managed by appliance daemon)\n")
	for host, key := range e.hostKeys {
		keyBytes := key.Marshal()
		fmt.Fprintf(&buf, "%s %s %s\n", host, key.Type(), base64.StdEncoding.EncodeToString(keyBytes))
	}

	if err := os.WriteFile(knownHostsPath, []byte(buf.String()), 0o600); err != nil {
		log.Printf("[ssh] TOFU: failed to save known_hosts: %v", err)
	}
}

func isAuthError(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return strings.Contains(msg, "unable to authenticate") ||
		strings.Contains(msg, "permission denied") ||
		strings.Contains(msg, "no supported methods remain")
}

func hashOutput(output map[string]interface{}) string {
	data, _ := json.Marshal(output)
	hash := sha256.Sum256(data)
	return fmt.Sprintf("%x", hash)[:16]
}

func failResult(runbookID, hostname, phase, errMsg string, start time.Time, retryCount int, hipaaControls []string, distro string) *ExecutionResult {
	return &ExecutionResult{
		Success:       false,
		RunbookID:     runbookID,
		Target:        hostname,
		Phase:         phase,
		Output:        map[string]interface{}{"success": false, "stdout": "", "stderr": errMsg, "exit_code": -1},
		DurationSecs:  time.Since(start).Seconds(),
		Error:         errMsg,
		Timestamp:     start.Format(time.RFC3339),
		RetryCount:    retryCount,
		HIPAAControls: hipaaControls,
		Distro:        distro,
		ExitCode:      -1,
	}
}
