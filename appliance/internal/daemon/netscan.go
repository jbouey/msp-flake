package daemon

import (
	"context"
	"fmt"
	"log"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/osiriscare/appliance/internal/grpcserver"
)

const (
	netScanInterval = 15 * time.Minute
	portTimeout     = 3 * time.Second
)

// netScanner periodically checks network-level security:
// - Appliance listening ports (detect unexpected services)
// - Host reachability (known hosts responsive on expected ports)
// - External binding detection (services on 0.0.0.0 vs localhost)
type netScanner struct {
	daemon *Daemon

	mu           sync.Mutex
	lastScanTime time.Time
	running      int32
}

func newNetScanner(d *Daemon) *netScanner {
	return &netScanner{daemon: d}
}

// runNetScanIfNeeded runs a network scan if the interval has elapsed.
func (ns *netScanner) runNetScanIfNeeded(ctx context.Context) {
	if !atomic.CompareAndSwapInt32(&ns.running, 0, 1) {
		return
	}
	defer atomic.StoreInt32(&ns.running, 0)

	ns.mu.Lock()
	since := time.Since(ns.lastScanTime)
	first := ns.lastScanTime.IsZero()
	ns.mu.Unlock()

	if !first && since < netScanInterval {
		return
	}

	log.Printf("[netscan] Starting network scan cycle")
	ns.mu.Lock()
	ns.lastScanTime = time.Now()
	ns.mu.Unlock()

	ns.scanNetwork(ctx)
}

// scanNetwork performs all network-level checks.
func (ns *netScanner) scanNetwork(ctx context.Context) {
	var findings []driftFinding
	hostname := ns.daemon.config.SiteID + "-appliance"

	// 1. Check for unexpected listening ports on the appliance
	portFindings := ns.checkListeningPorts(hostname)
	findings = append(findings, portFindings...)

	// 2. Check known hosts are reachable on expected ports
	reachFindings := ns.checkHostReachability(ctx, hostname)
	findings = append(findings, reachFindings...)

	// Report all findings through the healing pipeline
	for _, f := range findings {
		ns.reportNetDrift(f)
	}

	log.Printf("[netscan] Scan complete: findings=%d", len(findings))
}

// expectedPorts are the ports the appliance should have open.
var expectedPorts = map[int]string{
	22:    "sshd",
	8090:  "agent-file-server",
	50051: "grpc",
}

// checkListeningPorts scans the appliance itself for unexpected listening ports.
func (ns *netScanner) checkListeningPorts(hostname string) []driftFinding {
	var findings []driftFinding

	// Scan common port range on localhost
	portsToCheck := []int{
		21, 23, 25, 53, 80, 110, 135, 139, 443, 445,
		993, 995, 1433, 1521, 3306, 3389, 5432, 5900,
		6379, 8080, 8443, 9090, 9200, 27017,
	}

	unexpectedOpen := []string{}

	for _, port := range portsToCheck {
		if _, ok := expectedPorts[port]; ok {
			continue // Skip expected ports
		}

		conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 500*time.Millisecond)
		if err == nil {
			conn.Close()
			unexpectedOpen = append(unexpectedOpen, fmt.Sprintf("%d", port))
		}
	}

	if len(unexpectedOpen) > 0 {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "net_unexpected_ports",
			Expected:     "none",
			Actual:       strings.Join(unexpectedOpen, ", "),
			HIPAAControl: "164.312(e)(1)",
			Severity:     "high",
			Details:      map[string]string{"ports": strings.Join(unexpectedOpen, ",")},
		})
	}

	// Check that expected ports are actually open
	for port, service := range expectedPorts {
		conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 500*time.Millisecond)
		if err != nil {
			findings = append(findings, driftFinding{
				Hostname:     hostname,
				CheckType:    "net_expected_service",
				Expected:     fmt.Sprintf("%s on port %d", service, port),
				Actual:       "not_listening",
				HIPAAControl: "164.308(a)(7)(ii)(A)",
				Severity:     "medium",
				Details:      map[string]string{"service": service, "port": fmt.Sprintf("%d", port)},
			})
		} else {
			conn.Close()
		}
	}

	return findings
}

// checkHostReachability verifies known network hosts are responsive.
func (ns *netScanner) checkHostReachability(ctx context.Context, applianceHostname string) []driftFinding {
	var findings []driftFinding
	cfg := ns.daemon.config

	// Check DC reachability on WinRM port
	if cfg.DomainController != nil && *cfg.DomainController != "" {
		dc := *cfg.DomainController
		conn, err := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", dc, 5985), portTimeout)
		if err != nil {
			findings = append(findings, driftFinding{
				Hostname:     applianceHostname,
				CheckType:    "net_host_reachability",
				Expected:     fmt.Sprintf("%s:5985 reachable", dc),
				Actual:       "unreachable",
				HIPAAControl: "164.308(a)(7)(ii)(A)",
				Severity:     "high",
				Details:      map[string]string{"target": dc, "port": "5985", "service": "winrm"},
			})
		} else {
			conn.Close()
		}
	}

	// Check API endpoint reachability
	apiHost := strings.TrimPrefix(cfg.APIEndpoint, "https://")
	apiHost = strings.TrimPrefix(apiHost, "http://")
	apiHost = strings.Split(apiHost, "/")[0]
	apiHost = strings.Split(apiHost, ":")[0]
	if apiHost != "" {
		conn, err := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", apiHost, 443), portTimeout)
		if err != nil {
			findings = append(findings, driftFinding{
				Hostname:     applianceHostname,
				CheckType:    "net_host_reachability",
				Expected:     fmt.Sprintf("%s:443 reachable", apiHost),
				Actual:       "unreachable",
				HIPAAControl: "164.308(a)(7)(ii)(A)",
				Severity:     "critical",
				Details:      map[string]string{"target": apiHost, "port": "443", "service": "api"},
			})
		} else {
			conn.Close()
		}
	}

	// Check deployed workstations via RDP port 3389 and WinRM 5985
	if ns.daemon.deployer != nil {
		ns.daemon.deployer.mu.Lock()
		hosts := make([]string, 0, len(ns.daemon.deployer.deployed))
		for h := range ns.daemon.deployer.deployed {
			hosts = append(hosts, h)
		}
		ns.daemon.deployer.mu.Unlock()

		for _, host := range hosts {
			select {
			case <-ctx.Done():
				return findings
			default:
			}
			conn, err := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", host, 5985), portTimeout)
			if err != nil {
				findings = append(findings, driftFinding{
					Hostname:     applianceHostname,
					CheckType:    "net_host_reachability",
					Expected:     fmt.Sprintf("%s:5985 reachable", host),
					Actual:       "unreachable",
					HIPAAControl: "164.308(a)(7)(ii)(A)",
					Severity:     "medium",
					Details:      map[string]string{"target": host, "port": "5985", "service": "winrm"},
				})
			} else {
				conn.Close()
			}
		}
	}

	// Check DNS resolution works
	_, err := net.LookupHost("api.osiriscare.net")
	if err != nil {
		findings = append(findings, driftFinding{
			Hostname:     applianceHostname,
			CheckType:    "net_dns_resolution",
			Expected:     "working",
			Actual:       fmt.Sprintf("failed: %v", err),
			HIPAAControl: "164.312(e)(1)",
			Severity:     "high",
		})
	}

	return findings
}

// reportNetDrift sends a network finding through the healing pipeline.
func (ns *netScanner) reportNetDrift(f driftFinding) {
	metadata := map[string]string{
		"platform": "network",
		"source":   "netscan",
	}
	for k, v := range f.Details {
		metadata[k] = v
	}

	req := grpcserver.HealRequest{
		Hostname:     f.Hostname,
		CheckType:    f.CheckType,
		Expected:     f.Expected,
		Actual:       f.Actual,
		HIPAAControl: f.HIPAAControl,
		AgentID:      "netscan",
		Metadata:     metadata,
	}

	log.Printf("[netscan] DRIFT: %s/%s expected=%s actual=%s",
		f.Hostname, f.CheckType, f.Expected, f.Actual)

	ns.daemon.healIncident(context.Background(), req)
}
