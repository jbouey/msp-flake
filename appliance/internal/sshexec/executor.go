// Package sshexec implements an SSH executor for running bash scripts
// on Linux targets. Handles key/password auth, sudo, session caching,
// distro detection, and retry with exponential backoff.
package sshexec

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net"
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

const (
	connMaxAge     = 300 * time.Second
	defaultTimeout = 60 // seconds
)

// Executor manages SSH connections and script execution.
type Executor struct {
	conns      map[string]*cachedConn
	distroCache map[string]string
	mu         sync.Mutex
}

// NewExecutor creates a new SSH executor.
func NewExecutor() *Executor {
	return &Executor{
		conns:       make(map[string]*cachedConn),
		distroCache: make(map[string]string),
	}
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
		return &ExecutionResult{
			Success:       exitCode == 0,
			RunbookID:     runbookID,
			Target:        target.Hostname,
			Phase:         phase,
			Output:        output,
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
	client, err := e.getConnection(target)
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
		if target.SudoPassword != nil && *target.SudoPassword != "" {
			cmd = fmt.Sprintf(`echo '%s' | sudo -S bash -c "$(echo %s | base64 -d)"`, *target.SudoPassword, encoded)
		} else {
			cmd = fmt.Sprintf(`sudo bash -c "$(echo %s | base64 -d)"`, encoded)
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
		return nil, -1, fmt.Errorf("context cancelled")
	case <-time.After(timeoutDur):
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
	if cached, ok := e.distroCache[target.Hostname]; ok {
		e.mu.Unlock()
		return cached, nil
	}
	e.mu.Unlock()

	script := `if [ -f /etc/os-release ]; then . /etc/os-release; echo "$ID"; elif [ -f /etc/redhat-release ]; then echo "rhel"; elif [ -f /etc/debian_version ]; then echo "debian"; else echo "unknown"; fi`

	output, exitCode, err := e.executeOnce(ctx, target, script, 10, false)
	if err != nil || exitCode != 0 {
		return "unknown", err
	}

	distro := strings.TrimSpace(output["stdout"].(string))
	if distro == "" {
		distro = "unknown"
	}

	e.mu.Lock()
	e.distroCache[target.Hostname] = distro
	e.mu.Unlock()

	return distro, nil
}

// getConnection returns a cached or new SSH connection.
func (e *Executor) getConnection(target *Target) (*ssh.Client, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if cached, ok := e.conns[target.Hostname]; ok {
		if time.Since(cached.createdAt) < connMaxAge {
			// Quick check: try to open a session to verify connection
			_, err := cached.client.NewSession()
			if err == nil {
				return cached.client, nil
			}
			log.Printf("[ssh] Stale connection to %s, reconnecting", target.Hostname)
		}
		cached.client.Close()
		delete(e.conns, target.Hostname)
	}

	config, err := buildSSHConfig(target)
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
	conn, err := net.DialTimeout("tcp", addr, connectTimeout)
	if err != nil {
		return nil, fmt.Errorf("dial %s: %w", addr, err)
	}

	sshConn, chans, reqs, err := ssh.NewClientConn(conn, addr, config)
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("SSH handshake %s: %w", addr, err)
	}

	client := ssh.NewClient(sshConn, chans, reqs)

	e.conns[target.Hostname] = &cachedConn{
		client:    client,
		createdAt: time.Now(),
	}

	log.Printf("[ssh] New connection to %s:%d as %s", target.Hostname, port, target.Username)
	return client, nil
}

// InvalidateConnection removes a cached connection for a host.
func (e *Executor) InvalidateConnection(hostname string) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if cached, ok := e.conns[hostname]; ok {
		cached.client.Close()
		delete(e.conns, hostname)
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
		cached.client.Close()
		delete(e.conns, host)
	}
}

// --- Helpers ---

func buildSSHConfig(target *Target) (*ssh.ClientConfig, error) {
	username := target.Username
	if username == "" {
		username = "root"
	}

	config := &ssh.ClientConfig{
		User:            username,
		HostKeyCallback: ssh.InsecureIgnoreHostKey(),
		Timeout:         30 * time.Second,
	}

	// Try key auth first, then password
	if target.PrivateKey != nil && *target.PrivateKey != "" {
		signer, err := ssh.ParsePrivateKey([]byte(*target.PrivateKey))
		if err != nil {
			return nil, fmt.Errorf("parse private key: %w", err)
		}
		config.Auth = []ssh.AuthMethod{ssh.PublicKeys(signer)}
	} else if target.Password != nil && *target.Password != "" {
		config.Auth = []ssh.AuthMethod{ssh.Password(*target.Password)}
	} else {
		return nil, fmt.Errorf("no auth method for %s (need key or password)", target.Hostname)
	}

	return config, nil
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
