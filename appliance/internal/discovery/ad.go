package discovery

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"strings"
	"time"
)

// ADComputer represents a computer object from Active Directory.
type ADComputer struct {
	Hostname           string  `json:"hostname"`
	FQDN               string  `json:"fqdn"`
	IPAddress          *string `json:"ip_address,omitempty"`
	OSName             string  `json:"os_name"`
	OSVersion          string  `json:"os_version"`
	IsServer           bool    `json:"is_server"`
	IsWorkstation      bool    `json:"is_workstation"`
	IsDomainController bool    `json:"is_domain_controller"`
	OUPath             string  `json:"ou_path"`
	LastLogon          *string `json:"last_logon,omitempty"`
	Enabled            bool    `json:"enabled"`
}

// EnumerationResult aggregates enumeration data with connectivity.
type EnumerationResult struct {
	Servers       []ADComputer `json:"servers"`
	Workstations  []ADComputer `json:"workstations"`
	Reachable     []ADComputer `json:"reachable"`
	Unreachable   []ADComputer `json:"unreachable"`
	EnumeratedAt  string       `json:"enumerated_at"`
	TotalFound    int          `json:"total_found"`
}

// ScriptExecutor is the interface for running PowerShell scripts on a target.
type ScriptExecutor interface {
	RunScript(ctx context.Context, hostname, script, username, password string, timeout int) (string, error)
}

// ADEnumerator queries Active Directory for computer objects.
type ADEnumerator struct {
	domainController string
	username         string
	password         string
	domain           string
	executor         ScriptExecutor
}

// NewADEnumerator creates a new AD enumerator.
func NewADEnumerator(dc, username, password, domain string, executor ScriptExecutor) *ADEnumerator {
	return &ADEnumerator{
		domainController: dc,
		username:         username,
		password:         password,
		domain:           domain,
		executor:         executor,
	}
}

// enumeration PowerShell script
const adEnumScript = `
Import-Module ActiveDirectory -ErrorAction SilentlyContinue

$computers = Get-ADComputer -Filter * -Properties ` +
	"`Name, DNSHostName, IPv4Address, OperatingSystem, OperatingSystemVersion, " +
	"DistinguishedName, LastLogonDate, Enabled, PrimaryGroupID`" + `

$result = @()
foreach ($c in $computers) {
    $obj = @{
        Name = $c.Name
        DNSHostName = $c.DNSHostName
        IPv4Address = $c.IPv4Address
        OperatingSystem = $c.OperatingSystem
        OperatingSystemVersion = $c.OperatingSystemVersion
        DistinguishedName = $c.DistinguishedName
        LastLogonDate = if ($c.LastLogonDate) { $c.LastLogonDate.ToString("o") } else { $null }
        Enabled = $c.Enabled
        PrimaryGroupID = $c.PrimaryGroupID
    }
    $result += $obj
}

$result | ConvertTo-Json -Compress
`

// EnumerateAll queries the DC for all computer objects.
// Returns (servers, workstations, error).
func (e *ADEnumerator) EnumerateAll(ctx context.Context) ([]ADComputer, []ADComputer, error) {
	if e.executor == nil {
		return nil, nil, fmt.Errorf("no script executor configured")
	}

	log.Printf("[ad] Starting AD enumeration against %s", e.domainController)

	output, err := e.executor.RunScript(ctx, e.domainController, adEnumScript, e.username, e.password, 120)
	if err != nil {
		return nil, nil, fmt.Errorf("AD enumeration failed: %w", err)
	}

	computers, err := parseADOutput(output)
	if err != nil {
		return nil, nil, fmt.Errorf("parse AD output: %w", err)
	}

	var servers, workstations []ADComputer
	for _, c := range computers {
		if c.IsServer || c.IsDomainController {
			servers = append(servers, c)
		} else if c.IsWorkstation {
			workstations = append(workstations, c)
		}
	}

	log.Printf("[ad] Enumerated %d computers: %d servers, %d workstations",
		len(computers), len(servers), len(workstations))

	return servers, workstations, nil
}

// EnumerateWithConnectivity enumerates and tests WinRM reachability.
func (e *ADEnumerator) EnumerateWithConnectivity(ctx context.Context, port int) (*EnumerationResult, error) {
	servers, workstations, err := e.EnumerateAll(ctx)
	if err != nil {
		return nil, err
	}

	if port == 0 {
		port = 5985
	}

	all := append(append([]ADComputer{}, servers...), workstations...)

	var reachable, unreachable []ADComputer
	for _, c := range all {
		if TestConnectivity(ctx, &c, port) {
			reachable = append(reachable, c)
		} else {
			unreachable = append(unreachable, c)
		}
	}

	return &EnumerationResult{
		Servers:      servers,
		Workstations: workstations,
		Reachable:    reachable,
		Unreachable:  unreachable,
		EnumeratedAt: time.Now().UTC().Format(time.RFC3339),
		TotalFound:   len(all),
	}, nil
}

// ResolveMissingIPs resolves FQDNs to IP addresses for computers without IPv4.
func (e *ADEnumerator) ResolveMissingIPs(ctx context.Context, computers []ADComputer) {
	for i := range computers {
		if computers[i].IPAddress != nil && *computers[i].IPAddress != "" {
			continue
		}

		fqdn := computers[i].FQDN
		if fqdn == "" {
			fqdn = computers[i].Hostname
		}

		ips, err := net.DefaultResolver.LookupHost(ctx, fqdn)
		if err != nil || len(ips) == 0 {
			continue
		}

		// Prefer IPv4
		for _, ip := range ips {
			if net.ParseIP(ip).To4() != nil {
				computers[i].IPAddress = &ip
				break
			}
		}
	}
}

// TestConnectivity tests if a host is reachable on a given port (TCP connect test).
func TestConnectivity(ctx context.Context, target *ADComputer, port int) bool {
	host := ""
	if target.IPAddress != nil && *target.IPAddress != "" {
		host = *target.IPAddress
	} else if target.FQDN != "" {
		host = target.FQDN
	} else {
		host = target.Hostname
	}

	if host == "" {
		return false
	}

	addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))

	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	dialer := net.Dialer{}
	conn, err := dialer.DialContext(ctx, "tcp", addr)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// parseADOutput parses the JSON output from Get-ADComputer.
func parseADOutput(output string) ([]ADComputer, error) {
	output = strings.TrimSpace(output)
	if output == "" {
		return nil, nil
	}

	// Try array first
	var rawArray []map[string]interface{}
	if err := json.Unmarshal([]byte(output), &rawArray); err == nil {
		return parseComputerMaps(rawArray), nil
	}

	// Try single object
	var rawObj map[string]interface{}
	if err := json.Unmarshal([]byte(output), &rawObj); err == nil {
		return parseComputerMaps([]map[string]interface{}{rawObj}), nil
	}

	return nil, fmt.Errorf("failed to parse AD JSON output")
}

func parseComputerMaps(raw []map[string]interface{}) []ADComputer {
	computers := make([]ADComputer, 0, len(raw))
	for _, m := range raw {
		c := ADComputer{
			Hostname:  strVal(m, "Name"),
			FQDN:      strVal(m, "DNSHostName"),
			OSName:    strVal(m, "OperatingSystem"),
			OSVersion: strVal(m, "OperatingSystemVersion"),
			OUPath:    strVal(m, "DistinguishedName"),
			Enabled:   boolVal(m, "Enabled"),
		}

		if ip := strVal(m, "IPv4Address"); ip != "" {
			c.IPAddress = &ip
		}

		if logon := strVal(m, "LastLogonDate"); logon != "" {
			c.LastLogon = &logon
		}

		// Classify
		osLower := strings.ToLower(c.OSName)
		c.IsServer = strings.Contains(osLower, "server")
		c.IsWorkstation = !c.IsServer && (strings.Contains(osLower, "windows 10") ||
			strings.Contains(osLower, "windows 11") ||
			strings.Contains(osLower, "professional") ||
			strings.Contains(osLower, "enterprise"))

		// PrimaryGroupID 516 = Domain Controller
		pgid := intVal(m, "PrimaryGroupID")
		c.IsDomainController = pgid == 516

		computers = append(computers, c)
	}
	return computers
}

// --- Map access helpers ---

func strVal(m map[string]interface{}, key string) string {
	v, _ := m[key].(string)
	return v
}

func boolVal(m map[string]interface{}, key string) bool {
	v, _ := m[key].(bool)
	return v
}

func intVal(m map[string]interface{}, key string) int {
	switch v := m[key].(type) {
	case float64:
		return int(v)
	case int:
		return v
	}
	return 0
}
