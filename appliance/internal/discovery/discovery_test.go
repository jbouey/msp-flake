package discovery

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"testing"
	"time"
)

// --- Domain discovery tests ---

func TestDNToDomain(t *testing.T) {
	tests := []struct {
		dn     string
		domain string
	}{
		{"DC=northvalley,DC=local", "northvalley.local"},
		{"DC=corp,DC=example,DC=com", "corp.example.com"},
		{"DC=single", "single"},
		{"", ""},
		{"OU=Computers,DC=ad,DC=test", "ad.test"},
	}

	for _, tt := range tests {
		result := dnToDomain(tt.dn)
		if result != tt.domain {
			t.Errorf("dnToDomain(%q) = %q, want %q", tt.dn, result, tt.domain)
		}
	}
}

func TestExtractNetBIOS(t *testing.T) {
	tests := []struct {
		domain  string
		netbios string
	}{
		{"northvalley.local", "NORTHVALLEY"},
		{"corp.example.com", "CORP"},
		{"single", "SINGLE"},
		{"", ""},
	}

	for _, tt := range tests {
		result := extractNetBIOS(tt.domain)
		if result != tt.netbios {
			t.Errorf("extractNetBIOS(%q) = %q, want %q", tt.domain, result, tt.netbios)
		}
	}
}

func TestExtractDCPattern(t *testing.T) {
	tests := []struct {
		data     string
		expected string
	}{
		{"something DC=northvalley,DC=local more", "DC=northvalley,DC=local"},
		{"no dc here", ""},
		{"DC=a,DC=b,DC=c trailing", "DC=a,DC=b,DC=c"},
	}

	for _, tt := range tests {
		result := extractDCPattern([]byte(tt.data))
		if result != tt.expected {
			t.Errorf("extractDCPattern(%q) = %q, want %q", tt.data, result, tt.expected)
		}
	}
}

func TestBuildRootDSESearchRequest(t *testing.T) {
	packet := buildRootDSESearchRequest()

	// Should start with SEQUENCE tag (0x30)
	if len(packet) == 0 || packet[0] != 0x30 {
		t.Fatal("packet should start with SEQUENCE tag 0x30")
	}

	// Should contain "defaultNamingContext" string
	found := false
	marker := "defaultNamingContext"
	for i := 0; i <= len(packet)-len(marker); i++ {
		if string(packet[i:i+len(marker)]) == marker {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("packet should contain 'defaultNamingContext'")
	}
}

func TestBERHelpers(t *testing.T) {
	// Test berLength
	if l := berLength(10); len(l) != 1 || l[0] != 10 {
		t.Fatalf("berLength(10) = %v, want [10]", l)
	}
	if l := berLength(200); len(l) != 2 || l[0] != 0x81 {
		t.Fatalf("berLength(200) should use long form, got %v", l)
	}

	// Test berInteger
	i := berInteger(0)
	if i[0] != 0x02 {
		t.Fatal("berInteger should have tag 0x02")
	}

	// Test berOctetString
	s := berOctetString("test")
	if s[0] != 0x04 {
		t.Fatal("berOctetString should have tag 0x04")
	}
	if s[1] != 4 {
		t.Fatalf("berOctetString length should be 4, got %d", s[1])
	}

	// Test berEnum
	e := berEnum(0)
	if e[0] != 0x0a {
		t.Fatal("berEnum should have tag 0x0a")
	}

	// Test berBool
	bt := berBool(true)
	if bt[2] != 0xff {
		t.Fatal("berBool(true) should be 0xff")
	}
	bf := berBool(false)
	if bf[2] != 0x00 {
		t.Fatal("berBool(false) should be 0x00")
	}
}

func TestNewDomainDiscovery(t *testing.T) {
	dd := NewDomainDiscovery([]string{"192.168.88.10"})
	if dd == nil {
		t.Fatal("expected non-nil")
	}
	if len(dd.knownCandidates) != 1 {
		t.Fatalf("expected 1 candidate, got %d", len(dd.knownCandidates))
	}
}

func TestDiscoverTimeout(t *testing.T) {
	dd := NewDomainDiscovery(nil)

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	// Should return nil quickly (no domain to find with no candidates)
	result := dd.Discover(ctx, 100*time.Millisecond)
	// Result may or may not be nil depending on /etc/resolv.conf
	_ = result
}

// --- AD enumeration tests ---

// mockExecutor implements ScriptExecutor for tests.
type mockExecutor struct {
	output string
	err    error
}

func (m *mockExecutor) RunScript(_ context.Context, _, _, _, _ string, _ int) (string, error) {
	return m.output, m.err
}

func TestEnumerateAll(t *testing.T) {
	computers := []map[string]interface{}{
		{
			"Name":                   "SERVER01",
			"DNSHostName":            "server01.northvalley.local",
			"IPv4Address":            "192.168.88.10",
			"OperatingSystem":        "Windows Server 2022 Standard",
			"OperatingSystemVersion": "10.0 (20348)",
			"DistinguishedName":      "CN=SERVER01,OU=Servers,DC=northvalley,DC=local",
			"LastLogonDate":          "2026-02-17T12:00:00Z",
			"Enabled":                true,
			"PrimaryGroupID":         float64(516), // DC
		},
		{
			"Name":                   "WS01",
			"DNSHostName":            "ws01.northvalley.local",
			"IPv4Address":            "192.168.88.101",
			"OperatingSystem":        "Windows 11 Pro",
			"OperatingSystemVersion": "10.0 (22631)",
			"DistinguishedName":      "CN=WS01,OU=Workstations,DC=northvalley,DC=local",
			"Enabled":                true,
			"PrimaryGroupID":         float64(515),
		},
		{
			"Name":                   "WS02",
			"DNSHostName":            "ws02.northvalley.local",
			"OperatingSystem":        "Windows 10 Enterprise",
			"Enabled":                true,
			"PrimaryGroupID":         float64(515),
		},
	}

	output, _ := json.Marshal(computers)
	exec := &mockExecutor{output: string(output)}

	enum := NewADEnumerator("dc1", "admin", "pass", "northvalley.local", exec)
	servers, workstations, err := enum.EnumerateAll(context.Background())
	if err != nil {
		t.Fatalf("EnumerateAll: %v", err)
	}

	if len(servers) != 1 {
		t.Fatalf("expected 1 server, got %d", len(servers))
	}
	if servers[0].Hostname != "SERVER01" {
		t.Fatalf("expected SERVER01, got %s", servers[0].Hostname)
	}
	if !servers[0].IsDomainController {
		t.Fatal("SERVER01 should be DC (PrimaryGroupID=516)")
	}

	if len(workstations) != 2 {
		t.Fatalf("expected 2 workstations, got %d", len(workstations))
	}
	if workstations[0].Hostname != "WS01" {
		t.Fatalf("expected WS01, got %s", workstations[0].Hostname)
	}
	if workstations[0].IPAddress == nil || *workstations[0].IPAddress != "192.168.88.101" {
		t.Fatal("WS01 should have IP 192.168.88.101")
	}
	if workstations[1].IPAddress != nil {
		t.Fatal("WS02 should have nil IP")
	}
}

func TestEnumerateAllSingleResult(t *testing.T) {
	single := map[string]interface{}{
		"Name":            "SOLO",
		"DNSHostName":     "solo.test.local",
		"OperatingSystem": "Windows Server 2019",
		"Enabled":         true,
		"PrimaryGroupID":  float64(515),
	}

	output, _ := json.Marshal(single)
	exec := &mockExecutor{output: string(output)}

	enum := NewADEnumerator("dc1", "admin", "pass", "test.local", exec)
	servers, _, err := enum.EnumerateAll(context.Background())
	if err != nil {
		t.Fatalf("EnumerateAll: %v", err)
	}
	if len(servers) != 1 {
		t.Fatalf("expected 1 server, got %d", len(servers))
	}
}

func TestEnumerateAllEmpty(t *testing.T) {
	exec := &mockExecutor{output: "[]"}

	enum := NewADEnumerator("dc1", "admin", "pass", "test.local", exec)
	servers, workstations, err := enum.EnumerateAll(context.Background())
	if err != nil {
		t.Fatalf("EnumerateAll: %v", err)
	}
	if len(servers) != 0 || len(workstations) != 0 {
		t.Fatal("expected empty results")
	}
}

func TestEnumerateAllExecutorError(t *testing.T) {
	exec := &mockExecutor{err: fmt.Errorf("WinRM timeout")}

	enum := NewADEnumerator("dc1", "admin", "pass", "test.local", exec)
	_, _, err := enum.EnumerateAll(context.Background())
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestEnumerateAllNoExecutor(t *testing.T) {
	enum := NewADEnumerator("dc1", "admin", "pass", "test.local", nil)
	_, _, err := enum.EnumerateAll(context.Background())
	if err == nil {
		t.Fatal("expected error for nil executor")
	}
}

func TestParseADOutputInvalidJSON(t *testing.T) {
	_, err := parseADOutput("not json")
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

func TestParseADOutputEmptyString(t *testing.T) {
	computers, err := parseADOutput("")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if computers != nil {
		t.Fatal("expected nil for empty string")
	}
}

func TestComputerClassification(t *testing.T) {
	tests := []struct {
		os            string
		pgid          int
		isServer      bool
		isWorkstation bool
		isDC          bool
	}{
		{"Windows Server 2022 Standard", 515, true, false, false},
		{"Windows Server 2019 Datacenter", 516, true, false, true},
		{"Windows 10 Pro", 515, false, true, false},
		{"Windows 11 Enterprise", 515, false, true, false},
		{"", 515, false, false, false},
	}

	for _, tt := range tests {
		raw := []map[string]interface{}{
			{
				"Name":              "TEST",
				"OperatingSystem":   tt.os,
				"PrimaryGroupID":    float64(tt.pgid),
				"Enabled":           true,
			},
		}
		computers := parseComputerMaps(raw)
		c := computers[0]
		if c.IsServer != tt.isServer {
			t.Errorf("OS=%q: IsServer=%v, want %v", tt.os, c.IsServer, tt.isServer)
		}
		if c.IsWorkstation != tt.isWorkstation {
			t.Errorf("OS=%q: IsWorkstation=%v, want %v", tt.os, c.IsWorkstation, tt.isWorkstation)
		}
		if c.IsDomainController != tt.isDC {
			t.Errorf("OS=%q pgid=%d: IsDC=%v, want %v", tt.os, tt.pgid, c.IsDomainController, tt.isDC)
		}
	}
}

func TestTestConnectivity(t *testing.T) {
	// Start a test TCP server
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer ln.Close()

	port := ln.Addr().(*net.TCPAddr).Port
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			conn.Close()
		}
	}()

	ip := "127.0.0.1"
	reachable := &ADComputer{Hostname: "localhost", IPAddress: &ip}
	if !TestConnectivity(context.Background(), reachable, port) {
		t.Fatal("expected reachable")
	}

	unreachable := &ADComputer{Hostname: "192.168.88.254"}
	if TestConnectivity(context.Background(), unreachable, 59999) {
		t.Fatal("expected unreachable")
	}
}

func TestTestConnectivityEmptyHost(t *testing.T) {
	c := &ADComputer{}
	if TestConnectivity(context.Background(), c, 5985) {
		t.Fatal("expected false for empty host")
	}
}

func TestResolveMissingIPs(t *testing.T) {
	exec := &mockExecutor{}
	enum := NewADEnumerator("dc1", "admin", "pass", "test.local", exec)

	ip := "192.168.1.1"
	computers := []ADComputer{
		{Hostname: "existing", IPAddress: &ip, FQDN: "existing.test"},
		{Hostname: "localhost", FQDN: "localhost"}, // This should resolve
	}

	enum.ResolveMissingIPs(context.Background(), computers)

	// First should keep its IP
	if *computers[0].IPAddress != "192.168.1.1" {
		t.Fatalf("existing IP should not change, got %s", *computers[0].IPAddress)
	}

	// Second should have resolved localhost
	if computers[1].IPAddress == nil {
		t.Fatal("localhost should have resolved")
	}
}

func TestDiscoveredDomainJSON(t *testing.T) {
	d := &DiscoveredDomain{
		DomainName:        "test.local",
		NetBIOSName:       "TEST",
		DomainControllers: []string{"dc1.test.local"},
		DNSServers:        []string{"192.168.1.1"},
		DiscoveredAt:      "2026-02-17T00:00:00Z",
		DiscoveryMethod:   "dns_srv",
	}

	data, err := json.Marshal(d)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var d2 DiscoveredDomain
	if err := json.Unmarshal(data, &d2); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if d2.DomainName != "test.local" {
		t.Fatalf("expected test.local, got %s", d2.DomainName)
	}
}
