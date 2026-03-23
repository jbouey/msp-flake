// Package phiscrub provides HIPAA-compliant PHI/PII scrubbing for all data
// egress paths. This is the single source of truth for scrubbing patterns —
// every outbound path (incidents, evidence, logs, checkin, discovery) MUST
// route through this package before data leaves the appliance.
//
// Compliant with HIPAA 164.312(e)(1) — transmission security.
//
// IP addresses are intentionally preserved: they are infrastructure identifiers
// per HIPAA Safe Harbor 45 CFR 164.514(b)(2), needed for network topology.
package phiscrub

import (
	"crypto/sha256"
	"fmt"
	"regexp"
	"strings"
)

// pattern holds a compiled regex and its redaction tag.
type pattern struct {
	category string
	re       *regexp.Regexp
	tag      string
}

// Global singleton — patterns are compiled once at init and are read-only.
var globalPatterns []pattern

// patientHostnameRe matches hostnames containing patient/clinical identifiers.
var patientHostnameRe *regexp.Regexp

// phiPathSegmentRe matches file path segments containing PHI-related directories.
var phiPathSegmentRe *regexp.Regexp

func init() {
	globalPatterns = compilePatterns()
	patientHostnameRe = regexp.MustCompile(`(?i)\b(?:PATIENT|ROOM|BED|WARD|DR\.|MR\.|MS\.)[A-Za-z0-9_\-]*\b`)
	phiPathSegmentRe = regexp.MustCompile(`(?i)(?:/(?:patient|ehr|medical)/)[^\s/]*`)
}

func compilePatterns() []pattern {
	defs := []struct {
		category string
		expr     string
		tag      string
	}{
		// 1. SSN: 123-45-6789 or 123 45 6789
		{"ssn", `\b\d{3}[-\s]\d{2}[-\s]\d{4}\b`, "SSN-REDACTED"},

		// 2. MRN: MRN followed by digits (various separators)
		{"mrn", `(?i)\bMRN[:\s#]*\d{4,12}\b`, "MRN-REDACTED"},

		// 3. Patient ID: patient_id or patient id followed by alphanumeric
		{"patient_id", `(?i)\bpatient[_\s]?id[:\s#]*[A-Za-z0-9\-]{3,20}\b`, "PATIENT-ID-REDACTED"},

		// 4. Phone: (555) 123-4567, 555-123-4567, 555.123.4567
		{"phone", `(?:\(\d{3}\)\s*\d{3}[-.]?\d{4}|\b\d{3}[-.]?\d{3}[-.]?\d{4}\b)`, "PHONE-REDACTED"},

		// 5. Email
		{"email", `\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b`, "EMAIL-REDACTED"},

		// 6. Credit card: 4111-1111-1111-1111 or spaces or no separator
		{"credit_card", `\b(?:\d{4}[-\s]?){3}\d{4}\b`, "CC-REDACTED"},

		// 7. DOB: DOB/Date of Birth followed by date patterns
		{"dob", `(?i)\b(?:DOB|date\s*of\s*birth)[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b`, "DOB-REDACTED"},

		// 8. Street address: number + street name + suffix
		{"address", `\b\d{1,6}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Court|Ct|Way|Place|Pl|Circle|Cir)\b`, "ADDRESS-REDACTED"},

		// 9. ZIP+4: 18501-1234 (not plain 5-digit which could be ports/counts)
		{"zip", `\b\d{5}-\d{4}\b`, "ZIP-REDACTED"},

		// 10. Account number: Account/Acct # followed by digits
		{"account_number", `(?i)\b(?:account|acct)[:\s#]*\d{4,20}\b`, "ACCOUNT-REDACTED"},

		// 11. Insurance ID
		{"insurance_id", `(?i)\b(?:insurance|policy)\s*(?:id|#|number)[:\s]*[A-Za-z0-9\-]{4,20}\b`, "INSURANCE-REDACTED"},

		// 12. Medicare: Medicare ID format (1EG4-TE5-MK72 or similar)
		{"medicare", `(?i)\bmedicare[:\s#]*[A-Za-z0-9]{4}[-\s]?[A-Za-z0-9]{3}[-\s]?[A-Za-z0-9]{4}\b`, "MEDICARE-REDACTED"},
	}

	out := make([]pattern, 0, len(defs))
	for _, d := range defs {
		out = append(out, pattern{
			category: d.category,
			re:       regexp.MustCompile(d.expr),
			tag:      d.tag,
		})
	}
	return out
}

// hashSuffix returns the first 8 hex chars of the SHA-256 hash for correlation.
func hashSuffix(value string) string {
	h := sha256.Sum256([]byte(value))
	return fmt.Sprintf("%x", h[:4])
}

// Scrub removes potential PHI patterns from text.
// Replaces SSNs, MRNs, patient IDs, phone numbers, emails, DOBs, addresses,
// account numbers, insurance IDs, credit cards, ZIP+4, and Medicare IDs
// with tagged [REDACTED] placeholders that include a hash suffix for correlation.
//
// Also scrubs:
//   - Patient name patterns in hostnames (PATIENT, ROOM, BED, WARD, DR., MR., MS.)
//   - File paths containing patient identifiers (/patient/, /ehr/, /medical/)
func Scrub(text string) string {
	result := text

	// Apply the 12 core PHI patterns
	for _, p := range globalPatterns {
		result = p.re.ReplaceAllStringFunc(result, func(match string) string {
			return fmt.Sprintf("[%s-%s]", p.tag, hashSuffix(match))
		})
	}

	// Scrub PHI file path segments BEFORE hostname patterns, because
	// /patient/ would otherwise match the PATIENT hostname pattern.
	result = phiPathSegmentRe.ReplaceAllStringFunc(result, func(match string) string {
		return fmt.Sprintf("/[PATH-REDACTED-%s]", hashSuffix(match))
	})

	// Scrub patient-identifying hostnames
	result = patientHostnameRe.ReplaceAllStringFunc(result, func(match string) string {
		return fmt.Sprintf("[HOSTNAME-REDACTED-%s]", hashSuffix(match))
	})

	return result
}

// ScrubMap scrubs all string values in a map recursively.
// Returns a new map — the original is not modified.
func ScrubMap(m map[string]interface{}) map[string]interface{} {
	out := make(map[string]interface{}, len(m))
	for k, v := range m {
		out[k] = scrubValue(v)
	}
	return out
}

// ScrubMapString scrubs all string values in a string map.
// Returns a new map — the original is not modified.
func ScrubMapString(m map[string]string) map[string]string {
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = Scrub(v)
	}
	return out
}

func scrubValue(v interface{}) interface{} {
	switch val := v.(type) {
	case string:
		return Scrub(val)
	case map[string]interface{}:
		return ScrubMap(val)
	case []interface{}:
		out := make([]interface{}, len(val))
		for i, item := range val {
			out[i] = scrubValue(item)
		}
		return out
	default:
		return v
	}
}

// ContainsPHI returns true if the input string matches any PHI pattern.
func ContainsPHI(input string) bool {
	for _, p := range globalPatterns {
		if p.re.MatchString(input) {
			return true
		}
	}
	if patientHostnameRe.MatchString(input) {
		return true
	}
	if phiPathSegmentRe.MatchString(input) {
		return true
	}
	return false
}

// ScrubReport returns a list of core PHI categories found in the input.
// Only reports the 12 core categories (not hostname/path patterns).
func ScrubReport(input string) []string {
	var found []string
	for _, p := range globalPatterns {
		if p.re.MatchString(input) {
			found = append(found, p.category)
		}
	}
	return found
}

// PatternCount returns the number of active scrubbing patterns (for diagnostics).
func PatternCount() int {
	// 12 core + hostname + path = 14
	return len(globalPatterns) + 2
}

// IPPattern is exposed for testing — confirms IPs are NOT scrubbed.
var IPPattern = regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`)

// VerifyIPsPreserved checks that IP addresses survive scrubbing unchanged.
func VerifyIPsPreserved(input string) bool {
	scrubbed := Scrub(input)
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
func String() string {
	cats := make([]string, len(globalPatterns))
	for i, p := range globalPatterns {
		cats[i] = p.category
	}
	return fmt.Sprintf("PHIScrubber(%d patterns: %s, +hostname, +filepath)", len(globalPatterns), strings.Join(cats, ", "))
}
