package l2planner

import (
	"strings"
	"testing"
)

func TestScrubSSN(t *testing.T) {
	s := NewPHIScrubber()

	tests := []struct {
		input    string
		contains string // should NOT be in output
	}{
		{"SSN is 123-45-6789", "123-45-6789"},
		{"Patient SSN: 999 88 7777", "999 88 7777"},
	}

	for _, tt := range tests {
		result := s.ScrubString(tt.input)
		if strings.Contains(result, tt.contains) {
			t.Errorf("SSN not scrubbed: %q still in %q", tt.contains, result)
		}
		if !strings.Contains(result, "[SSN-REDACTED-") {
			t.Errorf("Missing SSN redaction tag in %q", result)
		}
	}
}

func TestScrubMRN(t *testing.T) {
	s := NewPHIScrubber()

	tests := []string{
		"MRN: 12345678",
		"mrn#99887766",
		"MRN 5555",
	}

	for _, input := range tests {
		result := s.ScrubString(input)
		if !strings.Contains(result, "[MRN-REDACTED-") {
			t.Errorf("MRN not scrubbed in %q → %q", input, result)
		}
	}
}

func TestScrubPhone(t *testing.T) {
	s := NewPHIScrubber()

	tests := []string{
		"Call (555) 123-4567",
		"Phone: 555-123-4567",
		"Cell 555.123.4567",
	}

	for _, input := range tests {
		result := s.ScrubString(input)
		if !strings.Contains(result, "[PHONE-REDACTED-") {
			t.Errorf("Phone not scrubbed in %q → %q", input, result)
		}
	}
}

func TestScrubEmail(t *testing.T) {
	s := NewPHIScrubber()
	result := s.ScrubString("Contact admin@hospital.com for records")
	if strings.Contains(result, "admin@hospital.com") {
		t.Error("Email not scrubbed")
	}
	if !strings.Contains(result, "[EMAIL-REDACTED-") {
		t.Error("Missing email redaction tag")
	}
}

func TestScrubCreditCard(t *testing.T) {
	s := NewPHIScrubber()

	tests := []string{
		"Card: 4111-1111-1111-1111",
		"CC 4111 1111 1111 1111",
	}

	for _, input := range tests {
		result := s.ScrubString(input)
		if !strings.Contains(result, "[CC-REDACTED-") {
			t.Errorf("CC not scrubbed in %q → %q", input, result)
		}
	}
}

func TestScrubDOB(t *testing.T) {
	s := NewPHIScrubber()
	result := s.ScrubString("DOB: 01/15/1990")
	if strings.Contains(result, "01/15/1990") {
		t.Error("DOB not scrubbed")
	}
	if !strings.Contains(result, "[DOB-REDACTED-") {
		t.Error("Missing DOB redaction tag")
	}
}

func TestScrubAddress(t *testing.T) {
	s := NewPHIScrubber()
	result := s.ScrubString("Lives at 123 Main Street")
	if strings.Contains(result, "123 Main Street") {
		t.Error("Address not scrubbed")
	}
	if !strings.Contains(result, "[ADDRESS-REDACTED-") {
		t.Error("Missing address redaction tag")
	}
}

func TestScrubZipPlus4(t *testing.T) {
	s := NewPHIScrubber()
	result := s.ScrubString("ZIP: 18501-1234")
	if strings.Contains(result, "18501-1234") {
		t.Error("ZIP+4 not scrubbed")
	}
	if !strings.Contains(result, "[ZIP-REDACTED-") {
		t.Error("Missing ZIP redaction tag")
	}
}

func TestScrubAccountNumber(t *testing.T) {
	s := NewPHIScrubber()
	result := s.ScrubString("Account #123456789")
	if !strings.Contains(result, "[ACCOUNT-REDACTED-") {
		t.Errorf("Account not scrubbed: %q", result)
	}
}

func TestScrubInsuranceID(t *testing.T) {
	s := NewPHIScrubber()
	result := s.ScrubString("Insurance ID: XYZ-123-456")
	if !strings.Contains(result, "[INSURANCE-REDACTED-") {
		t.Errorf("Insurance ID not scrubbed: %q", result)
	}
}

func TestScrubMedicare(t *testing.T) {
	s := NewPHIScrubber()
	result := s.ScrubString("Medicare: 1EG4-TE5-MK72")
	if strings.Contains(result, "1EG4-TE5-MK72") {
		t.Error("Medicare not scrubbed")
	}
	if !strings.Contains(result, "[MEDICARE-REDACTED-") {
		t.Error("Missing Medicare redaction tag")
	}
}

func TestIPAddressesPreserved(t *testing.T) {
	s := NewPHIScrubber()

	input := "Server at 192.168.1.100 has SSN 123-45-6789 and IP 10.0.0.1"
	result := s.ScrubString(input)

	// IPs must survive
	if !strings.Contains(result, "192.168.1.100") {
		t.Errorf("IP 192.168.1.100 was scrubbed: %q", result)
	}
	if !strings.Contains(result, "10.0.0.1") {
		t.Errorf("IP 10.0.0.1 was scrubbed: %q", result)
	}

	// SSN must be scrubbed
	if strings.Contains(result, "123-45-6789") {
		t.Error("SSN was NOT scrubbed alongside IPs")
	}

	// Verify helper
	if !s.VerifyIPsPreserved(input) {
		t.Error("VerifyIPsPreserved returned false")
	}
}

func TestScrubMap(t *testing.T) {
	s := NewPHIScrubber()

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

	scrubbed := s.ScrubMap(data)

	// IP preserved
	if scrubbed["ip_address"] != "192.168.88.100" {
		t.Errorf("IP was scrubbed: %v", scrubbed["ip_address"])
	}

	// Hostname preserved
	if scrubbed["hostname"] != "DC01" {
		t.Error("Hostname was scrubbed")
	}

	// SSN and MRN scrubbed
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

func TestHashSuffixDeterministic(t *testing.T) {
	s := NewPHIScrubber()

	// Same input → same hash
	r1 := s.ScrubString("SSN 123-45-6789")
	r2 := s.ScrubString("SSN 123-45-6789")
	if r1 != r2 {
		t.Errorf("Non-deterministic scrubbing: %q vs %q", r1, r2)
	}

	// Different input → different hash
	r3 := s.ScrubString("SSN 999-88-7777")
	if r1 == r3 {
		t.Error("Different SSNs produced same hash")
	}
}

func TestContainsPHI(t *testing.T) {
	s := NewPHIScrubber()

	if !s.ContainsPHI("SSN 123-45-6789") {
		t.Error("Should detect SSN")
	}
	if !s.ContainsPHI("patient@hospital.com") {
		t.Error("Should detect email")
	}
	if s.ContainsPHI("Server 192.168.1.1 is healthy") {
		t.Error("IP should not flag as PHI")
	}
	if s.ContainsPHI("firewall_status drift detected") {
		t.Error("Plain text should not flag as PHI")
	}
}

func TestScrubReport(t *testing.T) {
	s := NewPHIScrubber()

	cats := s.ScrubReport("SSN 123-45-6789, email patient@hospital.com")
	if len(cats) < 2 {
		t.Errorf("Expected >=2 categories, got %d: %v", len(cats), cats)
	}

	found := map[string]bool{}
	for _, c := range cats {
		found[c] = true
	}
	if !found["ssn"] {
		t.Error("Missing ssn category")
	}
	if !found["email"] {
		t.Error("Missing email category")
	}
}

func TestNoFalsePositivesOnInfraData(t *testing.T) {
	s := NewPHIScrubber()

	infraStrings := []string{
		"firewall_status drift_detected=true",
		"Windows Defender is disabled",
		"Service wuauserv is stopped",
		"Port 5985 open on DC01",
		"NixOS rebuild completed in 45s",
		"Check linux_ssh_config failed",
		"HIPAA control 164.312(a)(1)",
	}

	for _, input := range infraStrings {
		result := s.ScrubString(input)
		if result != input {
			t.Errorf("False positive scrubbing on infra data: %q → %q", input, result)
		}
	}
}

func TestString(t *testing.T) {
	s := NewPHIScrubber()
	str := s.String()
	if !strings.Contains(str, "12 patterns") {
		t.Errorf("Unexpected String(): %q", str)
	}
}
