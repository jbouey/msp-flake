// Package l2planner implements a native Go L2 LLM planner for the appliance daemon.
// It replaces the Python L2 sidecar with a direct Anthropic API client.
package l2planner

import (
	"regexp"

	"github.com/osiriscare/appliance/internal/phiscrub"
)

// PHIScrubber strips PHI/PII from data before it's sent to cloud APIs.
// Compliant with HIPAA §164.312(e)(1) — transmission security.
//
// This is now a thin wrapper around the shared phiscrub package, which is
// the single source of truth for all scrubbing patterns across every egress path.
//
// IP addresses are intentionally excluded: they are infrastructure identifiers
// per HIPAA Safe Harbor 45 CFR 164.514(b)(2), needed for LLM to understand
// network topology when generating remediation plans.
type PHIScrubber struct{}

// NewPHIScrubber creates a scrubber. Patterns are compiled once at init in the
// shared phiscrub package.
func NewPHIScrubber() *PHIScrubber {
	return &PHIScrubber{}
}

// ScrubString replaces all PHI matches in a string with tagged placeholders.
// Each replacement includes a hash suffix for correlation: [SSN-REDACTED-a1b2c3d4]
func (s *PHIScrubber) ScrubString(input string) string {
	return phiscrub.Scrub(input)
}

// ScrubMap recursively scrubs all string values in a map.
// Returns a new map — the original is not modified.
func (s *PHIScrubber) ScrubMap(data map[string]interface{}) map[string]interface{} {
	return phiscrub.ScrubMap(data)
}

// ContainsPHI returns true if the input string contains any PHI patterns.
func (s *PHIScrubber) ContainsPHI(input string) bool {
	return phiscrub.ContainsPHI(input)
}

// ScrubReport returns a list of categories found in the input.
// NOTE: This only reports the 12 core categories (not hostname/path patterns)
// for backward compatibility with existing L2 telemetry.
func (s *PHIScrubber) ScrubReport(input string) []string {
	// Delegate to shared package — ContainsPHI now includes hostname/path,
	// but ScrubReport keeps backward compat by only reporting core categories.
	// The actual scrubbing still catches everything via Scrub().
	return phiscrub.ScrubReport(input)
}

// IPPattern is exposed for testing — confirms IPs are NOT scrubbed.
var IPPattern = regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`)

// VerifyIPsPreserved checks that IP addresses survive scrubbing unchanged.
func (s *PHIScrubber) VerifyIPsPreserved(input string) bool {
	return phiscrub.VerifyIPsPreserved(input)
}

// String returns a summary of the scrubber configuration.
func (s *PHIScrubber) String() string {
	return phiscrub.String()
}
