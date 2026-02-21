package l2planner

import (
	"regexp"
	"strings"
)

// Guardrails validates L2 LLM decisions before execution.
// Blocks dangerous commands and enforces an allowed-actions allowlist.
type Guardrails struct {
	dangerousPatterns []*regexp.Regexp
	allowedActions    map[string]bool
}

// DefaultAllowedActions is the set of actions the L2 planner can auto-execute.
var DefaultAllowedActions = []string{
	"restart_service",
	"enable_service",
	"configure_firewall",
	"apply_gpo",
	"enable_bitlocker",
	"fix_audit_policy",
	"apply_ssh_hardening",
	"fix_ntp",
	"fix_permissions",
	"enable_defender",
	"fix_password_policy",
	"escalate",
}

// dangerousPatternDefs are regex patterns that indicate destructive commands.
var dangerousPatternDefs = []string{
	// Filesystem destruction
	`rm\s+(-[a-zA-Z]*)?r[a-zA-Z]*f\s+/`,    // rm -rf /
	`rm\s+(-[a-zA-Z]*)?f[a-zA-Z]*r\s+/`,    // rm -fr /
	`\bmkfs\b`,                               // format filesystem
	`\bfdisk\b`,                              // partition editor
	`\bdd\s+if=/dev/zero\b`,                  // zero out disk
	`\bdd\s+if=/dev/urandom\b`,              // random overwrite

	// Permission destruction
	`chmod\s+777\s+/`,                         // world-writable root
	`chmod\s+(-[a-zA-Z]*)?R\s+777\b`,        // recursive world-writable

	// Remote code execution via pipe
	`curl\s+.*\|\s*(?:ba)?sh`,               // curl | bash
	`wget\s+.*\|\s*(?:ba)?sh`,              // wget | sh
	`curl\s+.*\|\s*python`,                  // curl | python
	`wget\s+.*\|\s*python`,                 // wget | python

	// SQL destruction
	`(?i)\bDROP\s+(?:TABLE|DATABASE)\b`,     // DROP TABLE/DATABASE
	`(?i)\bDELETE\s+FROM\b`,                // DELETE FROM
	`(?i)\bTRUNCATE\b`,                      // TRUNCATE

	// Credential files
	`/etc/shadow`,                            // shadow password file
	`\bid_rsa\b`,                             // SSH private key
	`(?i)\bapi[_\s]?key\b`,                  // API key references
	`\.env\b`,                                // env files with secrets

	// Reverse shells
	`\bnc\s+.*-[a-zA-Z]*e\s+/bin/`,          // netcat reverse shell
	`\bncat\s+.*-[a-zA-Z]*e\s+/bin/`,       // ncat reverse shell
	`/dev/tcp/`,                              // bash reverse shell

	// System destruction
	`\b(?:shutdown|reboot|halt|poweroff)\b.*-[a-zA-Z]*f\b`,  // forced shutdown
	`>\s*/dev/sda`,                           // overwrite disk device

	// Windows destruction
	`(?i)Format-Volume`,                      // PowerShell format disk
	`(?i)Clear-Disk`,                         // PowerShell clear disk
	`(?i)Remove-Partition`,                   // PowerShell remove partition
	`(?i)Stop-Computer\s+-Force`,            // forced shutdown
}

// NewGuardrails creates a Guardrails checker with the given allowed actions.
// If allowedActions is nil, DefaultAllowedActions is used.
func NewGuardrails(allowedActions []string) *Guardrails {
	if allowedActions == nil {
		allowedActions = DefaultAllowedActions
	}

	allowed := make(map[string]bool, len(allowedActions))
	for _, a := range allowedActions {
		allowed[strings.ToLower(a)] = true
	}

	patterns := make([]*regexp.Regexp, 0, len(dangerousPatternDefs))
	for _, p := range dangerousPatternDefs {
		patterns = append(patterns, regexp.MustCompile(p))
	}

	return &Guardrails{
		dangerousPatterns: patterns,
		allowedActions:    allowed,
	}
}

// CheckResult is the result of a guardrails check.
type CheckResult struct {
	Allowed  bool
	Reason   string
	Category string // "dangerous_pattern", "unknown_action", "low_confidence", ""
}

// Check validates an L2 decision. Returns a CheckResult indicating whether
// the decision should be executed or escalated.
func (g *Guardrails) Check(action string, script string, confidence float64) CheckResult {
	// Check confidence threshold
	if confidence < 0.6 {
		return CheckResult{
			Allowed:  false,
			Reason:   "confidence too low for auto-execution",
			Category: "low_confidence",
		}
	}

	// Check action is in allowlist
	if !g.IsActionAllowed(action) {
		return CheckResult{
			Allowed:  false,
			Reason:   "action not in allowed list: " + action,
			Category: "unknown_action",
		}
	}

	// Check script for dangerous patterns
	if reason := g.CheckDangerous(script); reason != "" {
		return CheckResult{
			Allowed:  false,
			Reason:   reason,
			Category: "dangerous_pattern",
		}
	}

	// Also check action string itself for dangerous patterns
	if reason := g.CheckDangerous(action); reason != "" {
		return CheckResult{
			Allowed:  false,
			Reason:   reason,
			Category: "dangerous_pattern",
		}
	}

	return CheckResult{Allowed: true}
}

// IsActionAllowed checks if an action is in the allowlist.
func (g *Guardrails) IsActionAllowed(action string) bool {
	return g.allowedActions[strings.ToLower(action)]
}

// CheckDangerous scans a string for dangerous patterns.
// Returns the reason string if dangerous, empty string if safe.
func (g *Guardrails) CheckDangerous(input string) string {
	for _, p := range g.dangerousPatterns {
		if p.MatchString(input) {
			return "dangerous pattern detected: " + p.String()
		}
	}
	return ""
}

// AllowedActions returns the list of allowed actions.
func (g *Guardrails) AllowedActions() []string {
	actions := make([]string, 0, len(g.allowedActions))
	for a := range g.allowedActions {
		actions = append(actions, a)
	}
	return actions
}
