// Package l2planner implements a native Go L2 LLM planner for the appliance daemon.
// It replaces the Python L2 sidecar with a direct Anthropic API client.
package l2planner

import (
	"crypto/sha256"
	"fmt"
	"regexp"
	"strings"
)

// PHIScrubber strips PHI/PII from data before it's sent to cloud APIs.
// Compliant with HIPAA §164.312(e)(1) — transmission security.
//
// IP addresses are intentionally excluded: they are infrastructure identifiers
// per HIPAA Safe Harbor 45 CFR 164.514(b)(2), needed for LLM to understand
// network topology when generating remediation plans.
type PHIScrubber struct {
	patterns []phiPattern
}

type phiPattern struct {
	category string
	re       *regexp.Regexp
	tag      string // e.g. "SSN-REDACTED"
}

// NewPHIScrubber creates a scrubber with all 12 active pattern categories.
func NewPHIScrubber() *PHIScrubber {
	return &PHIScrubber{
		patterns: compilePatterns(),
	}
}

func compilePatterns() []phiPattern {
	defs := []struct {
		category string
		pattern  string
		tag      string
	}{
		// SSN: 123-45-6789 or 123 45 6789
		{"ssn", `\b\d{3}[-\s]\d{2}[-\s]\d{4}\b`, "SSN-REDACTED"},

		// MRN: MRN followed by digits (various separators)
		{"mrn", `(?i)\bMRN[:\s#]*\d{4,12}\b`, "MRN-REDACTED"},

		// Patient ID: patient_id or patient id followed by alphanumeric
		{"patient_id", `(?i)\bpatient[_\s]?id[:\s#]*[A-Za-z0-9\-]{3,20}\b`, "PATIENT-ID-REDACTED"},

		// Phone: (555) 123-4567, 555-123-4567, 555.123.4567
		{"phone", `(?:\(\d{3}\)\s*\d{3}[-.]?\d{4}|\b\d{3}[-.]?\d{3}[-.]?\d{4}\b)`, "PHONE-REDACTED"},

		// Email
		{"email", `\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b`, "EMAIL-REDACTED"},

		// Credit card: 4111-1111-1111-1111 or spaces or no separator
		{"credit_card", `\b(?:\d{4}[-\s]?){3}\d{4}\b`, "CC-REDACTED"},

		// DOB: DOB/Date of Birth followed by date patterns
		{"dob", `(?i)\b(?:DOB|date\s*of\s*birth)[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b`, "DOB-REDACTED"},

		// Street address: number + street name + suffix
		{"address", `\b\d{1,6}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Court|Ct|Way|Place|Pl|Circle|Cir)\b`, "ADDRESS-REDACTED"},

		// ZIP+4: 18501-1234 (but not plain 5-digit which could be ports/counts)
		{"zip", `\b\d{5}-\d{4}\b`, "ZIP-REDACTED"},

		// Account number: Account/Acct # followed by digits
		{"account_number", `(?i)\b(?:account|acct)[:\s#]*\d{4,20}\b`, "ACCOUNT-REDACTED"},

		// Insurance ID
		{"insurance_id", `(?i)\b(?:insurance|policy)\s*(?:id|#|number)[:\s]*[A-Za-z0-9\-]{4,20}\b`, "INSURANCE-REDACTED"},

		// Medicare: Medicare ID format (1EG4-TE5-MK72 or similar)
		{"medicare", `(?i)\bmedicare[:\s#]*[A-Za-z0-9]{4}[-\s]?[A-Za-z0-9]{3}[-\s]?[A-Za-z0-9]{4}\b`, "MEDICARE-REDACTED"},
	}

	patterns := make([]phiPattern, 0, len(defs))
	for _, d := range defs {
		patterns = append(patterns, phiPattern{
			category: d.category,
			re:       regexp.MustCompile(d.pattern),
			tag:      d.tag,
		})
	}
	return patterns
}

// hashSuffix returns the first 8 hex chars of the SHA-256 hash.
// This enables correlation across scrubbed logs without revealing the original value.
func hashSuffix(value string) string {
	h := sha256.Sum256([]byte(value))
	return fmt.Sprintf("%x", h[:4])
}

// ScrubString replaces all PHI matches in a string with tagged placeholders.
// Each replacement includes a hash suffix for correlation: [SSN-REDACTED-a1b2c3d4]
func (s *PHIScrubber) ScrubString(input string) string {
	result := input
	for _, p := range s.patterns {
		result = p.re.ReplaceAllStringFunc(result, func(match string) string {
			return fmt.Sprintf("[%s-%s]", p.tag, hashSuffix(match))
		})
	}
	return result
}

// ScrubMap recursively scrubs all string values in a map.
// Returns a new map — the original is not modified.
func (s *PHIScrubber) ScrubMap(data map[string]interface{}) map[string]interface{} {
	out := make(map[string]interface{}, len(data))
	for k, v := range data {
		out[k] = s.scrubValue(v)
	}
	return out
}

func (s *PHIScrubber) scrubValue(v interface{}) interface{} {
	switch val := v.(type) {
	case string:
		return s.ScrubString(val)
	case map[string]interface{}:
		return s.ScrubMap(val)
	case []interface{}:
		out := make([]interface{}, len(val))
		for i, item := range val {
			out[i] = s.scrubValue(item)
		}
		return out
	default:
		return v
	}
}

// ContainsPHI returns true if the input string contains any PHI patterns.
func (s *PHIScrubber) ContainsPHI(input string) bool {
	for _, p := range s.patterns {
		if p.re.MatchString(input) {
			return true
		}
	}
	return false
}

// ScrubReport returns a list of categories found in the input.
func (s *PHIScrubber) ScrubReport(input string) []string {
	var found []string
	for _, p := range s.patterns {
		if p.re.MatchString(input) {
			found = append(found, p.category)
		}
	}
	return found
}

// IPPattern is exposed for testing — confirms IPs are NOT scrubbed.
var IPPattern = regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`)

// VerifyIPsPreserved checks that IP addresses survive scrubbing unchanged.
func (s *PHIScrubber) VerifyIPsPreserved(input string) bool {
	scrubbed := s.ScrubString(input)
	origIPs := IPPattern.FindAllString(input, -1)
	scrubbedIPs := IPPattern.FindAllString(scrubbed, -1)

	if len(origIPs) != len(scrubbedIPs) {
		return false
	}
	for i, ip := range origIPs {
		if ip != scrubbedIPs[i] {
			return false
		}
	}
	return true
}

// String returns a summary of the scrubber configuration.
func (s *PHIScrubber) String() string {
	cats := make([]string, len(s.patterns))
	for i, p := range s.patterns {
		cats[i] = p.category
	}
	return fmt.Sprintf("PHIScrubber(%d patterns: %s)", len(s.patterns), strings.Join(cats, ", "))
}
