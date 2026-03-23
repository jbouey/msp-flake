package daemon

import (
	"context"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

// ProbeResult holds the outcome of probing a single IP address.
type ProbeResult struct {
	IP            string
	SSHBanner     string
	SSHOpen       bool
	WinRMOpen     bool
	KerberosOpen  bool
	HTTPBanner    string
	OSType        string // "linux", "windows", "macos", "unknown"
	Distro        string // "ubuntu", "debian", "rhel", "centos", or ""
	OSFingerprint string
}

// grabSSHBanner connects to port 22 on the given IP and reads the SSH banner.
// Returns the banner string and whether the port was open.
// If the port is open but no data is returned, it returns ("", true).
func grabSSHBanner(ctx context.Context, ip string, port int) (string, bool) {
	dialer := net.Dialer{Timeout: 3 * time.Second}
	addr := fmt.Sprintf("%s:%d", ip, port)
	conn, err := dialer.DialContext(ctx, "tcp", addr)
	if err != nil {
		return "", false
	}
	defer conn.Close()

	// Set a read deadline so we don't block indefinitely.
	_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))

	buf := make([]byte, 256)
	n, err := conn.Read(buf)
	if err != nil || n == 0 {
		// Port open but no data (or read error after connect)
		return "", true
	}

	banner := strings.TrimSpace(string(buf[:n]))
	return banner, true
}

// checkPort connects to the given port on the given IP.
// Returns true if the port accepts a TCP connection.
func checkPort(ctx context.Context, ip string, port int) bool {
	if ctx == nil {
		ctx = context.Background()
	}
	dialer := net.Dialer{Timeout: 3 * time.Second}
	addr := fmt.Sprintf("%s:%d", ip, port)
	conn, err := dialer.DialContext(ctx, "tcp", addr)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// checkWinRM connects to port 5985 on the given IP.
// Returns true if the port accepts a TCP connection.
func checkWinRM(ctx context.Context, ip string) bool {
	return checkPort(ctx, ip, 5985)
}

// parseSSHBanner parses an SSH banner string and returns (osType, distro).
// Classification priority:
//  1. "apple"               → macos
//  2. "openssh_for_windows" → windows
//  3. "ubuntu"              → linux/ubuntu
//  4. "debian"              → linux/debian
//  5. "red hat"             → linux/rhel
//  6. "centos"              → linux/centos
//  7. "openssh" (fallback)  → linux (generic)
//  8. default               → unknown
func parseSSHBanner(banner string) (osType, distro string) {
	if banner == "" {
		return "unknown", ""
	}

	lower := strings.ToLower(banner)

	switch {
	case strings.Contains(lower, "apple"):
		return "macos", ""
	case strings.Contains(lower, "openssh_for_windows"):
		return "windows", ""
	case strings.Contains(lower, "ubuntu"):
		return "linux", "ubuntu"
	case strings.Contains(lower, "debian"):
		return "linux", "debian"
	case strings.Contains(lower, "red hat"):
		return "linux", "rhel"
	case strings.Contains(lower, "centos"):
		return "linux", "centos"
	case strings.Contains(lower, "openssh"):
		return "linux", ""
	default:
		return "unknown", ""
	}
}

// classifyFromProbes fills in OSType and Distro on the ProbeResult using
// probe data. Priority: SSH banner > WinRM open > SSH open (no banner) > unknown.
func classifyFromProbes(r *ProbeResult) {
	if r.SSHOpen && r.SSHBanner != "" {
		osType, distro := parseSSHBanner(r.SSHBanner)
		r.OSType = osType
		r.Distro = distro
		r.OSFingerprint = r.SSHBanner
		return
	}

	if r.WinRMOpen {
		r.OSType = "windows"
		r.Distro = ""
		return
	}

	if r.SSHOpen {
		r.OSType = "linux"
		r.Distro = ""
		return
	}

	r.OSType = "unknown"
	r.Distro = ""
}

// probeHost probes a single IP address for SSH, WinRM, and Kerberos, then classifies OS.
func probeHost(ctx context.Context, ip string) ProbeResult {
	result := ProbeResult{IP: ip}

	banner, sshOpen := grabSSHBanner(ctx, ip, 22)
	result.SSHOpen = sshOpen
	result.SSHBanner = banner

	result.WinRMOpen = checkWinRM(ctx, ip)
	result.KerberosOpen = checkPort(ctx, ip, 88)

	classifyFromProbes(&result)

	return result
}

// probeHosts probes a list of IP addresses concurrently (max 10 goroutines).
func probeHosts(ctx context.Context, ips []string) []ProbeResult {
	results := make([]ProbeResult, len(ips))

	sem := make(chan struct{}, 10)
	var wg sync.WaitGroup

	for i, ip := range ips {
		wg.Add(1)
		go func(idx int, addr string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			results[idx] = probeHost(ctx, addr)
		}(i, ip)
	}

	wg.Wait()
	return results
}
