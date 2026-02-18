// Package winrm implements a WinRM executor for running PowerShell scripts
// on Windows targets. It handles session caching, the cmd.exe 8191 character
// limit via temp file chunking, NTLM auth, and retry with exponential backoff.
package winrm

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"strings"
	"sync"
	"time"

	gowinrm "github.com/masterzen/winrm"
)

// Target describes a Windows machine to execute scripts on.
type Target struct {
	Hostname  string `json:"hostname"`
	Port      int    `json:"port"`
	Username  string `json:"username"` // DOMAIN\user format
	Password  string `json:"password"`
	UseSSL    bool   `json:"use_ssl"`
	VerifySSL bool   `json:"verify_ssl"`
	IPAddress string `json:"ip_address,omitempty"`
}

// ExecutionResult is the result of a script execution.
type ExecutionResult struct {
	Success        bool                   `json:"success"`
	RunbookID      string                 `json:"runbook_id"`
	Target         string                 `json:"target"`
	Phase          string                 `json:"phase"`
	Output         map[string]interface{} `json:"output"`
	DurationSecs   float64                `json:"duration_seconds"`
	Error          string                 `json:"error,omitempty"`
	Timestamp      string                 `json:"timestamp"`
	OutputHash     string                 `json:"output_hash"`
	RetryCount     int                    `json:"retry_count"`
	HIPAAControls  []string               `json:"hipaa_controls,omitempty"`
}

// cachedSession holds a WinRM client with its creation time.
type cachedSession struct {
	client    *gowinrm.Client
	createdAt time.Time
}

const (
	sessionMaxAge     = 300 * time.Second
	inlineScriptLimit = 2000 // Chars before switching to temp file mode
	chunkSize         = 6000 // Base64 chunk size for cmd.exe echo safety
	defaultTimeout    = 300  // seconds
)

// Executor manages WinRM sessions and script execution.
type Executor struct {
	sessions map[string]*cachedSession
	mu       sync.Mutex
}

// NewExecutor creates a new WinRM executor.
func NewExecutor() *Executor {
	return &Executor{
		sessions: make(map[string]*cachedSession),
	}
}

// Execute runs a PowerShell script on a Windows target with retry support.
func (e *Executor) Execute(target *Target, script, runbookID, phase string, timeout int, retries int, retryDelay float64, hipaaControls []string) *ExecutionResult {
	if timeout <= 0 {
		timeout = defaultTimeout
	}
	if retryDelay <= 0 {
		retryDelay = 30.0
	}

	start := time.Now().UTC()
	var lastErr string
	retryCount := 0

	for attempt := 0; attempt <= retries; attempt++ {
		if attempt > 0 {
			delay := time.Duration(retryDelay*float64(attempt)) * time.Second
			log.Printf("[winrm] Retry %d/%d for %s after %.0fs delay", attempt, retries, target.Hostname, delay.Seconds())
			time.Sleep(delay)
			retryCount++
		}

		output, err := e.executeOnce(target, script, timeout)
		if err != nil {
			lastErr = err.Error()
			log.Printf("[winrm] Execution failed on %s: %v", target.Hostname, err)
			e.InvalidateSession(target.Hostname)
			continue
		}

		elapsed := time.Since(start).Seconds()
		return &ExecutionResult{
			Success:       output["success"].(bool),
			RunbookID:     runbookID,
			Target:        target.Hostname,
			Phase:         phase,
			Output:        output,
			DurationSecs:  elapsed,
			Timestamp:     start.Format(time.RFC3339),
			OutputHash:    hashOutput(output),
			RetryCount:    retryCount,
			HIPAAControls: hipaaControls,
		}
	}

	// All retries exhausted
	elapsed := time.Since(start).Seconds()
	return &ExecutionResult{
		Success:       false,
		RunbookID:     runbookID,
		Target:        target.Hostname,
		Phase:         phase,
		Output:        map[string]interface{}{"success": false, "std_out": "", "std_err": lastErr},
		DurationSecs:  elapsed,
		Error:         lastErr,
		Timestamp:     start.Format(time.RFC3339),
		OutputHash:    "",
		RetryCount:    retryCount,
		HIPAAControls: hipaaControls,
	}
}

// executeOnce runs a script, choosing inline or temp file mode based on length.
func (e *Executor) executeOnce(target *Target, script string, timeout int) (map[string]interface{}, error) {
	client, err := e.getSession(target)
	if err != nil {
		return nil, fmt.Errorf("get session: %w", err)
	}

	var stdout, stderr string
	var exitCode int

	if len(script) > inlineScriptLimit {
		stdout, stderr, exitCode, err = e.executeViaTempFile(client, script, timeout)
	} else {
		stdout, stderr, exitCode, err = e.executeInline(client, script, timeout)
	}

	if err != nil {
		return nil, err
	}

	output := map[string]interface{}{
		"status_code": exitCode,
		"std_out":     stdout,
		"std_err":     stderr,
		"success":     exitCode == 0,
	}

	// Try to parse JSON output
	if stdout != "" {
		var parsed interface{}
		if json.Unmarshal([]byte(stdout), &parsed) == nil {
			output["parsed"] = parsed
		}
	}

	return output, nil
}

// executeInline runs a short PowerShell script directly.
func (e *Executor) executeInline(client *gowinrm.Client, script string, timeout int) (string, string, int, error) {
	shell, err := client.CreateShell()
	if err != nil {
		return "", "", -1, fmt.Errorf("create shell: %w", err)
	}
	defer shell.Close()

	// PowerShell base64-encoded command
	encoded := encodePowerShell(script)
	cmd, err := shell.Execute("powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded)
	if err != nil {
		return "", "", -1, fmt.Errorf("execute: %w", err)
	}
	defer cmd.Close()

	var stdoutBuf, stderrBuf bytes.Buffer
	go io.Copy(&stdoutBuf, cmd.Stdout)
	go io.Copy(&stderrBuf, cmd.Stderr)

	cmd.Wait()

	stdout := strings.TrimSpace(stdoutBuf.String())
	stderr := strings.TrimSpace(stderrBuf.String())

	return stdout, stderr, cmd.ExitCode(), nil
}

// executeViaTempFile handles the cmd.exe 8191 character limit by writing
// the script to a temp file via chunked base64 echo commands.
func (e *Executor) executeViaTempFile(client *gowinrm.Client, script string, timeout int) (string, string, int, error) {
	// Generate unique temp file names
	scriptHash := fmt.Sprintf("%x", sha256.Sum256([]byte(script)))[:8]
	tempB64 := fmt.Sprintf(`C:\Windows\Temp\msp_%s.b64`, scriptHash)
	tempPS1 := fmt.Sprintf(`C:\Windows\Temp\msp_%s.ps1`, scriptHash)

	// Base64 encode the script
	encoded := base64.StdEncoding.EncodeToString([]byte(script))

	// Split into chunks safe for cmd.exe
	chunks := splitString(encoded, chunkSize)

	shell, err := client.CreateShell()
	if err != nil {
		return "", "", -1, fmt.Errorf("create shell: %w", err)
	}
	defer shell.Close()

	// Write chunks to temp file
	for i, chunk := range chunks {
		op := ">"
		if i > 0 {
			op = ">>"
		}
		cmdStr := fmt.Sprintf(`echo %s%s"%s"`, chunk, op, tempB64)
		cmd, err := shell.Execute("cmd.exe", "/c", cmdStr)
		if err != nil {
			return "", "", -1, fmt.Errorf("write chunk %d: %w", i, err)
		}
		cmd.Wait()
		cmd.Close()
		if cmd.ExitCode() != 0 {
			return "", "", -1, fmt.Errorf("write chunk %d failed: exit %d", i, cmd.ExitCode())
		}
	}

	// Decode base64, write PS1, execute, cleanup
	decodeAndRun := fmt.Sprintf(
		`$r=(Get-Content '%s' -Raw) -replace '\s',''; `+
			`$b=[Convert]::FromBase64String($r); `+
			`[IO.File]::WriteAllText('%s',[Text.Encoding]::UTF8.GetString($b)); `+
			`Remove-Item '%s' -Force -EA SilentlyContinue; `+
			`try { & '%s' } finally { Remove-Item '%s' -Force -EA SilentlyContinue }`,
		tempB64, tempPS1, tempB64, tempPS1, tempPS1,
	)

	encodedCmd := encodePowerShell(decodeAndRun)
	cmd, err := shell.Execute("powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encodedCmd)
	if err != nil {
		return "", "", -1, fmt.Errorf("execute temp file: %w", err)
	}
	defer cmd.Close()

	var stdoutBuf, stderrBuf bytes.Buffer
	go io.Copy(&stdoutBuf, cmd.Stdout)
	go io.Copy(&stderrBuf, cmd.Stderr)

	cmd.Wait()

	stdout := strings.TrimSpace(stdoutBuf.String())
	stderr := strings.TrimSpace(stderrBuf.String())

	return stdout, stderr, cmd.ExitCode(), nil
}

// getSession returns a cached or new WinRM session.
func (e *Executor) getSession(target *Target) (*gowinrm.Client, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if cached, ok := e.sessions[target.Hostname]; ok {
		if time.Since(cached.createdAt) < sessionMaxAge {
			return cached.client, nil
		}
		log.Printf("[winrm] Session expired for %s, refreshing", target.Hostname)
	}

	port := target.Port
	if port == 0 {
		if target.UseSSL {
			port = 5986
		} else {
			port = 5985
		}
	}

	endpoint := gowinrm.NewEndpoint(target.Hostname, port, target.UseSSL, !target.VerifySSL, nil, nil, nil, 120*time.Second)

	// Use NTLM auth (required for domain environments; Basic is rarely enabled)
	params := gowinrm.NewParameters("PT120S", "en-US", 153600)
	params.TransportDecorator = func() gowinrm.Transporter { return &gowinrm.ClientNTLM{} }

	client, err := gowinrm.NewClientWithParameters(endpoint, target.Username, target.Password, params)
	if err != nil {
		return nil, fmt.Errorf("create WinRM client for %s: %w", target.Hostname, err)
	}

	e.sessions[target.Hostname] = &cachedSession{
		client:    client,
		createdAt: time.Now(),
	}

	log.Printf("[winrm] New session for %s:%d (ssl=%v)", target.Hostname, port, target.UseSSL)
	return client, nil
}

// InvalidateSession removes a cached session for a host.
func (e *Executor) InvalidateSession(hostname string) {
	e.mu.Lock()
	defer e.mu.Unlock()

	delete(e.sessions, hostname)
	log.Printf("[winrm] Invalidated session for %s", hostname)
}

// SessionCount returns the number of cached sessions.
func (e *Executor) SessionCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.sessions)
}

// --- Helpers ---

// encodePowerShell encodes a script for PowerShell's -EncodedCommand parameter.
// PowerShell expects UTF-16LE base64.
func encodePowerShell(script string) string {
	utf16 := make([]byte, len(script)*2)
	for i, c := range []byte(script) {
		utf16[i*2] = c
		utf16[i*2+1] = 0
	}
	return base64.StdEncoding.EncodeToString(utf16)
}

func splitString(s string, size int) []string {
	var chunks []string
	for len(s) > 0 {
		end := size
		if end > len(s) {
			end = len(s)
		}
		chunks = append(chunks, s[:end])
		s = s[end:]
	}
	return chunks
}

func hashOutput(output map[string]interface{}) string {
	data, _ := json.Marshal(output)
	hash := sha256.Sum256(data)
	return fmt.Sprintf("%x", hash)[:16]
}
