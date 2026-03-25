package daemon

import (
	"bufio"
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/osiriscare/appliance/internal/evidence"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/phiscrub"
)

const (
	netScanInterval = 3 * time.Minute
	portTimeout     = 3 * time.Second
)

// discoveredDevice holds a device found via ARP table scanning.
type discoveredDevice struct {
	IPAddress  string `json:"ip_address"`
	MACAddress string `json:"mac_address"`
	Hostname   string `json:"hostname,omitempty"`
	Interface  string `json:"interface,omitempty"`
	// Probe fields
	OSType        string `json:"os_type,omitempty"`
	Distro        string `json:"distro,omitempty"`
	OSFingerprint string `json:"os_fingerprint,omitempty"`
	ProbeSSH      bool   `json:"probe_ssh"`
	ProbeWinRM    bool   `json:"probe_winrm"`
	ADJoined      bool   `json:"ad_joined"`
	HasAgent      bool   `json:"has_agent"`
	// Topology field
	Subnet string `json:"subnet,omitempty"`
}

// rogueDetector tracks known MACs and detects new (rogue) devices.
type rogueDetector struct {
	knownMACs     map[string]time.Time // MAC → first seen
	baselineUntil time.Time            // suppress alerts during first 24h
	alertCount    int                  // rate limit counter
	alertWindow   time.Time            // rate limit window start
	mu            sync.Mutex
}

func newRogueDetector() *rogueDetector {
	return &rogueDetector{
		knownMACs:     make(map[string]time.Time),
		baselineUntil: time.Now().Add(24 * time.Hour),
		alertWindow:   time.Now(),
	}
}

// checkForRogues compares current devices against known MACs.
// Returns newly-seen devices (potential rogues).
// Suppressed during baseline period (first 24h) and rate-limited to 10/hour.
func (rd *rogueDetector) checkForRogues(devices []discoveredDevice) []discoveredDevice {
	rd.mu.Lock()
	defer rd.mu.Unlock()

	// Suppress during baseline period (first 24h after boot), but still learn MACs
	if time.Now().Before(rd.baselineUntil) {
		for _, d := range devices {
			if d.MACAddress != "" {
				rd.knownMACs[d.MACAddress] = time.Now()
			}
		}
		return nil
	}

	// Reset rate limit window every hour
	if time.Since(rd.alertWindow) > time.Hour {
		rd.alertCount = 0
		rd.alertWindow = time.Now()
	}

	var rogues []discoveredDevice
	for _, d := range devices {
		if d.MACAddress == "" {
			continue
		}
		if _, known := rd.knownMACs[d.MACAddress]; !known {
			// New MAC — learn it regardless of rate limit
			rd.knownMACs[d.MACAddress] = time.Now()
			// Only alert if under rate limit
			if rd.alertCount < 10 {
				rogues = append(rogues, d)
				rd.alertCount++
			}
		}
	}
	return rogues
}

// ipChange records a MAC address moving from one IP to another.
type ipChange struct {
	MACAddress string `json:"mac_address"`
	OldIP      string `json:"old_ip"`
	NewIP      string `json:"new_ip"`
	Hostname   string `json:"hostname,omitempty"`
}

// netScanner periodically checks network-level security:
// - Appliance listening ports (detect unexpected services)
// - Host reachability (known hosts responsive on expected ports)
// - External binding detection (services on 0.0.0.0 vs localhost)
// - ARP-based device discovery (LAN host inventory)
type netScanner struct {
	svc    *Services // interfaces for decoupled access
	daemon *Daemon   // for healing pipeline, evidence, deployer, etc.

	mu           sync.Mutex
	lastScanTime time.Time
	running      int32

	// Last discovered devices from ARP table
	devicesMu      sync.Mutex
	discoveredDevs []discoveredDevice

	// MAC-to-IP history: tracks last-known IP for each MAC address.
	// Used to detect DHCP IP changes between scan cycles.
	macIPMu      sync.Mutex
	macIPHistory map[string]string // MAC → last known IP

	// Rogue device detection
	rogueDetector *rogueDetector
}

func newNetScanner(svc *Services, d *Daemon) *netScanner {
	return &netScanner{
		svc:           svc,
		daemon:        d,
		macIPHistory:  make(map[string]string),
		rogueDetector: newRogueDetector(),
	}
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
	hostname := ns.svc.Config.SiteID + "-appliance"

	// 1. Check for unexpected listening ports on the appliance
	portFindings := ns.checkListeningPorts(hostname)
	findings = append(findings, portFindings...)

	// 2. Check known hosts are reachable on expected ports
	reachFindings := ns.checkHostReachability(ctx, hostname)
	findings = append(findings, reachFindings...)

	// Report all findings through the healing pipeline
	for i := range findings {
		ns.reportNetDrift(&findings[i])
	}

	// Submit network evidence bundle for compliance scoring
	if ns.daemon.evidenceSubmitter != nil {
		scannedHosts := []string{hostname}
		evFindings := make([]evidence.DriftFinding, len(findings))
		for i, f := range findings {
			evFindings[i] = evidence.DriftFinding{
				Hostname:     f.Hostname,
				CheckType:    f.CheckType,
				Expected:     f.Expected,
				Actual:       f.Actual,
				HIPAAControl: f.HIPAAControl,
				Severity:     f.Severity,
			}
		}
		if err := ns.daemon.evidenceSubmitter.BuildAndSubmitNetwork(ctx, evFindings, scannedHosts); err != nil {
			log.Printf("[netscan] Evidence submission failed: %v", err)
		}
	}

	// 3. Discover devices from the ARP table (passive — reads kernel ARP cache)
	devices := discoverARPDevices()

	// Enrich with OS probes (SSH banner, WinRM check, Kerberos port)
	if len(devices) > 0 {
		ips := make([]string, len(devices))
		for i, d := range devices {
			ips[i] = d.IPAddress
		}
		probes := probeHosts(ctx, ips)

		// Cross-reference with AD host cache to detect AD-joined Linux machines
		adHostnames := ns.daemon.getADHostnames()

		for i, p := range probes {
			devices[i].OSType = p.OSType
			devices[i].Distro = p.Distro
			devices[i].OSFingerprint = p.OSFingerprint
			devices[i].ProbeSSH = p.SSHOpen
			devices[i].ProbeWinRM = p.WinRMOpen
			if classifyADJoined(p, adHostnames) {
				devices[i].ADJoined = true
			}
		}
		adJoinedCount := countTrue(devices, func(d discoveredDevice) bool { return d.ADJoined })
		log.Printf("[netscan] Probed %d devices: %d SSH, %d WinRM, %d AD-joined",
			len(devices),
			countTrue(devices, func(d discoveredDevice) bool { return d.ProbeSSH }),
			countTrue(devices, func(d discoveredDevice) bool { return d.ProbeWinRM }),
			adJoinedCount)
	}

	// Populate Subnet field for each device
	for i := range devices {
		devices[i].Subnet = getDeviceSubnet(devices[i].IPAddress)
	}

	// Subnet topology analysis
	subnetGroups := groupBySubnet(devices)
	if len(subnetGroups) > 1 {
		log.Printf("[netscan] Multiple subnets detected: %d subnets", len(subnetGroups))
		for _, g := range subnetGroups {
			log.Printf("[netscan]   %s: %d devices", g.Subnet, len(g.Devices))
		}

		// Flag devices on unexpected subnets
		unexpected := detectUnexpectedSubnets(subnetGroups)
		for _, d := range unexpected {
			ns.reportNetDrift(&driftFinding{
				Hostname:     d.Hostname,
				CheckType:    "NETWORK-UNEXPECTED-SUBNET",
				Expected:     "Device on primary subnet",
				Actual:       fmt.Sprintf("Device %s (%s) on unexpected subnet %s", d.Hostname, d.IPAddress, getDeviceSubnet(d.IPAddress)),
				HIPAAControl: "164.312(a)(1)",
				Severity:     "low",
				Details: map[string]string{
					"ip_address":  d.IPAddress,
					"mac_address": d.MACAddress,
					"subnet":      getDeviceSubnet(d.IPAddress),
				},
			})
		}
	}

	// Detect DHCP IP changes by comparing current MAC→IP mappings with history
	ipChanges := ns.detectIPChanges(devices)

	ns.devicesMu.Lock()
	ns.discoveredDevs = devices
	ns.devicesMu.Unlock()

	// Check for rogue (previously unseen) devices and report as drift findings
	rogues := ns.rogueDetector.checkForRogues(devices)
	for _, r := range rogues {
		ns.reportNetDrift(&driftFinding{
			Hostname:     r.Hostname,
			CheckType:    "NETWORK-ROGUE-DEVICE",
			Expected:     "No unknown devices on network",
			Actual:       fmt.Sprintf("New device: %s (%s) MAC=%s", r.Hostname, r.IPAddress, r.MACAddress),
			HIPAAControl: "164.312(a)(1)",
			Severity:     "medium",
			Details: map[string]string{
				"ip_address":  r.IPAddress,
				"mac_address": r.MACAddress,
				"hostname":    r.Hostname,
				"os_type":     r.OSType,
				"probe_ssh":   fmt.Sprintf("%v", r.ProbeSSH),
				"probe_winrm": fmt.Sprintf("%v", r.ProbeWinRM),
			},
		})
	}
	if len(rogues) > 0 {
		log.Printf("[netscan] Rogue devices detected: %d new MACs", len(rogues))
	}

	// Sync discovered devices (with probe data + IP changes) to Central Command
	ns.syncDiscoveredDevices(ctx, devices, ipChanges)

	log.Printf("[netscan] Scan complete: findings=%d, arp_devices=%d, ip_changes=%d", len(findings), len(devices), len(ipChanges))
}

// expectedPorts are the ports the appliance should have open.
var expectedPorts = map[int]string{
	22:    "sshd",
	80:    "http-file-server",
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

		dialer := net.Dialer{Timeout: 500 * time.Millisecond}
		conn, err := dialer.DialContext(context.Background(), "tcp", fmt.Sprintf("127.0.0.1:%d", port))
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
		dialer := net.Dialer{Timeout: 500 * time.Millisecond}
		conn, err := dialer.DialContext(context.Background(), "tcp", fmt.Sprintf("127.0.0.1:%d", port))
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
	cfg := ns.svc.Config

	// Check DC reachability on WinRM port
	if cfg.DomainController != nil && *cfg.DomainController != "" {
		dc := *cfg.DomainController
		dialer := net.Dialer{Timeout: portTimeout}
		conn, err := dialer.DialContext(ctx, "tcp", fmt.Sprintf("%s:%d", dc, 5985))
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
		dialer := net.Dialer{Timeout: portTimeout}
		conn, err := dialer.DialContext(ctx, "tcp", fmt.Sprintf("%s:%d", apiHost, 443))
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
			dialer := net.Dialer{Timeout: portTimeout}
			conn, err := dialer.DialContext(ctx, "tcp", fmt.Sprintf("%s:%d", host, 5985))
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
	_, err := net.DefaultResolver.LookupHost(ctx, "api.osiriscare.net")
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

// detectIPChanges compares current MAC→IP mappings with the previous scan cycle.
// Returns a list of devices whose IP changed (DHCP reassignment) and updates the
// history map. Logs each change for operator visibility.
func (ns *netScanner) detectIPChanges(devices []discoveredDevice) []ipChange {
	ns.macIPMu.Lock()
	defer ns.macIPMu.Unlock()

	var changes []ipChange
	for _, d := range devices {
		if d.MACAddress == "" {
			continue
		}
		prevIP, known := ns.macIPHistory[d.MACAddress]
		ns.macIPHistory[d.MACAddress] = d.IPAddress
		if known && prevIP != d.IPAddress {
			log.Printf("[netscan] IP change detected: MAC %s moved from %s to %s",
				d.MACAddress, prevIP, d.IPAddress)
			changes = append(changes, ipChange{
				MACAddress: d.MACAddress,
				OldIP:      prevIP,
				NewIP:      d.IPAddress,
				Hostname:   d.Hostname,
			})
		}
	}
	if len(changes) > 0 {
		log.Printf("[netscan] %d IP change(s) detected this cycle", len(changes))
	}
	return changes
}

// LookupDeviceByHostname searches the last netscan results for a device matching
// the given hostname (case-insensitive). Returns the discovered IP and true if found.
func (ns *netScanner) LookupDeviceByHostname(hostname string) (string, bool) {
	ns.devicesMu.Lock()
	defer ns.devicesMu.Unlock()

	lower := strings.ToLower(hostname)
	for _, d := range ns.discoveredDevs {
		if strings.ToLower(d.Hostname) == lower {
			return d.IPAddress, true
		}
		// Also match short hostname against FQDN (e.g. "NVSRV01" matches "nvsrv01.northvalley.local")
		if strings.HasPrefix(strings.ToLower(d.Hostname), lower+".") {
			return d.IPAddress, true
		}
	}
	return "", false
}

// syncDiscoveredDevices sends the discovered device list (with probe data) to
// Central Command via POST /api/devices/sync. This is what populates the
// dashboard's device inventory with SSH/WinRM/AD status and OS fingerprints.
func (ns *netScanner) syncDiscoveredDevices(ctx context.Context, devices []discoveredDevice, ipChanges []ipChange) {
	if len(devices) == 0 {
		return
	}

	cfg := ns.svc.Config
	now := time.Now().UTC().Format(time.RFC3339)

	entries := make([]deviceSyncEntry, 0, len(devices))
	for _, d := range devices {
		// Use MAC as the stable device ID (unique per device on the LAN)
		deviceID := d.MACAddress
		if deviceID == "" {
			deviceID = d.IPAddress // fallback for devices without MAC
		}

		entry := deviceSyncEntry{
			DeviceID:         deviceID,
			Hostname:         d.Hostname,
			IPAddress:        d.IPAddress,
			MACAddress:       d.MACAddress,
			DeviceType:       "unknown",
			ComplianceStatus: "unknown",
			DiscoverySource:  "arp",
			FirstSeenAt:      now,
			LastSeenAt:       now,
			OpenPorts:        []int{},
		}

		// Populate probe fields
		if d.ProbeSSH || d.ProbeWinRM || d.ADJoined {
			entry.ProbeSSH = &d.ProbeSSH
			entry.ProbeWinRM = &d.ProbeWinRM
			entry.ADJoined = &d.ADJoined
		}
		if d.OSFingerprint != "" {
			entry.OSFingerprint = d.OSFingerprint
		}
		if d.Distro != "" {
			entry.Distro = d.Distro
		}
		if d.OSType != "" {
			entry.OSName = d.OSType
		}

		// Build open_ports list from probe results
		if d.ProbeSSH {
			entry.OpenPorts = append(entry.OpenPorts, 22)
		}
		if d.ProbeWinRM {
			entry.OpenPorts = append(entry.OpenPorts, 5985)
		}

		entries = append(entries, entry)
	}

	payload := &deviceSyncPayload{
		ApplianceID:      ns.daemon.orderProc.ApplianceID(),
		SiteID:           cfg.SiteID,
		ScanTimestamp:    now,
		Devices:          entries,
		TotalDevices:     len(entries),
		MonitoredDevices: len(entries),
		ExcludedDevices:  0,
		MedicalDevices:   0,
		ComplianceRate:   0,
		IPChanges:        ipChanges,
	}

	if err := ns.daemon.phoneCli.SyncDevices(ctx, payload); err != nil {
		log.Printf("[netscan] Device sync failed: %v", err)
	} else {
		log.Printf("[netscan] Device sync OK: %d devices sent to Central Command", len(entries))
	}
}

// DiscoveredDevices returns the most recent ARP-discovered device list.
func (ns *netScanner) DiscoveredDevices() []discoveredDevice {
	ns.devicesMu.Lock()
	defer ns.devicesMu.Unlock()
	out := make([]discoveredDevice, len(ns.discoveredDevs))
	copy(out, ns.discoveredDevs)
	return out
}

// discoverARPDevices reads the Linux ARP table from /proc/net/arp and returns
// all valid entries with their actual hardware (MAC) addresses.
//
// /proc/net/arp format:
//   IP address       HW type     Flags       HW address            Mask     Device
//   192.168.88.250   0x1         0x2         08:00:27:fd:68:81     *        enp0s3
//
// Column 3 (index 3, zero-based) is the remote device's MAC address.
// This must NOT be confused with the local interface MAC (which net.Interfaces()
// would return) — that was the source of the bug where all devices showed the
// appliance's own MAC.
func discoverARPDevices() []discoveredDevice {
	f, err := os.Open("/proc/net/arp")
	if err != nil {
		log.Printf("[netscan] Cannot read /proc/net/arp: %v", err)
		return nil
	}
	defer f.Close()

	var devices []discoveredDevice
	scanner := bufio.NewScanner(f)

	// Skip header line
	if !scanner.Scan() {
		return nil
	}

	for scanner.Scan() {
		line := scanner.Text()
		fields := strings.Fields(line)
		if len(fields) < 6 {
			continue
		}

		ip := fields[0]
		flags := fields[2]
		mac := fields[3]
		iface := fields[5]

		// Skip incomplete entries (flags 0x0 means no valid ARP entry)
		if flags == "0x0" {
			continue
		}

		// Skip entries with no MAC resolved
		if mac == "00:00:00:00:00:00" || mac == "" {
			continue
		}

		// Validate IP format (basic check)
		if net.ParseIP(ip) == nil {
			continue
		}

		devices = append(devices, discoveredDevice{
			IPAddress:  ip,
			MACAddress: strings.ToLower(mac),
			Interface:  iface,
		})
	}

	// Resolve hostnames via reverse DNS (best-effort, parallel with timeout)
	resolveHostnames(devices)

	return devices
}

// resolveHostnames performs reverse DNS lookups on discovered devices.
// Uses the local resolver (which queries the router/DHCP server's DNS),
// picking up DHCP-assigned hostnames that the router tracks.
// Lookups are parallel with a per-IP timeout to avoid blocking the scan.
func resolveHostnames(devices []discoveredDevice) {
	if len(devices) == 0 {
		return
	}

	const lookupTimeout = 2 * time.Second
	var wg sync.WaitGroup

	for i := range devices {
		if devices[i].Hostname != "" {
			continue // already has a name
		}
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), lookupTimeout)
			defer cancel()

			resolver := &net.Resolver{}
			names, err := resolver.LookupAddr(ctx, devices[idx].IPAddress)
			if err != nil || len(names) == 0 {
				return
			}
			// LookupAddr returns FQDNs with trailing dot — strip it
			name := strings.TrimSuffix(names[0], ".")
			// Skip if the "hostname" is just the IP repeated
			if name == devices[idx].IPAddress {
				return
			}
			devices[idx].Hostname = name
		}(i)
	}

	wg.Wait()
	resolved := 0
	for _, d := range devices {
		if d.Hostname != "" {
			resolved++
		}
	}
	if resolved > 0 {
		log.Printf("[netscan] Resolved %d/%d device hostnames via reverse DNS", resolved, len(devices))
	}
}

// countTrue counts devices satisfying fn.
func countTrue(devices []discoveredDevice, fn func(discoveredDevice) bool) int {
	n := 0
	for _, d := range devices {
		if fn(d) {
			n++
		}
	}
	return n
}

// reportNetDrift sends a network finding through the healing pipeline.
// All text fields that could contain PHI are scrubbed before egress.
func (ns *netScanner) reportNetDrift(f *driftFinding) {
	// Scrub details map values — may contain discovered hostnames or device info
	metadata := map[string]string{
		"platform": "network",
		"source":   "netscan",
	}
	for k, v := range f.Details {
		metadata[k] = phiscrub.Scrub(v)
	}

	req := grpcserver.HealRequest{
		Hostname:     phiscrub.Scrub(f.Hostname),
		CheckType:    f.CheckType,
		Expected:     phiscrub.Scrub(f.Expected),
		Actual:       phiscrub.Scrub(f.Actual),
		HIPAAControl: f.HIPAAControl,
		AgentID:      "netscan",
		Metadata:     metadata,
	}

	log.Printf("[netscan] DRIFT: %s/%s expected=%s actual=%s",
		f.Hostname, f.CheckType, f.Expected, f.Actual)

	ns.daemon.healIncident(context.Background(), &req)
}
