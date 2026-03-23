package phiscrub

import (
	"strings"
	"testing"
)

func TestScrub_SSN(t *testing.T) {
	tests := []struct {
		input    string
		contains string // should NOT be in output
	}{
		{"SSN is 123-45-6789", "123-45-6789"},
		{"Patient SSN: 999 88 7777", "999 88 7777"},
	}

	for _, tt := range tests {
		result := Scrub(tt.input)
		if strings.Contains(result, tt.contains) {
			t.Errorf("SSN not scrubbed: %q still in %q", tt.contains, result)
		}
		if !strings.Contains(result, "[SSN-REDACTED-") {
			t.Errorf("Missing SSN redaction tag in %q", result)
		}
	}
}

func TestScrub_Email(t *testing.T) {
	tests := []struct {
		input string
		phi   string
	}{
		{"Contact admin@hospital.com for records", "admin@hospital.com"},
		{"Email: patient.john@clinic.org", "patient.john@clinic.org"},
	}

	for _, tt := range tests {
		result := Scrub(tt.input)
		if strings.Contains(result, tt.phi) {
			t.Errorf("Email not scrubbed: %q still in %q", tt.phi, result)
		}
		if !strings.Contains(result, "[EMAIL-REDACTED-") {
			t.Errorf("Missing EMAIL redaction tag in %q", result)
		}
	}
}

func TestScrub_MRN(t *testing.T) {
	tests := []string{
		"MRN: 12345678",
		"mrn#99887766",
		"MRN 5555",
	}

	for _, input := range tests {
		result := Scrub(input)
		if !strings.Contains(result, "[MRN-REDACTED-") {
			t.Errorf("MRN not scrubbed in %q -> %q", input, result)
		}
	}
}

func TestScrub_PatientHostname(t *testing.T) {
	tests := []struct {
		input    string
		redacted bool
		tag      string
	}{
		{"Host PATIENT-ROOM-201 is offline", true, "[HOSTNAME-REDACTED-"},
		{"Scanning BED-4A-MONITOR", true, "[HOSTNAME-REDACTED-"},
		{"Device WARD-3-NURSE-STATION", true, "[HOSTNAME-REDACTED-"},
		{"Connected to DR.SMITH-PC", true, "[HOSTNAME-REDACTED-"},
		{"Connected to MR.JONES-LAPTOP", true, "[HOSTNAME-REDACTED-"},
		{"Scanning MS.DOE-WORKSTATION", true, "[HOSTNAME-REDACTED-"},
	}

	for _, tt := range tests {
		result := Scrub(tt.input)
		if tt.redacted && !strings.Contains(result, tt.tag) {
			t.Errorf("Patient hostname not scrubbed in %q -> %q", tt.input, result)
		}
	}
}

func TestScrub_FilePath(t *testing.T) {
	tests := []struct {
		input    string
		contains string // should NOT be in output
	}{
		{"Reading /patient/john-doe/records.pdf", "/patient/john-doe"},
		{"File at /ehr/12345/chart.xml loaded", "/ehr/12345"},
		{"Export /medical/visits-2024 completed", "/medical/visits-2024"},
	}

	for _, tt := range tests {
		result := Scrub(tt.input)
		if strings.Contains(result, tt.contains) {
			t.Errorf("PHI path not scrubbed: %q still in %q", tt.contains, result)
		}
		if !strings.Contains(result, "[PATH-REDACTED-") {
			t.Errorf("Missing PATH redaction tag in %q", result)
		}
	}
}

func TestScrub_NoFalsePositives(t *testing.T) {
	// Normal infrastructure strings that must NOT be scrubbed
	infraStrings := []string{
		"firewall_status drift_detected=true",
		"Windows Defender is disabled",
		"Service wuauserv is stopped",
		"Port 5985 open on DC01",
		"NixOS rebuild completed in 45s",
		"Check linux_ssh_config failed",
		"HIPAA control 164.312(a)(1)",
		"Host NVDC01 is reachable",
		"Interface enp0s3 is up",
		"Site site-northvalley configured",
	}

	for _, input := range infraStrings {
		result := Scrub(input)
		if result != input {
			t.Errorf("False positive on infra data: %q -> %q", input, result)
		}
	}
}

func TestScrub_IPsPreserved(t *testing.T) {
	input := "Server at 192.168.1.100 has SSN 123-45-6789 and IP 10.0.0.1"
	result := Scrub(input)

	if !strings.Contains(result, "192.168.1.100") {
		t.Errorf("IP 192.168.1.100 was scrubbed: %q", result)
	}
	if !strings.Contains(result, "10.0.0.1") {
		t.Errorf("IP 10.0.0.1 was scrubbed: %q", result)
	}
	if strings.Contains(result, "123-45-6789") {
		t.Error("SSN was NOT scrubbed alongside IPs")
	}
	if !VerifyIPsPreserved(input) {
		t.Error("VerifyIPsPreserved returned false")
	}
}

func TestScrub_MACsPreserved(t *testing.T) {
	input := "Device MAC 08:00:27:fd:68:81 found with SSN 123-45-6789"
	result := Scrub(input)

	if !strings.Contains(result, "08:00:27:fd:68:81") {
		t.Errorf("MAC address was scrubbed: %q", result)
	}
	if strings.Contains(result, "123-45-6789") {
		t.Error("SSN was NOT scrubbed alongside MAC")
	}
}

func TestScrubMap(t *testing.T) {
	data := map[string]interface{}{
		"hostname":   "DC01",
		"ip_address": "192.168.88.100",
		"user_info":  "Patient John, SSN 123-45-6789, MRN: 12345678",
		"nested": map[string]interface{}{
			"email": "patient@hospital.com",
			"count": 42,
		},
		"list": []interface{}{"Call (555) 123-4567", 99},
	}

	scrubbed := ScrubMap(data)

	// IP preserved
	if scrubbed["ip_address"] != "192.168.88.100" {
		t.Errorf("IP was scrubbed: %v", scrubbed["ip_address"])
	}

	// Hostname preserved (DC01 is not a patient identifier)
	if scrubbed["hostname"] != "DC01" {
		t.Errorf("Normal hostname was scrubbed: %v", scrubbed["hostname"])
	}

	// SSN scrubbed
	userInfo := scrubbed["user_info"].(string)
	if strings.Contains(userInfo, "123-45-6789") {
		t.Error("SSN not scrubbed in map")
	}
	if !strings.Contains(userInfo, "[SSN-REDACTED-") {
		t.Error("Missing SSN tag in map")
	}

	// Nested email scrubbed
	nested := scrubbed["nested"].(map[string]interface{})
	email := nested["email"].(string)
	if strings.Contains(email, "patient@hospital.com") {
		t.Error("Nested email not scrubbed")
	}

	// Nested int preserved
	if nested["count"] != 42 {
		t.Error("Nested int was modified")
	}

	// List phone scrubbed
	list := scrubbed["list"].([]interface{})
	if !strings.Contains(list[0].(string), "[PHONE-REDACTED-") {
		t.Error("Phone in list not scrubbed")
	}
	if list[1] != 99 {
		t.Error("Int in list was modified")
	}

	// Original not modified
	if data["user_info"].(string) != "Patient John, SSN 123-45-6789, MRN: 12345678" {
		t.Error("Original data was modified")
	}
}

func TestScrubMapString(t *testing.T) {
	m := map[string]string{
		"hostname": "DC01",
		"message":  "SSN 123-45-6789 found in log",
		"ip":       "192.168.1.1",
	}

	scrubbed := ScrubMapString(m)

	if scrubbed["hostname"] != "DC01" {
		t.Errorf("Normal hostname scrubbed: %v", scrubbed["hostname"])
	}
	if strings.Contains(scrubbed["message"], "123-45-6789") {
		t.Error("SSN not scrubbed in string map")
	}
	if scrubbed["ip"] != "192.168.1.1" {
		t.Errorf("IP was scrubbed: %v", scrubbed["ip"])
	}
	// Original not modified
	if m["message"] != "SSN 123-45-6789 found in log" {
		t.Error("Original map was modified")
	}
}

func TestScrub_HashDeterministic(t *testing.T) {
	r1 := Scrub("SSN 123-45-6789")
	r2 := Scrub("SSN 123-45-6789")
	if r1 != r2 {
		t.Errorf("Non-deterministic scrubbing: %q vs %q", r1, r2)
	}

	r3 := Scrub("SSN 999-88-7777")
	if r1 == r3 {
		t.Error("Different SSNs produced same hash")
	}
}

func TestContainsPHI_Positive(t *testing.T) {
	positives := []string{
		"SSN 123-45-6789",
		"patient@hospital.com",
		"MRN: 12345678",
		"Host PATIENT-ROOM-201",
		"File at /patient/john/records",
	}
	for _, input := range positives {
		if !ContainsPHI(input) {
			t.Errorf("ContainsPHI should be true for %q", input)
		}
	}
}

func TestContainsPHI_Negative(t *testing.T) {
	negatives := []string{
		"Server 192.168.1.1 is healthy",
		"firewall_status drift detected",
		"DC01 is reachable on port 5985",
		"NixOS rebuild completed",
	}
	for _, input := range negatives {
		if ContainsPHI(input) {
			t.Errorf("ContainsPHI should be false for %q", input)
		}
	}
}

func TestPatternCount(t *testing.T) {
	// 12 core + hostname + filepath = 14
	if PatternCount() != 14 {
		t.Errorf("Expected 14 patterns, got %d", PatternCount())
	}
}

func TestScrub_Phone(t *testing.T) {
	tests := []string{
		"Call (555) 123-4567",
		"Phone: 555-123-4567",
		"Cell 555.123.4567",
	}

	for _, input := range tests {
		result := Scrub(input)
		if !strings.Contains(result, "[PHONE-REDACTED-") {
			t.Errorf("Phone not scrubbed in %q -> %q", input, result)
		}
	}
}

func TestScrub_CreditCard(t *testing.T) {
	tests := []string{
		"Card: 4111-1111-1111-1111",
		"CC 4111 1111 1111 1111",
	}

	for _, input := range tests {
		result := Scrub(input)
		if !strings.Contains(result, "[CC-REDACTED-") {
			t.Errorf("CC not scrubbed in %q -> %q", input, result)
		}
	}
}

func TestScrub_DOB(t *testing.T) {
	result := Scrub("DOB: 01/15/1990")
	if strings.Contains(result, "01/15/1990") {
		t.Error("DOB not scrubbed")
	}
	if !strings.Contains(result, "[DOB-REDACTED-") {
		t.Error("Missing DOB redaction tag")
	}
}

func TestScrub_Address(t *testing.T) {
	result := Scrub("Lives at 123 Main Street")
	if strings.Contains(result, "123 Main Street") {
		t.Error("Address not scrubbed")
	}
	if !strings.Contains(result, "[ADDRESS-REDACTED-") {
		t.Error("Missing address redaction tag")
	}
}

func TestScrub_AccountNumber(t *testing.T) {
	result := Scrub("Account #123456789")
	if !strings.Contains(result, "[ACCOUNT-REDACTED-") {
		t.Errorf("Account not scrubbed: %q", result)
	}
}

func TestScrub_InsuranceID(t *testing.T) {
	result := Scrub("Insurance ID: XYZ-123-456")
	if !strings.Contains(result, "[INSURANCE-REDACTED-") {
		t.Errorf("Insurance ID not scrubbed: %q", result)
	}
}

func TestScrub_Medicare(t *testing.T) {
	result := Scrub("Medicare: 1EG4-TE5-MK72")
	if strings.Contains(result, "1EG4-TE5-MK72") {
		t.Error("Medicare not scrubbed")
	}
	if !strings.Contains(result, "[MEDICARE-REDACTED-") {
		t.Error("Missing Medicare redaction tag")
	}
}

func BenchmarkScrub(b *testing.B) {
	text := "Server 192.168.1.100 reports SSN 123-45-6789 and email admin@hospital.com with MRN: 12345678"
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		Scrub(text)
	}
}

func BenchmarkScrub_NoMatch(b *testing.B) {
	text := "firewall_status drift_detected=true on DC01 at 192.168.88.250"
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		Scrub(text)
	}
}
